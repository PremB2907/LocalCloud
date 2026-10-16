import pytest
from botocore.exceptions import ClientError

from app.services.dynamodb import put_test_item
from app.services.s3 import upload_object
from app.services.sns import TopicHasSubscriptions, publish_to_local_sqs


class ExistingS3Object:
    def __init__(self):
        self.put_called = False

    def list_buckets(self):
        return {"Buckets": [{"Name": "owned-bucket"}]}

    def head_object(self, **kwargs):
        return {"ContentLength": 5}

    def put_object(self, **kwargs):
        self.put_called = True


def test_s3_upload_refuses_to_overwrite_existing_key():
    client = ExistingS3Object()
    with pytest.raises(ValueError, match="was not overwritten"):
        upload_object(client, "owned-bucket", "occupied.txt", b"new", "text/plain")
    assert client.put_called is False


class ExistingDynamoItem:
    def __init__(self):
        self.put_called = False

    def describe_table(self, **kwargs):
        return {"Table": {
            "TableStatus": "ACTIVE",
            "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"}],
        }}

    def get_item(self, **kwargs):
        return {"Item": {"pk": {"S": "occupied"}}}

    def put_item(self, **kwargs):
        self.put_called = True


def test_dynamodb_sample_write_refuses_to_overwrite_existing_key():
    client = ExistingDynamoItem()
    with pytest.raises(ValueError, match="was not overwritten"):
        put_test_item(client, "owned-table", "occupied", "new")
    assert client.put_called is False


class SubscribedTopic:
    def __init__(self, subscriptions):
        self.publish_called = False
        self.subscriptions = subscriptions

    def get_paginator(self, operation):
        assert operation == "list_subscriptions_by_topic"
        return self

    def paginate(self, **kwargs):
        return [{"Subscriptions": self.subscriptions}]

    def publish(self, **kwargs):
        self.publish_called = True
        return {"MessageId": "message-1"}


def test_sns_publish_skips_localcloud_email_subscription():
    client = SubscribedTopic([{"Protocol": "email", "Endpoint": "user@example.com"}])
    result = publish_to_local_sqs(
        client,
        "arn:aws:sns:us-east-1:000000000000:topic",
        "test",
        "us-east-1",
    )
    assert result["MessageId"] == "message-1"
    assert client.publish_called is True


def test_sns_publish_refuses_unsupported_nonlocal_subscription():
    client = SubscribedTopic([{"Protocol": "http", "Endpoint": "https://example.com/hook"}])
    with pytest.raises(TopicHasSubscriptions):
        publish_to_local_sqs(client, "arn:aws:sns:us-east-1:000000000000:topic", "test", "us-east-1")
    assert client.publish_called is False


def test_sns_publish_allows_local_sqs_subscription():
    client = SubscribedTopic([{
        "Protocol": "sqs",
        "Endpoint": "arn:aws:sqs:us-east-1:000000000000:local-queue",
    }])
    result = publish_to_local_sqs(
        client,
        "arn:aws:sns:us-east-1:000000000000:topic",
        "test",
        "us-east-1",
    )
    assert result["MessageId"] == "message-1"
    assert client.publish_called is True