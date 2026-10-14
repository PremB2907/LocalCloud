import pytest
from botocore.exceptions import ClientError

from app.services.dynamodb import put_test_item
from app.services.s3 import upload_object
from app.services.sns import TopicHasSubscriptions, publish_if_unsubscribed


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
    def __init__(self):
        self.publish_called = False

    def get_paginator(self, operation):
        assert operation == "list_subscriptions_by_topic"
        return self

    def paginate(self, **kwargs):
        return [{"Subscriptions": [{"Protocol": "sqs", "Endpoint": "local-only"}]}]

    def publish(self, **kwargs):
        self.publish_called = True


def test_sns_publish_refuses_any_subscribed_topic():
    client = SubscribedTopic()
    with pytest.raises(TopicHasSubscriptions):
        publish_if_unsubscribed(client, "arn:aws:sns:us-east-1:000000000000:topic", "test")
    assert client.publish_called is False