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


def test_s3_bucket_delete_is_limited_to_console_created_buckets(app, monkeypatch):
    monkeypatch.setattr("app.routes.s3.delete_bucket", lambda *args: (_ for _ in ()).throw(AssertionError("delete should not run")))
    response = app.test_client().post("/api/services/s3/buckets/delete", json={
        "name": "preexisting-bucket",
        "confirmed": True,
        "confirm_value": "preexisting-bucket",
    })
    assert response.status_code == 403


def test_sqs_queue_delete_is_limited_to_console_created_queues(app, monkeypatch):
    monkeypatch.setattr("app.routes.sqs.delete_queue", lambda *args: (_ for _ in ()).throw(AssertionError("delete should not run")))
    response = app.test_client().post("/api/services/sqs/queues/delete", json={
        "queue_url": "http://127.0.0.1:4566/000000000000/preexisting-queue",
        "confirmed": True,
        "confirm_value": "preexisting-queue",
    })
    assert response.status_code == 403


def test_sns_topic_delete_is_limited_to_console_created_topics(app, monkeypatch):
    monkeypatch.setattr("app.routes.sns.delete_topic", lambda *args: (_ for _ in ()).throw(AssertionError("delete should not run")))
    response = app.test_client().post("/api/services/sns/topics/delete", json={
        "topic_arn": "arn:aws:sns:us-east-1:000000000000:preexisting-topic",
        "confirmed": True,
        "confirm_value": "preexisting-topic",
    })
    assert response.status_code == 403


def test_dynamodb_scan_is_limited_to_console_created_tables(app, monkeypatch):
    monkeypatch.setattr("app.routes.dynamodb.scan_table", lambda *args: (_ for _ in ()).throw(AssertionError("scan should not run")))
    response = app.test_client().get("/api/services/dynamodb/tables/existing-user-table/scan")
    assert response.status_code == 403


def test_lambda_invoke_is_limited_to_console_created_functions(app, monkeypatch):
    monkeypatch.setattr("app.routes.lambda_functions.invoke_function", lambda *args: (_ for _ in ()).throw(AssertionError("invoke should not run")))
    response = app.test_client().post("/api/services/lambda/functions/ExistingFn/invoke", json={"payload": {}})
    assert response.status_code == 403


def test_ec2_start_is_limited_to_console_created_instances(app, monkeypatch):
    monkeypatch.setattr("app.routes.ec2.start_instances", lambda *args: (_ for _ in ()).throw(AssertionError("start should not run")))
    response = app.test_client().post("/api/services/ec2/instances/start", json={"instance_ids": ["i-0123456789abcdef0"]})
    assert response.status_code == 403