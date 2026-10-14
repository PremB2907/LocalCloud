from app.services.common import isoformat


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