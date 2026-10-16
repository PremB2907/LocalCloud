import base64
import json
from decimal import Decimal, InvalidOperation

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from app.services.common import isoformat


_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def _convert_json_numbers(value):
    if isinstance(value, float):
        try:
            return Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError("Item numbers must be finite JSON numbers.") from error
    if isinstance(value, list):
        return [_convert_json_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _convert_json_numbers(item) for key, item in value.items()}
    return value


def serialize_item(item):
    if not isinstance(item, dict) or not item:
        raise ValueError("An item must be a non-empty JSON object.")
    normalized = _convert_json_numbers(item)
    if not isinstance(normalized.get("pk"), str) or not normalized["pk"]:
        raise ValueError("Items must include a non-empty String attribute named pk.")
    try:
        return {name: _SERIALIZER.serialize(value) for name, value in normalized.items()}
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Item contains a value that DynamoDB cannot store: {error}") from error


def deserialize_item(item):
    def json_safe(value):
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        if isinstance(value, list):
            return [json_safe(part) for part in value]
        if isinstance(value, dict):
            return {key: json_safe(part) for key, part in value.items()}
        return value

    return json_safe({name: _DESERIALIZER.deserialize(value) for name, value in item.items()})


def create_test_table(client, table_name):
    existing = set(client.list_tables(Limit=100).get("TableNames", []))
    if table_name in existing:
        raise ValueError("A table with this name already exists; it was left unchanged.")
    return client.create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


def list_tables(client):
    paginator = client.get_paginator("list_tables")
    names = []
    for page in paginator.paginate(Limit=100):
        names.extend(page.get("TableNames", []))
    return [{"name": name} for name in names]


def put_test_item(client, table_name, partition_key, value):
    table = client.describe_table(TableName=table_name)["Table"]
    if table.get("TableStatus") != "ACTIVE":
        raise ValueError("Table is not ACTIVE.")
    schema = table.get("KeySchema", [])
    definitions = {item["AttributeName"]: item["AttributeType"] for item in table.get("AttributeDefinitions", [])}
    if schema != [{"AttributeName": "pk", "KeyType": "HASH"}] or definitions.get("pk") != "S":
        raise ValueError("Dashboard test items are supported only on a String pk table.")
    existing = client.get_item(
        TableName=table_name,
        Key={"pk": {"S": partition_key}},
        ConsistentRead=True,
    ).get("Item")
    if existing:
        raise ValueError("An item with this partition key already exists; it was not overwritten.")
    return client.put_item(
        TableName=table_name,
        Item={"pk": {"S": partition_key}, "value": {"S": value}},
    )


def get_test_item(client, table_name, partition_key):
    return client.get_item(
        TableName=table_name,
        Key={"pk": {"S": partition_key}},
        ConsistentRead=True,
    )


def put_json_item(client, table_name, item, overwrite=False):
    typed_item = serialize_item(item)
    if not overwrite:
        existing = client.get_item(
            TableName=table_name,
            Key={"pk": typed_item["pk"]},
            ConsistentRead=True,
        ).get("Item")
        if existing:
            raise ValueError("An item with this partition key already exists. Confirm replacement to overwrite it.")
    return client.put_item(TableName=table_name, Item=typed_item)


def scan_items(client, table_name, next_token=None, limit=100):
    options = {"TableName": table_name, "Limit": limit}
    if next_token:
        try:
            decoded = base64.urlsafe_b64decode(next_token.encode("ascii"), validate=True)
            options["ExclusiveStartKey"] = json.loads(decoded)
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("The DynamoDB scan cursor is invalid.") from error
    response = client.scan(**options)
    last_key = response.get("LastEvaluatedKey")
    cursor = None
    if last_key:
        encoded = json.dumps(last_key, separators=(",", ":")).encode("utf-8")
        cursor = base64.urlsafe_b64encode(encoded).decode("ascii")
    return {
        "items": [deserialize_item(item) for item in response.get("Items", [])],
        "next_token": cursor,
    }


def delete_item(client, table_name, partition_key):
    return client.delete_item(TableName=table_name, Key={"pk": {"S": partition_key}})


def delete_table(client, table_name):
    return client.delete_table(TableName=table_name)


def describe_table(client, table_name):
    table = client.describe_table(TableName=table_name)["Table"]
    return {
        "name": table.get("TableName"),
        "status": table.get("TableStatus"),
        "item_count": table.get("ItemCount"),
        "table_arn": table.get("TableArn"),
        "key_schema": table.get("KeySchema", []),
        "attribute_definitions": table.get("AttributeDefinitions", []),
        "billing_mode": table.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED"),
        "creation_date": isoformat(table.get("CreationDateTime")),
    }