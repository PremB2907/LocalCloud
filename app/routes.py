import json
import re
import secrets
import shlex
import time
from datetime import datetime
from io import BytesIO
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from botocore.exceptions import BotoCoreError, ClientError
from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from app import catalog
from app.services import api_gateway, cloudwatch, dynamodb, ec2, lambda_functions, s3, sns, sqs, ssm


console = Blueprint("console", __name__)
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
TABLE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
QUEUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
API_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]{1,128}$")
API_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
HTTP_ROUTE_PATTERN = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD|ANY) /[A-Za-z0-9_./{}+:-]*$")
INSTANCE_ID_PATTERN = re.compile(r"^i-[A-Za-z0-9]+$")
FUNCTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
AMI_ID_PATTERN = re.compile(r"^ami-[A-Za-z0-9]+$")
INSTANCE_TYPE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*\.[A-Za-z0-9]+$")
LOG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_./#-]{1,512}$")
RUNTIME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
HANDLER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
KEY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]{1,255}$")
CWD_MARKER = "__LOCALCLOUD_CWD__"
MAX_COMMAND_LENGTH = 4096
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_LAMBDA_CODE_BYTES = 256 * 1024


@console.before_app_request
def restrict_mutations_to_same_origin():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return jsonify({"error": {"code": "CrossOriginBlocked", "message": "Cross-origin mutations are blocked."}}), 403
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.netloc != request.host or parsed.scheme != request.scheme:
            return jsonify({"error": {"code": "CrossOriginBlocked", "message": "Cross-origin mutations are blocked."}}), 403
    return None


def _client(name):
    return current_app.extensions["aws_clients"].get(name)


def _record_operation(operation, resource, result):
    records = current_app.extensions["recent_operations"]
    records.insert(0, {
        "operation": operation,
        "resource": resource,
        "result": result,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    del records[100:]


def _body():
    return request.get_json(silent=True) or {}


def _safe_queue_url(queue_url):
    try:
        parsed = urlsplit(queue_url or "")
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port == 4566
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _safe_topic_arn(topic_arn):
    try:
        arn = topic_arn.split(":", 5)
        config = current_app.extensions["floci_config"]
        return (
            len(arn) == 6
            and arn[0] == "arn"
            and arn[1] == "aws"
            and arn[2] == "sns"
            and arn[3] == config.region
            and arn[4] == "000000000000"
            and bool(arn[5])
        )
    except (AttributeError, ValueError):
        return False


def _operation_error(error):
    if isinstance(error, (ValueError, LookupError)):
        code = "ValidationError" if isinstance(error, ValueError) else "ResourceNotFound"
        status = 400 if isinstance(error, ValueError) else 404
        return jsonify({"ok": False, "error": {"code": code, "message": str(error)}}), status
    detail, status = _safe_error(error)
    return jsonify({"ok": False, "error": detail}), status


def _confirmation(payload, expected):
    return payload.get("confirmed") is True and payload.get("confirm_value") == expected


def _safe_error(error):
    if isinstance(error, ClientError):
        detail = error.response.get("Error", {})
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 502)
        return {
            "code": str(detail.get("Code", "ServiceError")),
            "message": "Floci rejected or could not complete this request.",
        }, status
    if isinstance(error, BotoCoreError):
        return {
            "code": type(error).__name__,
            "message": "Floci did not respond to this operation in time.",
        }, 504
    current_app.logger.exception("Unhandled dashboard service error")
    return {"code": "InternalError", "message": "The dashboard could not complete this request."}, 500


def _load_service(service_name):
    clients = current_app.extensions["aws_clients"]
    if service_name == "s3":
        return {"buckets": s3.list_buckets(clients.get("s3"))}
    if service_name == "dynamodb":
        owned = current_app.extensions["console_created_tables"]
        return {"tables": [{**table, "console_owned": table["name"] in owned} for table in dynamodb.list_tables(clients.get("dynamodb"))]}
    if service_name == "sqs":
        owned = current_app.extensions["console_created_queues"]
        return {"queues": [{**queue, "console_owned": queue["url"] in owned} for queue in sqs.list_queues(clients.get("sqs"))]}
    if service_name == "sns":
        owned = current_app.extensions["console_created_topics"]
        return {"topics": [{**topic, "console_owned": topic["arn"] in owned} for topic in sns.list_topics(clients.get("sns"))]}
    if service_name == "lambda":
        return {"functions": lambda_functions.list_functions(clients.get("lambda"))}
    if service_name == "ec2":
        ec2_client = clients.get("ec2")
        owned_keys = current_app.extensions["console_created_key_pairs"]
        try:
            key_pairs = {"ok": True, "key_pairs": [
                {**key, "console_owned": key["name"] in owned_keys} for key in ec2.list_key_pairs(ec2_client)
            ]}
        except (ClientError, BotoCoreError) as error:
            detail, status = _safe_error(error)
            key_pairs = {"ok": False, "error": detail, "http_status": status, "key_pairs": []}
        return {"instances": ec2.list_instances(ec2_client), "key_pairs": key_pairs}
    if service_name == "cloudwatch":
        data = {}
        for key, service_client, loader in (
            ("metrics", "cloudwatch", cloudwatch.list_metrics),
            ("log_groups", "logs", cloudwatch.list_log_groups),
        ):
            try:
                data[key] = {"ok": True, **loader(clients.get(service_client))}
            except (ClientError, BotoCoreError) as error:
                detail, status = _safe_error(error)
                data[key] = {"ok": False, "error": detail, "http_status": status}
        if data["log_groups"]["ok"]:
            owned = current_app.extensions["console_created_log_groups"]
            data["log_groups"]["log_groups"] = [
                {**group, "console_owned": group["name"] in owned}
                for group in data["log_groups"]["log_groups"]
            ]
        return data
    if service_name == "api_gateway":
        data = {}
        owned = current_app.extensions["console_created_apis"]
        for key, service_client, loader in (
            ("rest", "apigateway", api_gateway.list_rest_apis),
            ("http", "apigatewayv2", api_gateway.list_http_apis),
        ):
            try:
                result = loader(clients.get(service_client))
                result["apis"] = [{**api, "console_owned": api["id"] in owned} for api in result["apis"]]
                data[key] = {"ok": True, **result}
            except (ClientError, BotoCoreError) as error:
                detail, status = _safe_error(error)
                data[key] = {"ok": False, "error": detail, "http_status": status}
        return data
    raise KeyError(service_name)


@console.get("/")
def index():
    return render_template("index.html")


@console.get("/api/health")
def health():
    config = current_app.extensions["floci_config"]
    try:
        with urlopen(f"{config.endpoint_url}/_floci/health", timeout=2) as response:
            payload = json.loads(response.read())
            return jsonify({
                "connected": True,
                "version": payload.get("version"),
                "endpoint": config.endpoint_url,
                "http_status": response.status,
            })
    except (OSError, URLError, TimeoutError, ValueError):
        return jsonify({
            "connected": False,
            "endpoint": config.endpoint_url,
            "message": "Floci is not reachable at the configured local endpoint.",
        }), 503


@console.get("/api/catalog")
def get_catalog():
    return jsonify(catalog.SERVICE_CATALOG)


@console.get("/api/services/<service_name>")
def get_service(service_name):
    metadata = catalog.SERVICE_CATALOG.get(service_name)
    if metadata is None:
        return jsonify({"error": {"code": "UnknownService", "message": "No dashboard page exists for this service."}}), 404
    try:
        data = _load_service(service_name)
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"service": metadata, "ok": False, "error": detail}), status
    return jsonify({"service": metadata, "ok": True, "data": data})


@console.get("/api/operations")
def operations():
    return jsonify(current_app.extensions["recent_operations"])


@console.get("/api/services/s3/buckets/<bucket_name>/objects")
def get_s3_objects(bucket_name):
    try:
        result = s3.list_objects(_client("s3"), bucket_name, request.args.get("continuation_token"))
        return jsonify({"ok": True, **result})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.get("/api/services/s3/buckets/<bucket_name>/objects/<path:key>/metadata")
def get_s3_object_metadata(bucket_name, key):
    try:
        result = s3.object_metadata(_client("s3"), bucket_name, key)
        return jsonify({"ok": True, "metadata": result})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.get("/api/services/cloudwatch/streams")
def get_cloudwatch_streams():
    group_name = request.args.get("group", "")
    if not group_name:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A log group name is required."}}), 400
    try:
        streams = cloudwatch.list_log_streams(_client("logs"), group_name)
        return jsonify({"ok": True, "streams": streams})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.get("/api/services/cloudwatch/events")
def get_cloudwatch_events():
    group_name = request.args.get("group", "")
    stream_name = request.args.get("stream", "")
    if not group_name or not stream_name:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A log group and stream name are required."}}), 400
    try:
        events = cloudwatch.get_log_events(_client("logs"), group_name, stream_name)
        return jsonify({"ok": True, **events})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.post("/api/services/cloudwatch/log-groups")
def post_cloudwatch_log_group():
    group_name = _body().get("group_name", "")
    if not isinstance(group_name, str) or not LOG_NAME_PATTERN.fullmatch(group_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid log group name (up to 512 letters, numbers, slash, dot, underscore, hash, or hyphen characters)."}}), 400
    try:
        response = cloudwatch.create_log_group(_client("logs"), group_name)
        current_app.extensions["console_created_log_groups"].add(group_name)
        _record_operation("CreateLogGroup", group_name, "Succeeded")
        return jsonify({"ok": True, "resource": {"name": group_name}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("CreateLogGroup", group_name, "Failed")
        return _operation_error(error)


@console.post("/api/services/cloudwatch/log-streams")
def post_cloudwatch_log_stream():
    payload = _body()
    group_name = payload.get("group_name", "")
    stream_name = payload.get("stream_name", "")
    if not isinstance(group_name, str) or not LOG_NAME_PATTERN.fullmatch(group_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid log group name."}}), 400
    if not isinstance(stream_name, str) or not LOG_NAME_PATTERN.fullmatch(stream_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid log stream name."}}), 400
    if group_name not in current_app.extensions["console_created_log_groups"]:
        return jsonify({"ok": False, "error": {"code": "LogGroupNotOwnedByConsole", "message": "Create the log group in this dashboard session before adding streams."}}), 403
    try:
        response = cloudwatch.create_log_stream(_client("logs"), group_name, stream_name)
        _record_operation("CreateLogStream", f"{group_name}/{stream_name}", "Succeeded")
        return jsonify({"ok": True, "resource": {"group_name": group_name, "stream_name": stream_name}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("CreateLogStream", f"{group_name}/{stream_name}", "Failed")
        return _operation_error(error)


@console.post("/api/services/cloudwatch/events")
def post_cloudwatch_log_event():
    payload = _body()
    group_name = payload.get("group_name", "")
    stream_name = payload.get("stream_name", "")
    message = payload.get("message", "")
    if not isinstance(group_name, str) or not LOG_NAME_PATTERN.fullmatch(group_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid log group name."}}), 400
    if not isinstance(stream_name, str) or not LOG_NAME_PATTERN.fullmatch(stream_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid log stream name."}}), 400
    if not isinstance(message, str) or not message or len(message.encode("utf-8")) > 262144:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Log messages must be non-empty and no larger than 256 KiB."}}), 400
    if group_name not in current_app.extensions["console_created_log_groups"]:
        return jsonify({"ok": False, "error": {"code": "LogGroupNotOwnedByConsole", "message": "Writes are limited to log groups created in this dashboard session."}}), 403
    try:
        response = cloudwatch.put_log_event(_client("logs"), group_name, stream_name, message)
        _record_operation("PutLogEvents", f"{group_name}/{stream_name}", "Succeeded")
        return jsonify({"ok": True, "resource": {"group_name": group_name, "stream_name": stream_name}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("PutLogEvents", f"{group_name}/{stream_name}", "Failed")
        return _operation_error(error)


@console.post("/api/services/cloudwatch/log-groups/delete")
def post_cloudwatch_delete_log_group():
    payload = _body()
    group_name = payload.get("group_name", "")
    if not isinstance(group_name, str) or not LOG_NAME_PATTERN.fullmatch(group_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid log group name."}}), 400
    if group_name not in current_app.extensions["console_created_log_groups"]:
        return jsonify({"ok": False, "error": {"code": "LogGroupNotOwnedByConsole", "message": "Only log groups created in this dashboard session can be deleted here."}}), 403
    if not _confirmation(payload, group_name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Type the exact log group name to confirm deletion."}}), 400
    try:
        response = cloudwatch.delete_log_group(_client("logs"), group_name)
        current_app.extensions["console_created_log_groups"].discard(group_name)
        _record_operation("DeleteLogGroup", group_name, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteLogGroup", group_name, "Failed")
        return _operation_error(error)


@console.get("/api/services/s3/buckets/<bucket_name>/objects/<path:key>/download")
def download_s3_object(bucket_name, key):
    try:
        client = _client("s3")
        metadata = s3.object_metadata(client, bucket_name, key)
        if (metadata.get("content_length") or 0) > MAX_UPLOAD_BYTES:
            return jsonify({"ok": False, "error": {"code": "ObjectTooLarge", "message": "Downloads are limited to 10 MiB."}}), 413
        response = s3.download_object(client, bucket_name, key)
        content = response["Body"].read()
        response["Body"].close()
        return send_file(
            BytesIO(content),
            mimetype=metadata.get("content_type") or "application/octet-stream",
            as_attachment=True,
            download_name=key.rsplit("/", 1)[-1] or "object",
        )
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.post("/api/services/s3/buckets")
def post_s3_bucket():
    payload = _body()
    name = payload.get("name", "")
    if not isinstance(name, str) or not BUCKET_PATTERN.fullmatch(name) or ".." in name:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid lowercase S3 bucket name."}}), 400
    try:
        response = s3.create_bucket(_client("s3"), name)
        _record_operation("CreateBucket", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"name": name}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateBucket", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/s3/objects")
def post_s3_object():
    bucket_name = request.form.get("bucket", "")
    key = request.form.get("key", "")
    upload = request.files.get("file")
    if not BUCKET_PATTERN.fullmatch(bucket_name) or not key or len(key.encode("utf-8")) > 1024 or upload is None or not upload.filename:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a bucket, object key, and file."}}), 400
    content = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": {"code": "FileTooLarge", "message": "Uploads are limited to 10 MiB."}}), 413
    try:
        response = s3.upload_object(_client("s3"), bucket_name, key, content, upload.mimetype)
        _record_operation("PutObject", f"{bucket_name}/{key}", "Succeeded")
        return jsonify({"ok": True, "resource": {"bucket": bucket_name, "key": key}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError, LookupError) as error:
        _record_operation("PutObject", f"{bucket_name}/{key}", "Failed")
        return _operation_error(error)


@console.post("/api/services/s3/objects/delete")
def post_s3_object_delete():
    payload = _body()
    bucket_name, key = payload.get("bucket", ""), payload.get("key", "")
    if not bucket_name or not key or not _confirmation(payload, key):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the exact object key before deletion."}}), 400
    try:
        response = s3.delete_object(_client("s3"), bucket_name, key)
        _record_operation("DeleteObject", f"{bucket_name}/{key}", "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteObject", f"{bucket_name}/{key}", "Failed")
        return _operation_error(error)


@console.post("/api/services/dynamodb/tables")
def post_dynamodb_table():
    name = _body().get("name", "")
    if not isinstance(name, str) or not TABLE_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Table names must be 3 to 255 letters, numbers, underscore, dot, or hyphen."}}), 400
    try:
        response = dynamodb.create_test_table(_client("dynamodb"), name)
        current_app.extensions["console_created_tables"].add(name)
        _record_operation("CreateTable", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"name": name, "status": response["TableDescription"].get("TableStatus")}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateTable", name, "Failed")
        return _operation_error(error)


@console.get("/api/services/dynamodb/tables/<table_name>")
def get_dynamodb_table(table_name):
    try:
        return jsonify({"ok": True, "table": dynamodb.describe_table(_client("dynamodb"), table_name)})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.post("/api/services/dynamodb/items")
def post_dynamodb_item():
    payload = _body()
    table_name = payload.get("table", "")
    partition_key = payload.get("pk", "")
    value = payload.get("value", "")
    if table_name not in current_app.extensions["console_created_tables"]:
        return jsonify({"ok": False, "error": {"code": "TableNotOwnedByConsole", "message": "For safety, sample writes are limited to tables created in this dashboard session."}}), 403
    if not isinstance(partition_key, str) or not partition_key or len(partition_key) > 256 or not isinstance(value, str) or len(value) > 4096:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a partition key (up to 256 characters) and a value (up to 4096 characters)."}}), 400
    try:
        response = dynamodb.put_test_item(_client("dynamodb"), table_name, partition_key, value)
        _record_operation("PutItem", table_name, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("PutItem", table_name, "Failed")
        return _operation_error(error)


@console.get("/api/services/dynamodb/items")
def get_dynamodb_item():
    table_name = request.args.get("table", "")
    partition_key = request.args.get("pk", "")
    if table_name not in current_app.extensions["console_created_tables"]:
        return jsonify({"ok": False, "error": {"code": "TableNotOwnedByConsole", "message": "For safety, item reads are limited to tables created in this dashboard session."}}), 403
    if not partition_key:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A partition key is required."}}), 400
    try:
        response = dynamodb.get_test_item(_client("dynamodb"), table_name, partition_key)
        return jsonify({"ok": True, "item": response.get("Item")})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.get("/api/services/lambda/functions/<path:function_name>")
def get_lambda_function_configuration(function_name):
    if not function_name or len(function_name) > 256:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A function name or ARN up to 256 characters is required."}}), 400
    try:
        details = lambda_functions.function_configuration(_client("lambda"), function_name)
        return jsonify({"ok": True, "function": details})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.post("/api/services/sqs/queues")
def post_sqs_queue():
    name = _body().get("name", "")
    if not isinstance(name, str) or not QUEUE_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Use a standard queue name containing 1 to 80 letters, numbers, underscores, or hyphens."}}), 400
    try:
        response = sqs.create_standard_queue(_client("sqs"), name)
        current_app.extensions["console_created_queues"].add(response["QueueUrl"])
        _record_operation("CreateQueue", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"url": response["QueueUrl"]}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateQueue", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/sqs/queues/delete")
def post_sqs_delete_queue():
    payload = _body()
    queue_url = payload.get("queue_url", "")
    name = payload.get("name", "")
    if not isinstance(queue_url, str) or not _safe_queue_url(queue_url):
        return jsonify({"ok": False, "error": {"code": "InvalidQueueUrl", "message": "Queue URLs must point to the local Floci endpoint."}}), 400
    if not isinstance(name, str) or not QUEUE_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid standard queue name."}}), 400
    if queue_url not in current_app.extensions["console_created_queues"]:
        return jsonify({"ok": False, "error": {"code": "QueueNotOwnedByConsole", "message": "Only queues created in this dashboard session can be deleted here."}}), 403
    if not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Type the exact queue name to confirm deletion."}}), 400
    try:
        response = _client("sqs").delete_queue(QueueUrl=queue_url)
        current_app.extensions["console_created_queues"].discard(queue_url)
        _record_operation("DeleteQueue", name, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteQueue", name, "Failed")
        return _operation_error(error)


@console.get("/api/services/sqs/attributes")
def get_sqs_attributes():
    queue_url = request.args.get("queue_url", "")
    if not _safe_queue_url(queue_url):
        return jsonify({"ok": False, "error": {"code": "InvalidQueueUrl", "message": "Queue URLs must point to the local Floci endpoint."}}), 400
    try:
        return jsonify({"ok": True, "attributes": sqs.queue_attributes(_client("sqs"), queue_url)})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.post("/api/services/sqs/messages/send")
def post_sqs_send():
    payload = _body()
    queue_url, message = payload.get("queue_url", ""), payload.get("message", "")
    if not _safe_queue_url(queue_url) or not isinstance(message, str) or not message or len(message.encode("utf-8")) > 262144:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a local queue and a non-empty message under 256 KiB."}}), 400
    try:
        response = sqs.send_message(_client("sqs"), queue_url, message)
        _record_operation("SendMessage", queue_url.rsplit("/", 1)[-1], "Succeeded")
        return jsonify({"ok": True, "message_id": response.get("MessageId"), "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("SendMessage", queue_url.rsplit("/", 1)[-1], "Failed")
        return _operation_error(error)


@console.post("/api/services/sqs/messages/receive")
def post_sqs_receive():
    payload = _body()
    queue_url, maximum = payload.get("queue_url", ""), payload.get("max_messages", 5)
    if not _safe_queue_url(queue_url) or not isinstance(maximum, int) or not 1 <= maximum <= 10:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a local queue and receive between 1 and 10 messages."}}), 400
    try:
        messages = sqs.receive_messages(_client("sqs"), queue_url, maximum)
        _record_operation("ReceiveMessage", queue_url.rsplit("/", 1)[-1], "Succeeded")
        visible_messages = []
        handles = current_app.extensions["received_messages"]
        now = time.time()
        for old_token, handle in list(handles.items()):
            if handle.get("expires_at", 0) <= now:
                handles.pop(old_token, None)
        for message in messages:
            token = secrets.token_urlsafe(24)
            handles[token] = {
                "queue_url": queue_url,
                "receipt_handle": message["ReceiptHandle"],
                "message_id": message["MessageId"],
                "expires_at": now + 900,
            }
            visible_messages.append({
                "MessageId": message.get("MessageId"),
                "Body": message.get("Body"),
                "Attributes": message.get("Attributes", {}),
                "MessageAttributes": message.get("MessageAttributes", {}),
                "delete_token": token,
            })
        return jsonify({"ok": True, "messages": visible_messages})
    except (ClientError, BotoCoreError) as error:
        _record_operation("ReceiveMessage", queue_url.rsplit("/", 1)[-1], "Failed")
        return _operation_error(error)


@console.post("/api/services/sqs/messages/delete")
def post_sqs_delete_message():
    payload = _body()
    token = payload.get("delete_token", "")
    message_id = payload.get("message_id", "")
    handles = current_app.extensions["received_messages"]
    received = handles.get(token)
    if received and received.get("expires_at", 0) <= time.time():
        handles.pop(token, None)
        received = None
    if not received or received.get("message_id") != message_id or not _confirmation(payload, message_id):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Select a received message and confirm its message ID before deletion."}}), 400
    queue_url = received["queue_url"]
    try:
        response = sqs.delete_message(_client("sqs"), queue_url, received["receipt_handle"])
        current_app.extensions["received_messages"].pop(token, None)
        _record_operation("DeleteMessage", message_id, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteMessage", message_id, "Failed")
        return _operation_error(error)


@console.post("/api/services/sns/topics")
def post_sns_topic():
    name = _body().get("name", "")
    if not isinstance(name, str) or not TOPIC_PATTERN.fullmatch(name) or name.endswith(".fifo"):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid standard SNS topic name; FIFO topics are not exposed in this console."}}), 400
    try:
        response = sns.create_topic(_client("sns"), name)
        current_app.extensions["console_created_topics"].add(response["TopicArn"])
        _record_operation("CreateTopic", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"arn": response["TopicArn"]}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateTopic", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/sns/topics/delete")
def post_sns_delete_topic():
    payload = _body()
    topic_arn = payload.get("topic_arn", "")
    name = payload.get("name", "")
    if not isinstance(topic_arn, str) or not _safe_topic_arn(topic_arn):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a local SNS topic ARN."}}), 400
    if not isinstance(name, str) or name != topic_arn.rsplit(":", 1)[-1]:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Topic name does not match its ARN."}}), 400
    if topic_arn not in current_app.extensions["console_created_topics"]:
        return jsonify({"ok": False, "error": {"code": "TopicNotOwnedByConsole", "message": "Only topics created in this dashboard session can be deleted here."}}), 403
    if not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Type the exact topic name to confirm deletion."}}), 400
    try:
        response = _client("sns").delete_topic(TopicArn=topic_arn)
        current_app.extensions["console_created_topics"].discard(topic_arn)
        _record_operation("DeleteTopic", name, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteTopic", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/sns/publish")
def post_sns_publish():
    payload = _body()
    topic_arn, message = payload.get("topic_arn", ""), payload.get("message", "")
    if not _safe_topic_arn(topic_arn) or not isinstance(message, str) or not message or len(message.encode("utf-8")) > 262144:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a local topic and a non-empty message under 256 KiB."}}), 400
    try:
        region = current_app.extensions["floci_config"].region
        response = sns.publish_to_local_sqs(_client("sns"), topic_arn, message, region)
        _record_operation("Publish", topic_arn.rsplit(":", 1)[-1], "Succeeded")
        return jsonify({"ok": True, "message_id": response.get("MessageId"), "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except sns.TopicHasSubscriptions as error:
        _record_operation("Publish", topic_arn.rsplit(":", 1)[-1], "Blocked")
        return _operation_error(ValueError(str(error)))
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("Publish", topic_arn.rsplit(":", 1)[-1], "Failed")
        return _operation_error(error)


@console.get("/api/services/sns/subscriptions")
def get_sns_subscriptions():
    topic_arn = request.args.get("topic_arn", "")
    if not _safe_topic_arn(topic_arn):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A local SNS topic ARN is required."}}), 400
    try:
        response = _client("sns").list_subscriptions_by_topic(TopicArn=topic_arn)
        return jsonify({"ok": True, "subscriptions": response.get("Subscriptions", [])})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


SUBSCRIBE_PROTOCOLS = {"email", "email-json", "sms", "http", "https", "sqs", "lambda", "application"}


@console.post("/api/services/sns/subscribe")
def post_sns_subscribe():
    payload = _body()
    topic_arn = payload.get("topic_arn", "")
    protocol = payload.get("protocol", "")
    endpoint = payload.get("endpoint", "")
    if not _safe_topic_arn(topic_arn):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a local SNS topic."}}), 400
    if protocol not in SUBSCRIBE_PROTOCOLS:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a supported subscription protocol."}}), 400
    if not isinstance(endpoint, str) or not endpoint.strip():
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A subscription endpoint is required."}}), 400
    try:
        response = sns.subscribe(_client("sns"), topic_arn, protocol, endpoint.strip())
        _record_operation("Subscribe", topic_arn.rsplit(":", 1)[-1], "Succeeded")
        return jsonify({
            "ok": True,
            "resource": {"subscription_arn": response.get("SubscriptionArn")},
            "http_status": response["ResponseMetadata"]["HTTPStatusCode"],
        }), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("Subscribe", topic_arn.rsplit(":", 1)[-1], "Failed")
        return _operation_error(error)


@console.post("/api/services/api_gateway/rest")
def post_rest_api():
    name = _body().get("name", "")
    if not isinstance(name, str) or not API_NAME_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter an API name using letters, numbers, spaces, dot, underscore, or hyphen."}}), 400
    try:
        response = api_gateway.create_rest_api(_client("apigateway"), name)
        current_app.extensions["console_created_apis"].add(response["id"])
        _record_operation("CreateRestApi", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"id": response["id"], "name": response.get("name")}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateRestApi", name, "Failed")
        return _operation_error(error)


@console.get("/api/services/api_gateway/rest/<api_id>/resources")
def get_rest_api_resources(api_id):
    try:
        resources = api_gateway.list_rest_resources(_client("apigateway"), api_id)
        return jsonify({"ok": True, "resources": resources})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.post("/api/services/api_gateway/rest/delete")
def post_rest_api_delete():
    payload = _body()
    api_id = payload.get("api_id", "")
    api_name = payload.get("name", "")
    if api_id not in current_app.extensions["console_created_apis"]:
        return jsonify({"ok": False, "error": {"code": "ApiNotOwnedByConsole", "message": "Only REST APIs created in this dashboard session can be deleted here."}}), 403
    if not api_id or not api_name or not _confirmation(payload, api_name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the exact REST API name before deletion."}}), 400
    try:
        response = _client("apigateway").delete_rest_api(restApiId=api_id)
        current_app.extensions["console_created_apis"].discard(api_id)
        _record_operation("DeleteRestApi", api_name, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteRestApi", api_name, "Failed")
        return _operation_error(error)


@console.get("/api/services/api_gateway/http/<api_id>/routes")
def get_http_api_routes(api_id):
    try:
        routes = api_gateway.list_http_routes(_client("apigatewayv2"), api_id)
        return jsonify({"ok": True, "routes": routes})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


@console.post("/api/services/api_gateway/http/routes")
def post_http_api_route():
    payload = _body()
    api_id = payload.get("api_id", "")
    route_key = payload.get("route_key", "")
    function_name = payload.get("function_name", "")
    if not isinstance(api_id, str) or not API_ID_PATTERN.fullmatch(api_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a valid HTTP API."}}), 400
    if api_id not in current_app.extensions["console_created_apis"]:
        return jsonify({"ok": False, "error": {"code": "ApiNotOwnedByConsole", "message": "Routes can only be added to an HTTP API created in this dashboard session."}}), 403
    if not isinstance(route_key, str) or not HTTP_ROUTE_PATTERN.fullmatch(route_key):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Route must be an HTTP method and a path, for example GET /hello."}}), 400
    if not isinstance(function_name, str) or not FUNCTION_NAME_PATTERN.fullmatch(function_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid Lambda function name."}}), 400
    try:
        region = current_app.extensions["floci_config"].region
        route = api_gateway.create_http_lambda_route(
            _client("apigatewayv2"),
            _client("lambda"),
            api_id,
            route_key,
            function_name,
            region,
        )
        _record_operation("CreateRoute", f"{api_id} {route_key}", "Succeeded")
        return jsonify({"ok": True, "resource": route, "http_status": 201}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateRoute", f"{api_id} {route_key}", "Failed")
        return _operation_error(error)


@console.post("/api/services/api_gateway/http")
def post_http_api():
    name = _body().get("name", "")
    if not isinstance(name, str) or not API_NAME_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter an API name using letters, numbers, spaces, dot, underscore, or hyphen."}}), 400
    try:
        response = api_gateway.create_http_api(_client("apigatewayv2"), name)
        current_app.extensions["console_created_apis"].add(response["ApiId"])
        _record_operation("CreateApi", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"id": response["ApiId"], "name": response.get("Name")}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateApi", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/api_gateway/http/delete")
def post_http_api_delete():
    payload = _body()
    api_id = payload.get("api_id", "")
    api_name = payload.get("name", "")
    if not isinstance(api_id, str) or not API_ID_PATTERN.fullmatch(api_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a valid HTTP API."}}), 400
    if api_id not in current_app.extensions["console_created_apis"]:
        return jsonify({"ok": False, "error": {"code": "ApiNotOwnedByConsole", "message": "Only HTTP APIs created in this dashboard session can be deleted here."}}), 403
    if not isinstance(api_name, str) or not api_name or not _confirmation(payload, api_name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the exact HTTP API name before deletion."}}), 400
    try:
        response = api_gateway.delete_http_api(_client("apigatewayv2"), api_id)
        current_app.extensions["console_created_apis"].discard(api_id)
        _record_operation("DeleteApi", api_name, "Succeeded")
        return jsonify({"ok": True, "http_status": response.get("ResponseMetadata", {}).get("HTTPStatusCode", 200)})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteApi", api_name, "Failed")
        return _operation_error(error)


@console.post("/api/services/api_gateway/http/invoke")
def post_http_api_invoke():
    payload = _body()
    api_id = payload.get("api_id", "")
    method = payload.get("method") or "GET"
    path = payload.get("path") or "/"
    if not isinstance(api_id, str) or not API_ID_PATTERN.fullmatch(api_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A valid HTTP API ID is required."}}), 400
    if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Unsupported HTTP method."}}), 400
    if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Path must start with / and cannot contain a query string or fragment."}}), 400
    config = current_app.extensions["floci_config"]
    endpoint = config.endpoint_url.rstrip("/")
    invoke_path = quote(path.lstrip("/"), safe="/{}-._~")
    invoke_url = f"{endpoint}/execute-api/{quote(api_id, safe='')}/$default/{invoke_path}"
    body = payload.get("body")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    try:
        if body is not None and not isinstance(body, str):
            body = json.dumps(body)
        request_data = body.encode("utf-8") if body is not None else None
        req = Request(invoke_url, data=request_data, method=method, headers=headers)
        with urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(resp_body)
            except (ValueError, TypeError):
                parsed = resp_body
            _record_operation("Invoke", api_id, "Succeeded")
            return jsonify({"ok": True, "status_code": resp.status, "body": parsed})
    except HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body_text)
        except (ValueError, TypeError):
            parsed = body_text
        _record_operation("Invoke", api_id, f"HTTP {error.code}")
        return jsonify({"ok": True, "status_code": error.code, "body": parsed})
    except (ClientError, BotoCoreError) as error:
        _record_operation("Invoke", api_id, "Failed")
        return _operation_error(error)
    except (OSError, URLError, TimeoutError) as error:
        _record_operation("Invoke", api_id, "Failed")
        return jsonify({"ok": False, "error": {"code": type(error).__name__, "message": "The HTTP API invoke request failed."}}), 502


# ---------------------------------------------------------------------------
# EC2 — Docker-backed instance lifecycle
# ---------------------------------------------------------------------------



@console.post("/api/services/ec2/instances")
def post_ec2_instances():
    payload = _body()
    image_id = payload.get("image_id", "")
    instance_type = payload.get("instance_type", "")
    name = payload.get("name", "")
    count = payload.get("count", 1)
    key_name = payload.get("key_name") or None
    install_ssh = payload.get("install_ssh", False)
    if not isinstance(image_id, str) or not AMI_ID_PATTERN.fullmatch(image_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid AMI ID (ami-...)."}}), 400
    if not isinstance(instance_type, str) or not INSTANCE_TYPE_PATTERN.fullmatch(instance_type):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid instance type (e.g. t2.micro)."}}), 400
    if not isinstance(count, int) or not 1 <= count <= 10:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Count must be an integer between 1 and 10."}}), 400
    if name and (not isinstance(name, str) or len(name) > 128):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Instance name must be 128 characters or fewer."}}), 400
    if key_name is not None and (not isinstance(key_name, str) or not KEY_NAME_PATTERN.fullmatch(key_name)):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid key pair name."}}), 400
    if not isinstance(install_ssh, bool):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "install_ssh must be true or false."}}), 400
    try:
        result = ec2.run_instances(
            _client("ec2"), image_id, instance_type, name or None, count,
            key_name=key_name, install_ssh=install_ssh,
        )
        for instance_id in result["instance_ids"]:
            current_app.extensions["console_created_instances"].add(instance_id)
        _record_operation("RunInstances", f"{image_id} x{count}", "Succeeded")
        return jsonify({"ok": True, "resource": result, "http_status": 200}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("RunInstances", f"{image_id} x{count}", "Failed")
        return _operation_error(error)


def _ec2_lifecycle(action, service_call, operation_label):
    payload = _body()
    instance_ids = payload.get("instance_ids", [])
    if not isinstance(instance_ids, list) or not instance_ids:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Provide at least one instance ID."}}), 400
    if any(not isinstance(iid, str) or not INSTANCE_ID_PATTERN.fullmatch(iid) for iid in instance_ids):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Each instance ID must match i-... ."}}), 400
    owned = current_app.extensions["console_created_instances"]
    # Start/Stop/Reboot are allowed on any visible instance; terminate requires ownership.
    if action == "terminate":
        unowned = [iid for iid in instance_ids if iid not in owned]
        if unowned:
            return jsonify({"ok": False, "error": {"code": "InstanceNotOwnedByConsole", "message": "Only instances created in this dashboard session can be terminated here."}}), 403
    try:
        changes = service_call(_client("ec2"), instance_ids)
        if action == "terminate":
            for iid in instance_ids:
                owned.discard(iid)
        _record_operation(operation_label, ", ".join(instance_ids), "Succeeded")
        return jsonify({"ok": True, "changes": changes, "http_status": 200})
    except (ClientError, BotoCoreError) as error:
        _record_operation(operation_label, ", ".join(instance_ids), "Failed")
        return _operation_error(error)


@console.post("/api/services/ec2/instances/start")
def post_ec2_start():
    return _ec2_lifecycle("start", ec2.start_instances, "StartInstances")


@console.post("/api/services/ec2/instances/stop")
def post_ec2_stop():
    return _ec2_lifecycle("stop", ec2.stop_instances, "StopInstances")


@console.post("/api/services/ec2/instances/reboot")
def post_ec2_reboot():
    return _ec2_lifecycle("reboot", ec2.reboot_instances, "RebootInstances")


@console.post("/api/services/ec2/instances/terminate")
def post_ec2_terminate():
    return _ec2_lifecycle("terminate", ec2.terminate_instances, "TerminateInstances")


@console.get("/api/services/ec2/instances/<instance_id>/connect")
def get_ec2_connect(instance_id):
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Instance ID must match i-... ."}}), 400
    try:
        instance = ec2.describe_instance(_client("ec2"), instance_id)
    except (ClientError, BotoCoreError, LookupError) as error:
        return _operation_error(error)
    endpoint = ec2.find_ssh_endpoint(instance_id)
    key_file = f"{instance['key_name']}.pem" if instance.get("key_name") else "<key-pair>.pem"
    port = endpoint["port"]
    return jsonify({
        "ok": True,
        "instance": instance,
        "ssh": {
            "user": ec2.SSH_USER,
            "host": ec2.SSH_HOST,
            "port": port,
            "key_name": instance.get("key_name"),
            "container": endpoint["container"],
            "command": f"ssh -i {shlex.quote(key_file)} -p {port or '<ssh-port>'} {ec2.SSH_USER}@{ec2.SSH_HOST}",
        },
    })


@console.post("/api/services/ec2/instances/command")
def post_ec2_command():
    payload = _body()
    instance_id = payload.get("instance_id", "")
    command = payload.get("command", "")
    cwd = payload.get("cwd") or "/"
    if not isinstance(instance_id, str) or not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Instance ID must match i-... ."}}), 400
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND_LENGTH:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": f"Enter a command of at most {MAX_COMMAND_LENGTH} characters."}}), 400
    if not isinstance(cwd, str) or not cwd.startswith("/") or len(cwd) > 1024 or "\n" in cwd:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Working directory must be an absolute path."}}), 400
    script = (
        f"cd {shlex.quote(cwd)} 2>/dev/null || cd /\n"
        f"{command}\n"
        "__lc_rc=$?\n"
        f"printf '\\n{CWD_MARKER}%s' \"$(pwd)\"\n"
        "exit $__lc_rc"
    )
    try:
        result = ssm.run_shell_command(_client("ssm"), instance_id, script)
    except (ClientError, BotoCoreError) as error:
        _record_operation("SendCommand", instance_id, "Failed")
        return _operation_error(error)
    stdout = result["stdout"] or ""
    new_cwd = cwd
    marker_at = stdout.rfind(CWD_MARKER)
    if marker_at != -1:
        new_cwd = stdout[marker_at + len(CWD_MARKER):].strip() or cwd
        stdout = stdout[:marker_at]
        if stdout.endswith("\n"):
            stdout = stdout[:-1]
    result.update({"stdout": stdout, "cwd": new_cwd})
    _record_operation("SendCommand", instance_id, "Succeeded" if result["status"] == "Success" else result["status"])
    return jsonify({"ok": True, "result": result})


@console.post("/api/services/ec2/key-pairs")
def post_ec2_key_pair():
    name = _body().get("name", "")
    if not isinstance(name, str) or not KEY_NAME_PATTERN.fullmatch(name) or name != name.strip():
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Key pair names use letters, numbers, spaces, dots, dashes, and underscores."}}), 400
    try:
        client = _client("ec2")
        if any(key["name"] == name for key in ec2.list_key_pairs(client)):
            return jsonify({"ok": False, "error": {"code": "KeyPairAlreadyExists", "message": "A key pair with this name already exists."}}), 409
        result = ec2.create_key_pair(client, name)
        current_app.extensions["console_created_key_pairs"].add(name)
        _record_operation("CreateKeyPair", name, "Succeeded")
        response = jsonify({"ok": True, "resource": result, "http_status": 201})
        response.headers["Cache-Control"] = "no-store"
        return response, 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("CreateKeyPair", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/ec2/key-pairs/delete")
def post_ec2_key_pair_delete():
    payload = _body()
    name = payload.get("name", "")
    if not isinstance(name, str) or not KEY_NAME_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid key pair name."}}), 400
    if name not in current_app.extensions["console_created_key_pairs"]:
        return jsonify({"ok": False, "error": {"code": "KeyPairNotOwnedByConsole", "message": "Only key pairs created in this dashboard session can be deleted here."}}), 403
    if not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the exact key pair name before deletion."}}), 400
    try:
        response = ec2.delete_key_pair(_client("ec2"), name)
        current_app.extensions["console_created_key_pairs"].discard(name)
        _record_operation("DeleteKeyPair", name, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteKeyPair", name, "Failed")
        return _operation_error(error)


# ---------------------------------------------------------------------------
# Lambda — create / invoke / delete
# ---------------------------------------------------------------------------


@console.post("/api/services/lambda/functions")
def post_lambda_function():
    payload = _body()
    name = payload.get("name", "")
    runtime = payload.get("runtime", "")
    handler = payload.get("handler", "")
    code = payload.get("code", "")
    if not isinstance(name, str) or not FUNCTION_NAME_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Function name must be 1-64 letters, numbers, underscore, or hyphen."}}), 400
    if not isinstance(runtime, str) or not RUNTIME_PATTERN.fullmatch(runtime):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid runtime identifier."}}), 400
    if runtime not in lambda_functions.SUPPORTED_RUNTIMES:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": f"Unsupported runtime. Choose one of: {', '.join(lambda_functions.SUPPORTED_RUNTIMES)}."}}), 400
    if not isinstance(handler, str) or not HANDLER_PATTERN.fullmatch(handler):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Handler must be in the form filename.function_name."}}), 400
    if not isinstance(code, str) or not code.strip():
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Function source code is required."}}), 400
    if len(code.encode("utf-8")) > MAX_LAMBDA_CODE_BYTES:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Function code is limited to 256 KiB."}}), 400
    memory_size = payload.get("memory_size", 128)
    timeout = payload.get("timeout", 3)
    if not isinstance(memory_size, int) or not 128 <= memory_size <= 3008:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Memory size must be between 128 and 3008 MB."}}), 400
    if not isinstance(timeout, int) or not 1 <= timeout <= 900:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Timeout must be between 1 and 900 seconds."}}), 400
    try:
        result = lambda_functions.create_function(
            _client("lambda"), name, runtime, handler, code,
            memory_size=memory_size, timeout=timeout,
        )
        current_app.extensions["console_created_functions"].add(name)
        _record_operation("CreateFunction", name, "Succeeded")
        return jsonify({"ok": True, "resource": result, "http_status": 201}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateFunction", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/lambda/functions/invoke")
def post_lambda_invoke():
    payload = _body()
    name = payload.get("name", "")
    event = payload.get("payload")
    if not isinstance(name, str) or not FUNCTION_NAME_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid function name."}}), 400
    if event is not None and not isinstance(event, (dict, list, str, int, float, bool)):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Payload must be valid JSON."}}), 400
    try:
        result = lambda_functions.invoke_function(_client("lambda"), name, event)
        _record_operation("Invoke", name, "Succeeded")
        return jsonify({"ok": True, "result": result, "http_status": 200})
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("Invoke", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/lambda/functions/delete")
def post_lambda_delete():
    payload = _body()
    name = payload.get("name", "")
    if not isinstance(name, str) or not FUNCTION_NAME_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid function name."}}), 400
    if name not in current_app.extensions["console_created_functions"]:
        return jsonify({"ok": False, "error": {"code": "FunctionNotOwnedByConsole", "message": "Only functions created in this dashboard session can be deleted here."}}), 403
    if not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the exact function name before deletion."}}), 400
    try:
        response = lambda_functions.delete_function(_client("lambda"), name)
        current_app.extensions["console_created_functions"].discard(name)
        _record_operation("DeleteFunction", name, "Succeeded")
        return jsonify({"ok": True, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteFunction", name, "Failed")
        return _operation_error(error)