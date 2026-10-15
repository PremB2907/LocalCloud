import io
import os
import subprocess
import time
import uuid
from urllib.parse import urlsplit

import boto3
import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_FLOCI_INTEGRATION") != "1", reason="set RUN_FLOCI_INTEGRATION=1 to use live Floci"),
]


def _client(app, service):
    return app.extensions["aws_clients"].get(service)


def _suffix():
    return uuid.uuid4().hex[:10]


def test_s3_dashboard_upload_list_metadata_download_and_confirmed_delete(app):
    client = app.test_client()
    s3 = _client(app, "s3")
    bucket = f"lc-console-it-s3-{_suffix()}"
    key = "validation/roundtrip.txt"
    expected = b"LocalCloud Console S3 integration"
    created = False
    try:
        response = client.post("/api/services/s3/buckets", json={"name": bucket})
        assert response.status_code == 201, response.json
        created = True
        response = client.post("/api/services/s3/objects", data={
            "bucket": bucket,
            "key": key,
            "file": (io.BytesIO(expected), "roundtrip.txt", "text/plain"),
        }, content_type="multipart/form-data")
        assert response.status_code == 201, response.json
        listed = client.get(f"/api/services/s3/buckets/{bucket}/objects")
        assert listed.status_code == 200 and listed.json["objects"][0]["key"] == key
        metadata = client.get(f"/api/services/s3/buckets/{bucket}/objects/{key}/metadata")
        assert metadata.status_code == 200 and metadata.json["metadata"]["content_length"] == len(expected)
        download = client.get(f"/api/services/s3/buckets/{bucket}/objects/{key}/download")
        assert download.status_code == 200 and download.data == expected
        refused = client.post("/api/services/s3/objects/delete", json={"bucket": bucket, "key": key})
        assert refused.status_code == 400
        deleted = client.post("/api/services/s3/objects/delete", json={
            "bucket": bucket, "key": key, "confirmed": True, "confirm_value": key,
        })
        assert deleted.status_code == 200
    finally:
        if created:
            for item in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=item["Key"])
            s3.delete_bucket(Bucket=bucket)
            assert bucket not in {item["Name"] for item in s3.list_buckets().get("Buckets", [])}


def test_dynamodb_dashboard_create_insert_and_retrieve(app):
    client = app.test_client()
    ddb = _client(app, "dynamodb")
    table = f"lc-console-it-ddb-{_suffix()}"
    partition_key = "integration-sample"
    created = False
    try:
        response = client.post("/api/services/dynamodb/tables", json={"name": table})
        assert response.status_code == 201, response.json
        created = True
        response = client.post("/api/services/dynamodb/items", json={
            "table": table, "pk": partition_key, "value": "Floci dashboard integration",
        })
        assert response.status_code == 201, response.json
        item = client.get("/api/services/dynamodb/items", query_string={"table": table, "pk": partition_key})
        assert item.status_code == 200
        assert item.json["item"] == {
            "pk": {"S": partition_key},
            "value": {"S": "Floci dashboard integration"},
        }
        duplicate = client.post("/api/services/dynamodb/items", json={
            "table": table, "pk": partition_key, "value": "must not overwrite",
        })
        assert duplicate.status_code == 400
    finally:
        if created:
            ddb.delete_table(TableName=table)
            ddb.get_waiter("table_not_exists").wait(TableName=table, WaiterConfig={"Delay": 1, "MaxAttempts": 20})
            assert table not in set(ddb.list_tables(Limit=100).get("TableNames", []))


def test_sqs_dashboard_create_send_receive_and_confirmed_delete(app):
    client = app.test_client()
    sqs = _client(app, "sqs")
    queue_name = f"lc-console-it-sqs-{_suffix()}"
    body = "Floci dashboard SQS integration"
    queue_url = None
    try:
        response = client.post("/api/services/sqs/queues", json={"name": queue_name})
        assert response.status_code == 201, response.json
        queue_url = response.json["resource"]["url"]
        attributes = client.get("/api/services/sqs/attributes", query_string={"queue_url": queue_url})
        assert attributes.status_code == 200 and attributes.json["attributes"].get("QueueArn")
        sent = client.post("/api/services/sqs/messages/send", json={"queue_url": queue_url, "message": body})
        assert sent.status_code == 201, sent.json
        received = client.post("/api/services/sqs/messages/receive", json={"queue_url": queue_url, "max_messages": 1})
        assert received.status_code == 200 and len(received.json["messages"]) == 1
        message = received.json["messages"][0]
        assert message["Body"] == body
        assert "ReceiptHandle" not in message and "receipt_handle" not in message
        refused = client.post("/api/services/sqs/messages/delete", json={"message_id": message["MessageId"]})
        assert refused.status_code == 400
        deleted = client.post("/api/services/sqs/messages/delete", json={
            "delete_token": message["delete_token"],
            "message_id": message["MessageId"],
            "confirmed": True,
            "confirm_value": message["MessageId"],
        })
        assert deleted.status_code == 200
        deleted_queue = client.post("/api/services/sqs/queues/delete", json={
            "queue_url": queue_url,
            "name": queue_name,
            "confirmed": True,
            "confirm_value": queue_name,
        })
        assert deleted_queue.status_code == 200, deleted_queue.json
        queue_url = None
    finally:
        if queue_url:
            sqs.delete_queue(QueueUrl=queue_url)
            remaining = sqs.list_queues().get("QueueUrls", [])
            assert all(urlsplit(url).path.rstrip("/").split("/")[-1] != queue_name for url in remaining)


def test_sns_dashboard_publish_only_to_unsubscribed_temporary_topic(app):
    client = app.test_client()
    sns = _client(app, "sns")
    topic_name = f"lc-console-it-sns-{_suffix()}"
    topic_arn = None
    try:
        response = client.post("/api/services/sns/topics", json={"name": topic_name})
        assert response.status_code == 201, response.json
        topic_arn = response.json["resource"]["arn"]
        subscriptions = client.get("/api/services/sns/subscriptions", query_string={"topic_arn": topic_arn})
        assert subscriptions.status_code == 200 and subscriptions.json["subscriptions"] == []
        published = client.post("/api/services/sns/publish", json={
            "topic_arn": topic_arn,
            "message": "Floci dashboard SNS integration",
        })
        assert published.status_code == 201 and published.json["message_id"]
        deleted = client.post("/api/services/sns/topics/delete", json={
            "topic_arn": topic_arn,
            "name": topic_name,
            "confirmed": True,
            "confirm_value": topic_name,
        })
        assert deleted.status_code == 200, deleted.json
        topic_arn = None
    finally:
        if topic_arn:
            sns.delete_topic(TopicArn=topic_arn)
            names = {
                topic["TopicArn"].rsplit(":", 1)[-1]
                for page in sns.get_paginator("list_topics").paginate()
                for topic in page.get("Topics", [])
            }
            assert topic_name not in names


def test_sns_dashboard_subscribe_to_temporary_topic(app):
    client = app.test_client()
    sns = _client(app, "sns")
    topic_name = f"lc-console-it-sns-sub-{_suffix()}"
    topic_arn = None
    try:
        created = client.post("/api/services/sns/topics", json={"name": topic_name})
        assert created.status_code == 201, created.json
        topic_arn = created.json["resource"]["arn"]

        subscribed = client.post("/api/services/sns/subscribe", json={
            "topic_arn": topic_arn,
            "protocol": "email",
            "endpoint": "console-integration@example.invalid",
        })
        assert subscribed.status_code == 201, subscribed.json

        listed = client.get("/api/services/sns/subscriptions", query_string={"topic_arn": topic_arn})
        assert listed.status_code == 200
        assert any(
            item.get("Protocol") == "email"
            and item.get("Endpoint") == "console-integration@example.invalid"
            for item in listed.json["subscriptions"]
        )
        deleted = client.post("/api/services/sns/topics/delete", json={
            "topic_arn": topic_arn,
            "name": topic_name,
            "confirmed": True,
            "confirm_value": topic_name,
        })
        assert deleted.status_code == 200, deleted.json
        topic_arn = None
    finally:
        if topic_arn:
            sns.delete_topic(TopicArn=topic_arn)


def test_sns_dashboard_delivers_to_local_sqs_subscription(app):
    client = app.test_client()
    sns = _client(app, "sns")
    sqs = _client(app, "sqs")
    topic_name = f"lc-console-it-fanout-{_suffix()}"
    queue_name = f"lc-console-it-fanout-{_suffix()}"
    topic_arn = None
    queue_url = None
    try:
        created_queue = client.post("/api/services/sqs/queues", json={"name": queue_name})
        assert created_queue.status_code == 201, created_queue.json
        queue_url = created_queue.json["resource"]["url"]
        queue_arn = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"],
        )["Attributes"]["QueueArn"]

        created_topic = client.post("/api/services/sns/topics", json={"name": topic_name})
        assert created_topic.status_code == 201, created_topic.json
        topic_arn = created_topic.json["resource"]["arn"]
        subscribed = client.post("/api/services/sns/subscribe", json={
            "topic_arn": topic_arn,
            "protocol": "sqs",
            "endpoint": queue_arn,
        })
        assert subscribed.status_code == 201, subscribed.json

        message = f"local SNS to SQS integration {uuid.uuid4().hex}"
        published = client.post("/api/services/sns/publish", json={
            "topic_arn": topic_arn,
            "message": message,
        })
        assert published.status_code == 201, published.json

        received = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=1,
        ).get("Messages", [])
        assert received and message in received[0]["Body"]
        deleted_topic = client.post("/api/services/sns/topics/delete", json={
            "topic_arn": topic_arn,
            "name": topic_name,
            "confirmed": True,
            "confirm_value": topic_name,
        })
        assert deleted_topic.status_code == 200, deleted_topic.json
        topic_arn = None
        deleted_queue = client.post("/api/services/sqs/queues/delete", json={
            "queue_url": queue_url,
            "name": queue_name,
            "confirmed": True,
            "confirm_value": queue_name,
        })
        assert deleted_queue.status_code == 200, deleted_queue.json
        queue_url = None
    finally:
        if topic_arn:
            sns.delete_topic(TopicArn=topic_arn)
        if queue_url:
            sqs.delete_queue(QueueUrl=queue_url)


def test_api_gateway_dashboard_create_inspect_and_confirmed_delete(app):
    client = app.test_client()
    api = _client(app, "apigateway")
    name = f"lc-console-it-api-{_suffix()}"
    api_id = None
    try:
        response = client.post("/api/services/api_gateway/rest", json={"name": name})
        assert response.status_code == 201, response.json
        api_id = response.json["resource"]["id"]
        resources = client.get(f"/api/services/api_gateway/rest/{api_id}/resources")
        assert resources.status_code == 200 and resources.json["resources"]
        refused = client.post("/api/services/api_gateway/rest/delete", json={"api_id": api_id, "name": name})
        assert refused.status_code == 400
        deleted = client.post("/api/services/api_gateway/rest/delete", json={
            "api_id": api_id, "name": name, "confirmed": True, "confirm_value": name,
        })
        assert deleted.status_code == 200
        remaining = api.get_rest_apis(limit=100).get("items", [])
        assert all(item.get("id") != api_id for item in remaining)
        api_id = None
    finally:
        if api_id:
            api.delete_rest_api(restApiId=api_id)


def test_api_gateway_dashboard_lists_http_apis_and_routes(app):
    client = app.test_client()
    api = _client(app, "apigatewayv2")
    lmbda = _client(app, "lambda")
    name = f"lc-console-it-http-{_suffix()}"
    function_name = f"lc-console-it-http-fn-{_suffix()}"
    api_id = None
    function_created = False
    try:
        created_function = client.post("/api/services/lambda/functions", json={
            "name": function_name,
            "runtime": "python3.12",
            "handler": "index.handler",
            "code": (
                "def handler(event, context):\n"
                "    return {'statusCode': 200, 'headers': {'content-type': 'application/json'}, "
                "'body': '{\"message\":\"hello from local Lambda\"}'}\n"
            ),
        })
        assert created_function.status_code == 201, created_function.json
        function_created = True

        created = client.post("/api/services/api_gateway/http", json={"name": name})
        assert created.status_code == 201, created.json
        api_id = created.json["resource"]["id"]

        listed_apis = client.get("/api/services/api_gateway")
        assert listed_apis.status_code == 200
        assert any(
            item["id"] == api_id
            for item in listed_apis.json["data"]["http"]["apis"]
        )

        route = client.post("/api/services/api_gateway/http/routes", json={
            "api_id": api_id,
            "route_key": "GET /hello",
            "function_name": function_name,
        })
        assert route.status_code == 201, route.json

        listed_routes = client.get(f"/api/services/api_gateway/http/{api_id}/routes")
        assert listed_routes.status_code == 200, listed_routes.json
        assert any(item["key"] == "GET /hello" for item in listed_routes.json["routes"])

        invoked = client.post("/api/services/api_gateway/http/invoke", json={
            "api_id": api_id,
            "method": "GET",
            "path": "/hello",
        })
        assert invoked.status_code == 200, invoked.json
        assert invoked.json["status_code"] == 200
        assert invoked.json["body"] == {"message": "hello from local Lambda"}
    finally:
        if api_id:
            api.delete_api(ApiId=api_id)
            remaining = api.get_apis(MaxResults="100").get("Items", [])
            assert all(item.get("ApiId") != api_id for item in remaining)
        if function_created:
            lmbda.delete_function(FunctionName=function_name)


def test_cloudwatch_dashboard_reads_real_log_events_and_cleans_temporary_group(app):
    client = app.test_client()
    logs = _client(app, "logs")
    group = f"/localcloud/integration/{_suffix()}"
    stream = "console-check"
    message = "Floci dashboard CloudWatch integration"
    created = False
    try:
        created_group = client.post("/api/services/cloudwatch/log-groups", json={"group_name": group})
        assert created_group.status_code == 201, created_group.json
        created = True
        created_stream = client.post("/api/services/cloudwatch/log-streams", json={
            "group_name": group,
            "stream_name": stream,
        })
        assert created_stream.status_code == 201, created_stream.json
        written = client.post("/api/services/cloudwatch/events", json={
            "group_name": group,
            "stream_name": stream,
            "message": message,
        })
        assert written.status_code == 201, written.json
        groups = client.get("/api/services/cloudwatch")
        assert groups.status_code == 200
        assert any(item["name"] == group for item in groups.json["data"]["log_groups"]["log_groups"])
        streams = client.get("/api/services/cloudwatch/streams", query_string={"group": group})
        assert streams.status_code == 200 and streams.json["streams"]
        events = client.get("/api/services/cloudwatch/events", query_string={"group": group, "stream": stream})
        assert events.status_code == 200
        assert any(item["message"] == message for item in events.json["events"])
    finally:
        if created:
            deleted = client.post("/api/services/cloudwatch/log-groups/delete", json={
                "group_name": group,
                "confirmed": True,
                "confirm_value": group,
            })
            assert deleted.status_code == 200, deleted.json
            remaining = logs.describe_log_groups(logGroupNamePrefix=group, limit=50).get("logGroups", [])
            assert all(item.get("logGroupName") != group for item in remaining)


def test_ec2_dashboard_launch_stop_start_and_terminate(app):
    client = app.test_client()
    ec2 = _client(app, "ec2")
    instance_ids = []
    try:
        launched = client.post("/api/services/ec2/instances", json={
            "image_id": "ami-0abcdef1234567892",
            "instance_type": "t2.micro",
            "name": f"lc-console-it-ec2-{_suffix()}",
            "count": 1,
        })
        assert launched.status_code == 201, launched.json
        instance_ids = launched.json["resource"]["instance_ids"]
        assert len(instance_ids) == 1

        ec2.get_waiter("instance_running").wait(
            InstanceIds=instance_ids,
            WaiterConfig={"Delay": 1, "MaxAttempts": 20},
        )
        connection = client.get(f"/api/services/ec2/instances/{instance_ids[0]}/connect")
        assert connection.status_code == 200, connection.json
        shell = connection.json["connection"]
        assert shell["container_name"] == f"floci-aws-ec2-{instance_ids[0]}"
        assert shell["command"] == f"docker exec -it {shell['container_name']} /bin/sh"
        result = subprocess.run(
            ["docker", "exec", shell["container_name"], "/bin/sh", "-lc", "printf LocalCloudShellReady"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "LocalCloudShellReady"

        listed = client.get("/api/services/ec2")
        assert listed.status_code == 200
        visible_ids = {inst["id"] for inst in listed.json["data"]["instances"]}
        assert instance_ids[0] in visible_ids

        stopped = client.post("/api/services/ec2/instances/stop", json={"instance_ids": instance_ids})
        assert stopped.status_code == 200, stopped.json

        started = client.post("/api/services/ec2/instances/start", json={"instance_ids": instance_ids})
        assert started.status_code == 200, started.json
    finally:
        if instance_ids:
            ec2.terminate_instances(InstanceIds=instance_ids)
            ec2.get_waiter("instance_terminated").wait(
                InstanceIds=instance_ids,
                WaiterConfig={"Delay": 1, "MaxAttempts": 20},
            )
            remaining = {
                inst["InstanceId"]: inst["State"]["Name"]
                for page in ec2.get_paginator("describe_instances").paginate()
                for reservation in page.get("Reservations", [])
                for inst in reservation.get("Instances", [])
            }
            assert all(remaining.get(iid) == "terminated" for iid in instance_ids)
def test_lambda_dashboard_create_invoke_and_confirmed_delete(app):
    client = app.test_client()
    lmbda = _client(app, "lambda")
    name = f"lc-console-it-lambda-{_suffix()}"
    created = False
    try:
        response = client.post("/api/services/lambda/functions", json={
            "name": name,
            "runtime": "python3.12",
            "handler": "index.handler",
            "code": "def handler(event, context):\n    return {'statusCode': 200, 'body': 'LocalCloud Lambda integration', 'event': event}",
            "memory_size": 128,
            "timeout": 3,
        })
        assert response.status_code == 201, response.json
        created = True

        listed = client.get("/api/services/lambda")
        assert listed.status_code == 200
        names = {fn["name"] for fn in listed.json["data"]["functions"]}
        assert name in names

        invoked = client.post("/api/services/lambda/functions/invoke", json={
            "name": name,
            "payload": {"ping": "pong"},
        })
        assert invoked.status_code == 200, invoked.json
        assert invoked.json["result"]["status_code"] == 200

        refused = client.post("/api/services/lambda/functions/delete", json={"name": name})
        assert refused.status_code == 400

        deleted = client.post("/api/services/lambda/functions/delete", json={
            "name": name,
            "confirmed": True,
            "confirm_value": name,
        })
        assert deleted.status_code == 200
        created = False
    finally:
        if created:
            lmbda.delete_function(FunctionName=name)
            remaining = {
                fn["FunctionName"]
                for page in lmbda.get_paginator("list_functions").paginate()
                for fn in page.get("Functions", [])
            }
            assert name not in remaining