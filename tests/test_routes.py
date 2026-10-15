from urllib.error import URLError

from botocore.exceptions import ClientError


def test_index_and_catalog_are_available(app):
    client = app.test_client()
    assert client.get("/").status_code == 200
    response = client.get("/api/catalog")
    assert response.status_code == 200
    assert set(response.json) == {
        "s3", "dynamodb", "sqs", "sns", "lambda", "ec2", "cloudwatch", "api_gateway"
    }
    operations = {
        service: {operation["name"]: operation["validation"] for operation in metadata["operations"]}
        for service, metadata in response.json.items()
    }
    assert operations["sns"]["Subscribe"] == "passed"
    assert operations["api_gateway"]["GetApis"] == "passed"
    assert operations["api_gateway"]["GetRoutes"] == "passed"
    assert operations["api_gateway"]["CreateApi"] == "passed"
    assert operations["api_gateway"]["CreateRoute"] == "passed"
    assert operations["api_gateway"]["Invoke"] == "passed"


def test_unknown_service_is_not_misreported_as_success(app):
    response = app.test_client().get("/api/services/not-a-service")
    assert response.status_code == 404
    assert response.json["error"]["code"] == "UnknownService"


def test_unavailable_floci_has_a_disconnected_response(app, monkeypatch):
    def fail_request(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr("app.routes.urlopen", fail_request)
    response = app.test_client().get("/api/health")
    assert response.status_code == 503
    assert response.json["connected"] is False
    assert "connection refused" not in response.get_data(as_text=True)


def test_one_service_error_does_not_break_other_service_pages(app, monkeypatch):
    def load(service_name):
        if service_name == "sns":
            raise ClientError(
                {
                    "Error": {"Code": "ServiceUnavailable", "Message": "private diagnostic"},
                    "ResponseMetadata": {"HTTPStatusCode": 503},
                },
                "ListTopics",
            )
        return {"buckets": []}

    monkeypatch.setattr("app.routes._load_service", load)
    client = app.test_client()
    failed = client.get("/api/services/sns")
    succeeded = client.get("/api/services/s3")
    assert failed.status_code == 503
    assert failed.json["error"]["code"] == "ServiceUnavailable"
    assert "private diagnostic" not in failed.get_data(as_text=True)
    assert succeeded.status_code == 200


def test_s3_object_delete_requires_exact_confirmation(app, monkeypatch):
    called = []
    monkeypatch.setattr("app.routes.s3.delete_object", lambda *args: called.append(args))
    client = app.test_client()
    response = client.post("/api/services/s3/objects/delete", json={"bucket": "some-bucket", "key": "file.txt"})
    assert response.status_code == 400
    assert called == []


def test_queue_url_cannot_target_external_hosts(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().get("/api/services/sqs/attributes?queue_url=http://example.com/queue")
    assert response.status_code == 400


def test_sns_publish_cannot_target_external_account(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().post("/api/services/sns/publish", json={
        "topic_arn": "arn:aws:sns:us-east-1:123456789012:external",
        "message": "test",
    })
    assert response.status_code == 400


def test_sqs_queue_delete_requires_session_ownership_and_exact_confirmation(app, monkeypatch):
    queue_url = "http://localhost:4566/000000000000/demo-queue"
    called = []

    class FakeSqsClient:
        def delete_queue(self, **kwargs):
            called.append(kwargs)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    monkeypatch.setattr("app.routes._client", lambda _name: FakeSqsClient())
    client = app.test_client()
    denied = client.post("/api/services/sqs/queues/delete", json={
        "queue_url": queue_url,
        "name": "demo-queue",
        "confirmed": True,
        "confirm_value": "demo-queue",
    })
    assert denied.status_code == 403
    assert called == []

    app.extensions["console_created_queues"].add(queue_url)
    unconfirmed = client.post("/api/services/sqs/queues/delete", json={
        "queue_url": queue_url,
        "name": "demo-queue",
        "confirmed": True,
        "confirm_value": "not-the-queue",
    })
    assert unconfirmed.status_code == 400
    assert called == []

    deleted = client.post("/api/services/sqs/queues/delete", json={
        "queue_url": queue_url,
        "name": "demo-queue",
        "confirmed": True,
        "confirm_value": "demo-queue",
    })
    assert deleted.status_code == 200
    assert called == [{"QueueUrl": queue_url}]
    assert queue_url not in app.extensions["console_created_queues"]


def test_sns_topic_delete_requires_session_ownership_and_exact_confirmation(app, monkeypatch):
    topic_arn = "arn:aws:sns:us-east-1:000000000000:demo-topic"
    called = []

    class FakeSnsClient:
        def delete_topic(self, **kwargs):
            called.append(kwargs)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    monkeypatch.setattr("app.routes._client", lambda _name: FakeSnsClient())
    client = app.test_client()
    denied = client.post("/api/services/sns/topics/delete", json={
        "topic_arn": topic_arn,
        "name": "demo-topic",
        "confirmed": True,
        "confirm_value": "demo-topic",
    })
    assert denied.status_code == 403
    assert called == []

    app.extensions["console_created_topics"].add(topic_arn)
    unconfirmed = client.post("/api/services/sns/topics/delete", json={
        "topic_arn": topic_arn,
        "name": "demo-topic",
        "confirmed": True,
        "confirm_value": "wrong-topic",
    })
    assert unconfirmed.status_code == 400
    assert called == []

    deleted = client.post("/api/services/sns/topics/delete", json={
        "topic_arn": topic_arn,
        "name": "demo-topic",
        "confirmed": True,
        "confirm_value": "demo-topic",
    })
    assert deleted.status_code == 200
    assert called == [{"TopicArn": topic_arn}]
    assert topic_arn not in app.extensions["console_created_topics"]


def test_dynamodb_item_write_is_limited_to_console_created_tables(app, monkeypatch):
    monkeypatch.setattr("app.routes.dynamodb.put_test_item", lambda *args: (_ for _ in ()).throw(AssertionError("write should not run")))
    response = app.test_client().post("/api/services/dynamodb/items", json={
        "table": "existing-user-table",
        "pk": "sample",
        "value": "test",
    })
    assert response.status_code == 403


def test_api_delete_is_limited_to_console_created_apis(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("delete should not run")))
    response = app.test_client().post("/api/services/api_gateway/rest/delete", json={
        "api_id": "existing-user-api",
        "name": "Existing user API",
        "confirmed": True,
        "confirm_value": "Existing user API",
    })
    assert response.status_code == 403


def test_cross_origin_mutation_is_blocked(app):
    response = app.test_client().post(
        "/api/services/s3/buckets",
        json={"name": "localcloud-test-bucket"},
        headers={"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "CrossOriginBlocked"


def test_non_loopback_host_is_rejected(app):
    response = app.test_client().get("/api/health", headers={"Host": "attacker.invalid"})
    assert response.status_code == 400


def test_expired_sqs_delete_token_is_rejected(app, monkeypatch):
    app.extensions["received_messages"]["expired-token"] = {
        "queue_url": "http://localhost:4566/000000000000/test",
        "receipt_handle": "not-returned-to-browser",
        "message_id": "message-1",
        "expires_at": 0,
    }
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("delete should not run")))
    response = app.test_client().post("/api/services/sqs/messages/delete", json={
        "delete_token": "expired-token",
        "message_id": "message-1",
        "confirmed": True,
        "confirm_value": "message-1",
    })
    assert response.status_code == 400
    assert "expired-token" not in app.extensions["received_messages"]


def test_sns_blocked_publish_is_recorded_in_operations_log(app, monkeypatch):
    """Verify that a publish blocked by TopicHasSubscriptions appears in the audit log."""
    from app.services.sns import TopicHasSubscriptions

    class FakeSubscribedClient:
        def list_subscriptions_by_topic(self, **kwargs):
            raise AssertionError("should not reach direct call")
        def get_paginator(self, operation):
            return self
        def paginate(self, **kwargs):
            return [{"Subscriptions": [{"Protocol": "sqs", "Endpoint": "local"}]}]
        def publish(self, **kwargs):
            raise AssertionError("publish must not be called")

    monkeypatch.setattr("app.routes._client", lambda _name: FakeSubscribedClient())
    response = app.test_client().post("/api/services/sns/publish", json={
        "topic_arn": "arn:aws:sns:us-east-1:000000000000:demo-topic",
        "message": "test message",
    })
    assert response.status_code == 400
    ops = app.extensions["recent_operations"]
    assert ops, "operation log must not be empty after a blocked publish"
    assert ops[0]["result"] == "Blocked"


def test_cloudwatch_dashboard_can_create_write_inspect_and_delete_owned_log_group(app, monkeypatch):
    class FakeLogsClient:
        def __init__(self):
            self.events = []
            self.deleted = []

        def create_log_group(self, **kwargs):
            assert kwargs == {"logGroupName": "/localcloud/test"}
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

        def create_log_stream(self, **kwargs):
            assert kwargs == {
                "logGroupName": "/localcloud/test",
                "logStreamName": "console",
            }
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

        def put_log_events(self, **kwargs):
            self.events.extend(kwargs["logEvents"])
            assert kwargs["logGroupName"] == "/localcloud/test"
            assert kwargs["logStreamName"] == "console"
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

        def delete_log_group(self, **kwargs):
            self.deleted.append(kwargs["logGroupName"])
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    logs = FakeLogsClient()
    monkeypatch.setattr("app.routes._client", lambda _name: logs)
    client = app.test_client()

    created = client.post("/api/services/cloudwatch/log-groups", json={"group_name": "/localcloud/test"})
    assert created.status_code == 201
    assert "/localcloud/test" in app.extensions["console_created_log_groups"]

    stream = client.post("/api/services/cloudwatch/log-streams", json={
        "group_name": "/localcloud/test",
        "stream_name": "console",
    })
    assert stream.status_code == 201

    written = client.post("/api/services/cloudwatch/events", json={
        "group_name": "/localcloud/test",
        "stream_name": "console",
        "message": "hello from dashboard",
    })
    assert written.status_code == 201
    assert logs.events[0]["message"] == "hello from dashboard"
    assert isinstance(logs.events[0]["timestamp"], int)

    deleted = client.post("/api/services/cloudwatch/log-groups/delete", json={
        "group_name": "/localcloud/test",
        "confirmed": True,
        "confirm_value": "/localcloud/test",
    })
    assert deleted.status_code == 200
    assert logs.deleted == ["/localcloud/test"]
    assert "/localcloud/test" not in app.extensions["console_created_log_groups"]


def test_cloudwatch_writes_are_limited_to_session_owned_log_groups(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("write should not run")))
    response = app.test_client().post("/api/services/cloudwatch/events", json={
        "group_name": "/existing/user-group",
        "stream_name": "stream",
        "message": "must not be written",
    })
    assert response.status_code == 403
    assert response.json["error"]["code"] == "LogGroupNotOwnedByConsole"


# ---------------------------------------------------------------------------
# EC2 lifecycle route safety
# ---------------------------------------------------------------------------


def test_ec2_run_instances_rejects_invalid_ami(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().post("/api/services/ec2/instances", json={
        "image_id": "not-an-ami",
        "instance_type": "t2.micro",
    })
    assert response.status_code == 400
    assert response.json["error"]["code"] == "ValidationError"


def test_ec2_run_instances_rejects_invalid_instance_type(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().post("/api/services/ec2/instances", json={
        "image_id": "ami-0abcdef1234567890",
        "instance_type": "invalid-type",
    })
    assert response.status_code == 400
    assert response.json["error"]["code"] == "ValidationError"


def test_ec2_run_instances_records_created_instances(app, monkeypatch):
    class FakeEc2Client:
        def run_instances(self, **kwargs):
            assert kwargs["ImageId"] == "ami-0abcdef1234567890"
            assert kwargs["InstanceType"] == "t2.micro"
            assert kwargs == {
                "ImageId": "ami-0abcdef1234567890",
                "InstanceType": "t2.micro",
                "MinCount": 2,
                "MaxCount": 2,
            }
            return {"Instances": [{"InstanceId": "i-abc123"}, {"InstanceId": "i-def456"}]}
        def create_tags(self, **kwargs):
            assert kwargs["Resources"] == ["i-abc123", "i-def456"]

    monkeypatch.setattr("app.routes._client", lambda _name: FakeEc2Client())
    response = app.test_client().post("/api/services/ec2/instances", json={
        "image_id": "ami-0abcdef1234567890",
        "instance_type": "t2.micro",
        "name": "demo",
        "count": 2,
    })
    assert response.status_code == 201, response.json
    assert response.json["resource"]["count"] == 2
    assert "i-abc123" in app.extensions["console_created_instances"]
    assert "i-def456" in app.extensions["console_created_instances"]


def test_ec2_connection_route_rejects_malformed_instance_id(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().get("/api/services/ec2/instances/not-an-instance/connect")
    assert response.status_code == 400


def test_ec2_connection_route_returns_local_container_shell(app, monkeypatch):
    monkeypatch.setattr(
        "app.routes.ec2.instance_shell",
        lambda _client, instance_id: {
            "instance_id": instance_id,
            "container_name": "floci-aws-ec2-i-abc123",
            "command": "docker exec -it floci-aws-ec2-i-abc123 /bin/sh",
            "note": "Local container shell",
        },
    )
    response = app.test_client().get("/api/services/ec2/instances/i-abc123/connect")
    assert response.status_code == 200
    assert response.json["connection"]["container_name"] == "floci-aws-ec2-i-abc123"
    assert response.json["connection"]["command"] == "docker exec -it floci-aws-ec2-i-abc123 /bin/sh"


def test_ec2_lifecycle_requires_instance_ids(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().post("/api/services/ec2/instances/start", json={"instance_ids": []})
    assert response.status_code == 400


def test_ec2_terminate_requires_console_ownership(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("terminate should not run")))
    response = app.test_client().post("/api/services/ec2/instances/terminate", json={
        "instance_ids": ["i-0abcdef1234567890"],
    })
    assert response.status_code == 403
    assert response.json["error"]["code"] == "InstanceNotOwnedByConsole"


def test_ec2_terminate_owned_instance_discards_ownership(app, monkeypatch):
    app.extensions["console_created_instances"].add("i-0abcdef1234567890")

    class FakeEc2Client:
        def terminate_instances(self, **kwargs):
            assert kwargs["InstanceIds"] == ["i-0abcdef1234567890"]
            return {"TerminatingInstances": [{"InstanceId": "i-0abcdef1234567890", "PreviousState": {"Name": "running"}, "CurrentState": {"Name": "shutting-down"}}]}

    monkeypatch.setattr("app.routes._client", lambda _name: FakeEc2Client())
    response = app.test_client().post("/api/services/ec2/instances/terminate", json={
        "instance_ids": ["i-0abcdef1234567890"],
    })
    assert response.status_code == 200, response.json
    assert "i-0abcdef1234567890" not in app.extensions["console_created_instances"]


# ---------------------------------------------------------------------------
# Lambda route safety
# ---------------------------------------------------------------------------


def test_lambda_create_function_rejects_invalid_runtime(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().post("/api/services/lambda/functions", json={
        "name": "demo-fn",
        "runtime": "unsupported-runtime",
        "handler": "index.handler",
        "code": "def handler(event, context): return {}",
    })
    assert response.status_code == 400


def test_lambda_create_function_rejects_invalid_handler(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("client should not be called")))
    response = app.test_client().post("/api/services/lambda/functions", json={
        "name": "demo-fn",
        "runtime": "python3.12",
        "handler": "no_dot",
        "code": "def handler(event, context): return {}",
    })
    assert response.status_code == 400


def test_lambda_create_function_packages_code_and_records_ownership(app, monkeypatch):
    class FakeLambdaClient:
        def create_function(self, **kwargs):
            assert kwargs["FunctionName"] == "demo-fn"
            assert kwargs["Runtime"] == "python3.12"
            assert kwargs["Handler"] == "index.handler"
            assert "ZipFile" in kwargs["Code"]
            return {
                "FunctionName": "demo-fn",
                "FunctionArn": "arn:aws:lambda:us-east-1:000000000000:function:demo-fn",
                "Runtime": "python3.12",
                "Handler": "index.handler",
                "State": "Pending",
            }

    monkeypatch.setattr("app.routes._client", lambda _name: FakeLambdaClient())
    response = app.test_client().post("/api/services/lambda/functions", json={
        "name": "demo-fn",
        "runtime": "python3.12",
        "handler": "index.handler",
        "code": "def handler(event, context): return {}",
    })
    assert response.status_code == 201, response.json
    assert "demo-fn" in app.extensions["console_created_functions"]


def test_lambda_delete_requires_ownership(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("delete should not run")))
    response = app.test_client().post("/api/services/lambda/functions/delete", json={
        "name": "not-owned-fn",
        "confirmed": True,
        "confirm_value": "not-owned-fn",
    })
    assert response.status_code == 403
    assert response.json["error"]["code"] == "FunctionNotOwnedByConsole"


def test_lambda_delete_requires_exact_confirmation(app, monkeypatch):
    app.extensions["console_created_functions"].add("owned-fn")
    monkeypatch.setattr("app.routes._client", lambda _name: (_ for _ in ()).throw(AssertionError("delete should not run")))
    response = app.test_client().post("/api/services/lambda/functions/delete", json={
        "name": "owned-fn",
    })
    assert response.status_code == 400
    assert response.json["error"]["code"] == "ConfirmationRequired"


def test_lambda_invoke_decodes_json_payload(app, monkeypatch):
    class FakeLambdaClient:
        def invoke(self, **kwargs):
            assert kwargs["FunctionName"] == "owned-fn"
            assert kwargs["InvocationType"] == "RequestResponse"
            from io import BytesIO
            return {
                "StatusCode": 200,
                "ExecutedVersion": "$LATEST",
                "Payload": BytesIO(b'{"statusCode": 200, "body": "ok"}'),
            }

    monkeypatch.setattr("app.routes._client", lambda _name: FakeLambdaClient())
    response = app.test_client().post("/api/services/lambda/functions/invoke", json={
        "name": "owned-fn",
        "payload": {"key": "value"},
    })
    assert response.status_code == 200, response.json
    assert response.json["result"]["payload"] == {"statusCode": 200, "body": "ok"}