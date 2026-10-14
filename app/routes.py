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
        return {"queues": sqs.list_queues(clients.get("sqs"))}
    if service_name == "sns":
        return {"topics": sns.list_topics(clients.get("sns"))}
    if service_name == "lambda":
        return {"functions": lambda_functions.list_functions(clients.get("lambda"))}
    if service_name == "ec2":
        return {"instances": ec2.list_instances(clients.get("ec2"))}
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