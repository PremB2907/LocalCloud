# Floci 2.2.0 Service Validation

**Date:** 2026-10-12  
**Endpoint:** `http://127.0.0.1:4566`

## Scope and Client

This validates one basic create/write/read path for S3, DynamoDB, and SQS against the running local Floci instance. It does not validate the full AWS API surface or connect to AWS.

- Floci health endpoint returned HTTP 200 before and after the tests.
- AWS CLI was not installed (`command -v aws` returned not found); no AWS CLI commands were run.
- System Python was 3.14.4 and did not have Boto3 installed. Boto3 1.43.111 (Botocore 1.43.111) was installed in the workspace-local `.venv`; no system Python packages were changed.
- All SDK clients used region `us-east-1`, access key `test`, secret key `test`, and the local endpoint above. S3 used path-style addressing.
- Resource names below include the validation date and a unique run suffix. Each test checked for a name collision before creation.

The availability checks were `command -v aws`, `aws --version`, and `python3 -c 'import sys; print(sys.version); import boto3; print("boto3", boto3.__version__)'`. The AWS CLI was not found, and the initial system-Python Boto3 import raised `ModuleNotFoundError`. The isolated SDK was prepared with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install boto3
```

Each inline Python test created its service client with `endpoint_url="http://127.0.0.1:4566"`, `region_name="us-east-1"`, `aws_access_key_id="test"`, and `aws_secret_access_key="test"`. The S3 client additionally used `Config(s3={"addressing_style": "path"})`. Test payloads were held in memory; no local data files were created.

## Official Floci 2.2.0 Documentation

- [Installation and Docker image](https://github.com/floci-io/floci/blob/2.2.0/docs/getting-started/installation.md)
- [S3 operations and addressing](https://github.com/floci-io/floci/blob/2.2.0/docs/services/s3.md)
- [DynamoDB actions and backend](https://github.com/floci-io/floci/blob/2.2.0/docs/services/dynamodb.md)
- [SQS actions and queue URL format](https://github.com/floci-io/floci/blob/2.2.0/docs/services/sqs.md)

The 2.2.0 service references list the operations exercised below as supported. DynamoDB's documented default backend is `native`; no backend override was made.

## S3

**Temporary resources:** bucket `floci-val-20261012-s3-9dc472`; object `validation/roundtrip.txt`.

The Boto3 calls used were:

```python
s3.create_bucket(Bucket="floci-val-20261012-s3-9dc472")
s3.put_object(Bucket="floci-val-20261012-s3-9dc472", Key="validation/roundtrip.txt", Body=expected)
response = s3.get_object(Bucket="floci-val-20261012-s3-9dc472", Key="validation/roundtrip.txt")
assert response["Body"].read() == expected
s3.delete_object(Bucket="floci-val-20261012-s3-9dc472", Key="validation/roundtrip.txt")
s3.delete_bucket(Bucket="floci-val-20261012-s3-9dc472")
```

**Results:** `CreateBucket` HTTP 200; `PutObject` HTTP 200; `GetObject` HTTP 200; downloaded content matched the uploaded 48 bytes exactly. Cleanup of the test object and bucket succeeded.

## DynamoDB

**Temporary resource:** table `floci-val-20261012-ddb-9dc472`, partition key `pk` (String).

The table was created with on-demand billing, awaited until `ACTIVE`, and tested with these Boto3 calls:

```python
ddb.create_table(TableName="floci-val-20261012-ddb-9dc472", AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}], KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}], BillingMode="PAY_PER_REQUEST")
ddb.get_waiter("table_exists").wait(TableName="floci-val-20261012-ddb-9dc472")
ddb.put_item(TableName="floci-val-20261012-ddb-9dc472", Item=expected_item)
response = ddb.get_item(TableName="floci-val-20261012-ddb-9dc472", Key={"pk": {"S": "roundtrip-test"}}, ConsistentRead=True)
assert response["Item"] == expected_item
ddb.delete_table(TableName="floci-val-20261012-ddb-9dc472")
```

`expected_item` was `{"pk":{"S":"roundtrip-test"},"message":{"S":"Floci 2.2.0 DynamoDB round-trip validation"},"count":{"N":"7"}}`.

**Results:** `CreateTable` HTTP 200 and returned `ACTIVE`; the waiter and subsequent `DescribeTable` confirmed `ACTIVE`; `PutItem` HTTP 200; consistent `GetItem` HTTP 200 and returned the exact typed item. `DeleteTable` returned HTTP 200 and the table-not-exists waiter completed.

## SQS

**Temporary resource:** standard queue `floci-val-20261012-sqs-9dc472`.

The Boto3 calls used were:

```python
created = sqs.create_queue(QueueName="floci-val-20261012-sqs-9dc472")
queue_url = created["QueueUrl"]
sent = sqs.send_message(QueueUrl=queue_url, MessageBody=expected_body)
received = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=2)
assert received["Messages"][0]["Body"] == expected_body
sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=received["Messages"][0]["ReceiptHandle"])
sqs.delete_queue(QueueUrl=queue_url)
```

`expected_body` was `Floci 2.2.0 SQS round-trip validation 2026-10-12`.

**Results:** `CreateQueue`, `SendMessage`, `ReceiveMessage`, `DeleteMessage`, and `DeleteQueue` each returned HTTP 200; receive returned one message with an exact body match. Floci returned queue URL `http://localhost:4566/000000000000/floci-val-20261012-sqs-9dc472`; the SDK client itself was configured with the requested `127.0.0.1` endpoint, and the returned localhost URL also reached the local emulator.

## Cleanup and Limitations

A final read-only check confirmed the exact temporary bucket, table, and queue were absent. No other resources were deleted or modified.

These tests establish that the specific S3 bucket/object round trip, DynamoDB table/item round trip, and standard SQS queue/message round trip worked on this Floci instance. They do not establish full AWS compatibility. S3 path-style access was tested; virtual-hosted access was not. DynamoDB tested the default native backend and one simple String partition key only. SQS tested a standard queue with one short-poll receive; FIFO behavior and other queue attributes were not tested. IAM enforcement, error parity, retries, concurrency, persistence across restart, and other service operations remain unvalidated. No operation failed during these three tests; the only initial tooling gap was the absent AWS CLI/system Boto3, addressed by using the workspace-local Boto3 environment.