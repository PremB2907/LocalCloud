import json
import re
import secrets
import time
from datetime import datetime
from io import BytesIO
from urllib.parse import urlsplit
from urllib.error import URLError
from urllib.request import urlopen

from botocore.exceptions import BotoCoreError, ClientError
from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from app import catalog
from app.services import api_gateway, cloudwatch, dynamodb, ec2, lambda_functions, s3, sns, sqs


console = Blueprint("console", __name__)
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
TABLE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
QUEUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
API_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]{1,128}$")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


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


def _owned(set_name):
    return current_app.extensions[set_name]


def _require_owned(set_name, value, code, message):
    if value not in _owned(set_name):
        return jsonify({"ok": False, "error": {"code": code, "message": message}}), 403
    return None


def _as_int(value, default=None):
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


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
        owned = _owned("console_created_buckets")
        return {"buckets": [{**bucket, "console_owned": bucket["name"] in owned} for bucket in s3.list_buckets(clients.get("s3"))]}
    if service_name == "dynamodb":
        owned = _owned("console_created_tables")
        return {"tables": [{**table, "console_owned": table["name"] in owned} for table in dynamodb.list_tables(clients.get("dynamodb"))]}
    if service_name == "sqs":
        owned = _owned("console_created_queues")
        return {"queues": [{**queue, "console_owned": queue["name"] in owned} for queue in sqs.list_queues(clients.get("sqs"))]}
    if service_name == "sns":
        owned = _owned("console_created_topics")
        return {"topics": [{**topic, "console_owned": topic["name"] in owned} for topic in sns.list_topics(clients.get("sns"))]}
    if service_name == "lambda":
        owned = _owned("console_created_functions")
        return {"functions": [{**fn, "console_owned": fn["name"] in owned} for fn in lambda_functions.list_functions(clients.get("lambda"))]}
    if service_name == "ec2":
        owned = _owned("console_created_instances")
        return {"instances": [{**inst, "console_owned": inst["id"] in owned} for inst in ec2.list_instances(clients.get("ec2"))]}
    if service_name == "cloudwatch":
        data = {}
        owned_groups = _owned("console_created_log_groups")
        for key, service_client, loader in (
            ("metrics", "cloudwatch", cloudwatch.list_metrics),
            ("log_groups", "logs", cloudwatch.list_log_groups),
        ):
            try:
                result = loader(clients.get(service_client))
                if key == "log_groups":
                    result["log_groups"] = [{**group, "console_owned": group["name"] in owned_groups} for group in result["log_groups"]]
                data[key] = {"ok": True, **result}
            except (ClientError, BotoCoreError) as error:
                detail, status = _safe_error(error)
                data[key] = {"ok": False, "error": detail, "http_status": status}
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
                if key == "rest":
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
        _owned("console_created_buckets").add(name)
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
        _owned("console_created_queues").add(name)
        _record_operation("CreateQueue", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"url": response["QueueUrl"]}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateQueue", name, "Failed")
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
        _owned("console_created_topics").add(name)
        _record_operation("CreateTopic", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"arn": response["TopicArn"]}, "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateTopic", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/sns/publish")
def post_sns_publish():
    payload = _body()
    topic_arn, message = payload.get("topic_arn", ""), payload.get("message", "")
    if not _safe_topic_arn(topic_arn) or not isinstance(message, str) or not message or len(message.encode("utf-8")) > 262144:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Choose a local topic and a non-empty message under 256 KiB."}}), 400
    try:
        response = sns.publish_if_unsubscribed(_client("sns"), topic_arn, message)
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


# ====================================================================
#  EC2 ROUTES
# ====================================================================

INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")
EC2_IMAGE_PATTERN = re.compile(r"^ami-[0-9a-f]{8,17}$")
EC2_TYPES = {"t2.nano", "t2.micro", "t2.small", "t2.medium", "t2.large",
             "t3.nano", "t3.micro", "t3.small", "t3.medium", "t3.large",
             "m5.large", "m5.xlarge"}


@console.get("/api/services/ec2/instances/<instance_id>")
def get_ec2_instance(instance_id):
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Invalid instance ID format."}}), 400
    try:
        details = ec2.describe_instance(_client("ec2"), instance_id)
        return jsonify({"ok": True, "instance": details})
    except (ClientError, BotoCoreError, LookupError) as error:
        return _operation_error(error)


@console.post("/api/services/ec2/instances")
def post_ec2_instance():
    payload = _body()
    image_id = payload.get("image_id", "ami-ff0fea8310f3")
    instance_type = payload.get("instance_type", "t2.micro")
    name = payload.get("name", "")
    if not EC2_IMAGE_PATTERN.fullmatch(image_id):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid AMI ID (ami-xxxxxxxx)."}}), 400
    if instance_type not in EC2_TYPES:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": f"Use a supported instance type: {', '.join(sorted(EC2_TYPES))}."}}), 400
    if name and (not isinstance(name, str) or len(name) > 255):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Instance name must be 255 characters or fewer."}}), 400
    try:
        instances = ec2.run_instances(_client("ec2"), image_id, instance_type, 1, name or None)
        for inst in instances:
            if inst.get("id"):
                _owned("console_created_instances").add(inst["id"])
        _record_operation("RunInstances", instances[0]["id"] if instances else "unknown", "Succeeded")
        return jsonify({"ok": True, "instances": instances}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("RunInstances", image_id, "Failed")
        return _operation_error(error)


@console.post("/api/services/ec2/instances/start")
def post_ec2_start():
    payload = _body()
    instance_ids = payload.get("instance_ids", [])
    if not isinstance(instance_ids, list) or not instance_ids or not all(isinstance(i, str) and INSTANCE_ID_PATTERN.fullmatch(i) for i in instance_ids):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Provide valid instance IDs."}}), 400
    owned = _owned("console_created_instances")
    if any(instance_id not in owned for instance_id in instance_ids):
        return jsonify({"ok": False, "error": {"code": "InstanceNotOwnedByConsole", "message": "Only instances launched in this session can be started."}}), 403
    try:
        ec2.start_instances(_client("ec2"), instance_ids)
        for iid in instance_ids:
            _record_operation("StartInstances", iid, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        for iid in instance_ids:
            _record_operation("StartInstances", iid, "Failed")
        return _operation_error(error)


@console.post("/api/services/ec2/instances/stop")
def post_ec2_stop():
    payload = _body()
    instance_ids = payload.get("instance_ids", [])
    if not isinstance(instance_ids, list) or not instance_ids or not all(isinstance(i, str) and INSTANCE_ID_PATTERN.fullmatch(i) for i in instance_ids):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Provide valid instance IDs."}}), 400
    owned = _owned("console_created_instances")
    if any(instance_id not in owned for instance_id in instance_ids):
        return jsonify({"ok": False, "error": {"code": "InstanceNotOwnedByConsole", "message": "Only instances launched in this session can be stopped."}}), 403
    try:
        ec2.stop_instances(_client("ec2"), instance_ids)
        for iid in instance_ids:
            _record_operation("StopInstances", iid, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        for iid in instance_ids:
            _record_operation("StopInstances", iid, "Failed")
        return _operation_error(error)


@console.post("/api/services/ec2/instances/terminate")
def post_ec2_terminate():
    payload = _body()
    instance_id = payload.get("instance_id", "")
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id) or not _confirmation(payload, instance_id):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the instance ID to terminate."}}), 400
    owned = _owned("console_created_instances")
    if instance_id not in owned:
        return jsonify({"ok": False, "error": {"code": "InstanceNotOwnedByConsole", "message": "Only instances launched in this session can be terminated."}}), 403
    try:
        ec2.terminate_instances(_client("ec2"), [instance_id])
        owned.discard(instance_id)
        _record_operation("TerminateInstances", instance_id, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        _record_operation("TerminateInstances", instance_id, "Failed")
        return _operation_error(error)


# ====================================================================
#  LAMBDA ROUTES
# ====================================================================

FUNCTION_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
LAMBDA_RUNTIMES = {"python3.8", "python3.9", "python3.10", "python3.11", "python3.12",
                    "nodejs18.x", "nodejs20.x", "nodejs22.x",
                    "java17", "java21", "dotnet8", "go1.x", "provided.al2023"}


@console.post("/api/services/lambda/functions")
def post_lambda_function():
    payload = _body()
    name = payload.get("name", "")
    runtime = payload.get("runtime", "python3.12")
    memory = _as_int(payload.get("memory_size", 128))
    timeout_val = _as_int(payload.get("timeout", 30))
    description = payload.get("description", "")

    if not isinstance(name, str) or not FUNCTION_NAME_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Function name must start with a letter and contain only letters, numbers, underscores, hyphens (max 64 chars)."}}), 400
    if runtime not in LAMBDA_RUNTIMES:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": f"Supported runtimes: {', '.join(sorted(LAMBDA_RUNTIMES))}."}}), 400
    if memory is None or memory < 128 or memory > 3008:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Memory must be between 128 and 3008 MB."}}), 400
    if timeout_val is None or timeout_val < 1 or timeout_val > 900:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Timeout must be between 1 and 900 seconds."}}), 400
    if not isinstance(description, str) or len(description) > 256:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Description must be 256 characters or fewer."}}), 400

    try:
        response = lambda_functions.create_function(_client("lambda"), name, runtime,
                                                      memory_size=memory, timeout=timeout_val,
                                                      description=description)
        _owned("console_created_functions").add(name)
        _record_operation("CreateFunction", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"name": response.get("FunctionName"), "arn": response.get("FunctionArn")}}), 201
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("CreateFunction", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/lambda/functions/<path:function_name>/invoke")
def post_lambda_invoke(function_name):
    if not function_name or not FUNCTION_NAME_PATTERN.fullmatch(function_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A valid function name is required."}}), 400
    denied = _require_owned("console_created_functions", function_name, "FunctionNotOwnedByConsole", "Only functions created in this session can be invoked.")
    if denied:
        return denied
    payload = _body()
    event_payload = payload.get("payload", {})
    if event_payload is None:
        event_payload = {}
    if isinstance(event_payload, str):
        try:
            event_payload = json.loads(event_payload) if event_payload else {}
        except ValueError:
            return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Invoke payload must be JSON."}}), 400
    if not isinstance(event_payload, dict):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Invoke payload must be a JSON object."}}), 400
    try:
        result = lambda_functions.invoke_function(_client("lambda"), function_name, event_payload)
        _record_operation("Invoke", function_name, "Succeeded" if not result.get("function_error") else "Failed")
        return jsonify({"ok": True, "result": result})
    except (ClientError, BotoCoreError) as error:
        _record_operation("Invoke", function_name, "Failed")
        return _operation_error(error)


@console.post("/api/services/lambda/functions/delete")
def post_lambda_delete():
    payload = _body()
    name = payload.get("name", "")
    if not name or not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the function name."}}), 400
    owned = _owned("console_created_functions")
    if name not in owned:
        return jsonify({"ok": False, "error": {"code": "FunctionNotOwnedByConsole", "message": "Only functions created in this session can be deleted."}}), 403
    try:
        lambda_functions.delete_function(_client("lambda"), name)
        owned.discard(name)
        _record_operation("DeleteFunction", name, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteFunction", name, "Failed")
        return _operation_error(error)


# ====================================================================
#  S3 DELETE BUCKET
# ====================================================================

@console.post("/api/services/s3/buckets/delete")
def post_s3_bucket_delete():
    payload = _body()
    name = payload.get("name", "")
    if not isinstance(name, str) or not BUCKET_PATTERN.fullmatch(name) or not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the bucket name."}}), 400
    denied = _require_owned("console_created_buckets", name, "BucketNotOwnedByConsole", "Only buckets created in this session can be deleted.")
    if denied:
        return denied
    try:
        s3.delete_bucket(_client("s3"), name)
        _owned("console_created_buckets").discard(name)
        _record_operation("DeleteBucket", name, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError, ValueError) as error:
        _record_operation("DeleteBucket", name, "Failed")
        return _operation_error(error)


# ====================================================================
#  DYNAMODB DELETE TABLE & SCAN
# ====================================================================

@console.post("/api/services/dynamodb/tables/delete")
def post_dynamodb_table_delete():
    payload = _body()
    name = payload.get("name", "")
    if not name or not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the table name."}}), 400
    owned = _owned("console_created_tables")
    if name not in owned:
        return jsonify({"ok": False, "error": {"code": "TableNotOwnedByConsole", "message": "Only tables created in this session can be deleted."}}), 403
    try:
        dynamodb.delete_table(_client("dynamodb"), name)
        owned.discard(name)
        _record_operation("DeleteTable", name, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteTable", name, "Failed")
        return _operation_error(error)


@console.get("/api/services/dynamodb/tables/<table_name>/scan")
def get_dynamodb_scan(table_name):
    if not TABLE_PATTERN.fullmatch(table_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Invalid table name."}}), 400
    denied = _require_owned("console_created_tables", table_name, "TableNotOwnedByConsole", "For safety, scans are limited to tables created in this dashboard session.")
    if denied:
        return denied
    try:
        result = dynamodb.scan_table(_client("dynamodb"), table_name)
        return jsonify({"ok": True, **result})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


# ====================================================================
#  SQS PURGE & DELETE QUEUE
# ====================================================================

@console.post("/api/services/sqs/queues/purge")
def post_sqs_purge():
    payload = _body()
    queue_url = payload.get("queue_url", "")
    queue_name = queue_url.rsplit("/", 1)[-1] if queue_url else ""
    if not _safe_queue_url(queue_url) or not _confirmation(payload, queue_name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the queue name to purge."}}), 400
    denied = _require_owned("console_created_queues", queue_name, "QueueNotOwnedByConsole", "Only queues created in this session can be purged.")
    if denied:
        return denied
    try:
        sqs.purge_queue(_client("sqs"), queue_url)
        _record_operation("PurgeQueue", queue_url.rsplit("/", 1)[-1], "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        _record_operation("PurgeQueue", queue_url.rsplit("/", 1)[-1], "Failed")
        return _operation_error(error)


@console.post("/api/services/sqs/queues/delete")
def post_sqs_delete_queue():
    payload = _body()
    queue_url = payload.get("queue_url", "")
    queue_name = queue_url.rsplit("/", 1)[-1] if queue_url else ""
    if not _safe_queue_url(queue_url) or not _confirmation(payload, queue_name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the queue name to delete."}}), 400
    denied = _require_owned("console_created_queues", queue_name, "QueueNotOwnedByConsole", "Only queues created in this session can be deleted.")
    if denied:
        return denied
    try:
        sqs.delete_queue(_client("sqs"), queue_url)
        _owned("console_created_queues").discard(queue_name)
        _record_operation("DeleteQueue", queue_name, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteQueue", queue_url.rsplit("/", 1)[-1], "Failed")
        return _operation_error(error)


# ====================================================================
#  SNS DELETE TOPIC & ATTRIBUTES
# ====================================================================

@console.post("/api/services/sns/topics/delete")
def post_sns_topic_delete():
    payload = _body()
    topic_arn = payload.get("topic_arn", "")
    topic_name = topic_arn.rsplit(":", 1)[-1] if topic_arn else ""
    if not _safe_topic_arn(topic_arn) or not _confirmation(payload, topic_name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the topic name to delete."}}), 400
    denied = _require_owned("console_created_topics", topic_name, "TopicNotOwnedByConsole", "Only topics created in this session can be deleted.")
    if denied:
        return denied
    try:
        sns.delete_topic(_client("sns"), topic_arn)
        _owned("console_created_topics").discard(topic_name)
        _record_operation("DeleteTopic", topic_name, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteTopic", topic_arn.rsplit(":", 1)[-1], "Failed")
        return _operation_error(error)


@console.get("/api/services/sns/attributes")
def get_sns_topic_attributes():
    topic_arn = request.args.get("topic_arn", "")
    if not _safe_topic_arn(topic_arn):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A local SNS topic ARN is required."}}), 400
    try:
        attrs = sns.topic_attributes(_client("sns"), topic_arn)
        return jsonify({"ok": True, "attributes": attrs})
    except (ClientError, BotoCoreError) as error:
        detail, status = _safe_error(error)
        return jsonify({"ok": False, "error": detail}), status


# ====================================================================
#  CLOUDWATCH LOG MANAGEMENT
# ====================================================================

LOG_GROUP_PATTERN = re.compile(r"^[a-zA-Z0-9_./#-]{1,512}$")
LOG_STREAM_PATTERN = re.compile(r"^[a-zA-Z0-9_./#-]{1,512}$")


@console.post("/api/services/cloudwatch/log-groups")
def post_cloudwatch_log_group():
    payload = _body()
    name = payload.get("name", "")
    if not isinstance(name, str) or not LOG_GROUP_PATTERN.fullmatch(name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Enter a valid log group name."}}), 400
    retention = payload.get("retention_days")
    if retention is not None and (not isinstance(retention, int) or retention not in {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653}):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Use a valid retention period in days."}}), 400
    try:
        cloudwatch.create_log_group(_client("logs"), name, retention)
        _owned("console_created_log_groups").add(name)
        _record_operation("CreateLogGroup", name, "Succeeded")
        return jsonify({"ok": True, "resource": {"name": name}}), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("CreateLogGroup", name, "Failed")
        return _operation_error(error)


@console.post("/api/services/cloudwatch/log-streams")
def post_cloudwatch_log_stream():
    payload = _body()
    group_name = payload.get("group_name", "")
    stream_name = payload.get("stream_name", "")
    if not group_name or not LOG_GROUP_PATTERN.fullmatch(group_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A valid log group name is required."}}), 400
    if not stream_name or not LOG_STREAM_PATTERN.fullmatch(stream_name):
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "A valid log stream name is required."}}), 400
    denied = _require_owned("console_created_log_groups", group_name, "LogGroupNotOwnedByConsole", "Streams can only be created in log groups from this session.")
    if denied:
        return denied
    try:
        cloudwatch.create_log_stream(_client("logs"), group_name, stream_name)
        _record_operation("CreateLogStream", f"{group_name}/{stream_name}", "Succeeded")
        return jsonify({"ok": True, "resource": {"name": stream_name}}), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("CreateLogStream", f"{group_name}/{stream_name}", "Failed")
        return _operation_error(error)


@console.post("/api/services/cloudwatch/events")
def post_cloudwatch_events():
    payload = _body()
    group_name = payload.get("group_name", "")
    stream_name = payload.get("stream_name", "")
    message = payload.get("message", "")
    if not isinstance(group_name, str) or not LOG_GROUP_PATTERN.fullmatch(group_name) or not isinstance(stream_name, str) or not LOG_STREAM_PATTERN.fullmatch(stream_name) or not isinstance(message, str) or not message:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Group name, stream name, and message are required."}}), 400
    denied = _require_owned("console_created_log_groups", group_name, "LogGroupNotOwnedByConsole", "Events can only be written to log groups from this session.")
    if denied:
        return denied
    if len(message) > 262144:
        return jsonify({"ok": False, "error": {"code": "ValidationError", "message": "Message too large (max 256 KiB)."}}), 400
    try:
        cloudwatch.put_log_events(_client("logs"), group_name, stream_name, [message])
        _record_operation("PutLogEvents", f"{group_name}/{stream_name}", "Succeeded")
        return jsonify({"ok": True}), 201
    except (ClientError, BotoCoreError) as error:
        _record_operation("PutLogEvents", f"{group_name}/{stream_name}", "Failed")
        return _operation_error(error)


@console.post("/api/services/cloudwatch/log-groups/delete")
def post_cloudwatch_log_group_delete():
    payload = _body()
    name = payload.get("name", "")
    if not name or not _confirmation(payload, name):
        return jsonify({"ok": False, "error": {"code": "ConfirmationRequired", "message": "Confirm the log group name."}}), 400
    owned = _owned("console_created_log_groups")
    if name not in owned:
        return jsonify({"ok": False, "error": {"code": "LogGroupNotOwnedByConsole", "message": "Only log groups created in this session can be deleted."}}), 403
    try:
        cloudwatch.delete_log_group(_client("logs"), name)
        owned.discard(name)
        _record_operation("DeleteLogGroup", name, "Succeeded")
        return jsonify({"ok": True})
    except (ClientError, BotoCoreError) as error:
        _record_operation("DeleteLogGroup", name, "Failed")
        return _operation_error(error)