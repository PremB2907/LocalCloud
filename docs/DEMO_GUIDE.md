# CCS Lab Demo Guide

## Start

Keep the existing Floci container running. In the LocalCloud workspace:

```bash
.venv/bin/python -m pip install -r requirements.txt
FLOCI_ENDPOINT_URL=http://127.0.0.1:4566 \
AWS_REGION=us-east-1 \
AWS_ACCESS_KEY_ID=test \
AWS_SECRET_ACCESS_KEY=test \
.venv/bin/python run.py
```

Open `http://127.0.0.1:5000`. The dashboard binds to loopback. Verify Floci is reachable from the Overview before making resource changes.

## Suggested Walkthrough

1. **Overview:** Show the actual endpoint/version and live resource counts. Empty counts are real API responses, not sample numbers.
2. **S3:** Create a uniquely named bucket, upload a small text file, list its object, inspect metadata, and download it. To delete the object, type its exact key into the confirmation dialog. The app refuses to overwrite an existing object key.
3. **DynamoDB:** Create a uniquely named on-demand table with String partition key `pk`; insert a sample item; retrieve it. Sample item writes are limited to tables created by the current dashboard process and duplicate keys are refused.
4. **SQS:** Create a standard queue, send a short message, receive it, and delete only the selected message by typing its exact message ID. Raw receipt handles are never shown to the browser.
5. **SNS:** Create a topic and publish a test message only while it has no subscriptions. The UI asks for `PUBLISH`; the backend rechecks subscriptions before publishing.
6. **CloudWatch:** Show live metric/log-group empty states, or inspect existing log groups, streams, and events if present. The dashboard does not generate metric data.
7. **API Gateway:** Create a REST API, inspect its resources, then delete that session-owned API by typing its exact name. Creation does not deploy a stage or make a public endpoint.
8. **Lambda:** Create a function from inline Python, invoke it, show the response, then delete the session-owned function by typing its exact name.
9. **EC2:** Create a key pair (the `.pem` downloads once), launch an Ubuntu 22.04 instance with that key pair and **Install & start OpenSSH** ticked, then click **Connect**:
   - *Browser terminal:* run `whoami`, `cd /etc && ls | head`, `uname -a`. Commands run through SSM Run Command; `cd` is remembered.
   - *SSH client:* after ~1 minute (OpenSSH install), run `chmod 400 <key>.pem` and the copied `ssh -i <key>.pem -p <port> root@127.0.0.1` command from a terminal on the same machine.
   Reboot, stop, and start the instance, then terminate it and delete the key pair. Instances are Docker containers, not public VMs.

## Cleanup

Dashboard operations create Floci resources until explicitly removed or the memory-backed Floci container is restarted. There is no delete-all action. Record the exact resource name, ARN, or URL shown by the app.

The UI exposes only selected-object deletion, selected received-message deletion, and current-process REST API, Lambda function, EC2 instance, and EC2 key pair deletion, all with exact-resource confirmation. It deliberately does not expose bucket/table/queue/topic deletion. To remove a demo-created resource, call the corresponding SDK operation with the exact recorded identifier: `s3.delete_bucket(Bucket="<exact-empty-demo-bucket>")`, `dynamodb.delete_table(TableName="<exact-demo-table>")`, `sqs.delete_queue(QueueUrl="<exact-demo-queue-url>")`, or `sns.delete_topic(TopicArn="<exact-demo-topic-arn>")`.

Do not replace those exact-resource calls with a broad list loop or delete-all operation. DynamoDB table/API ownership markers are process-local; after a dashboard restart those existing resources remain visible but ownership-restricted write/delete actions are unavailable in the app.

## Verification Commands

```bash
.venv/bin/pytest -q
RUN_FLOCI_INTEGRATION=1 .venv/bin/pytest -q -m integration
curl --fail --silent --show-error http://127.0.0.1:4566/_floci/health
```

See [docs/TEST_REPORT.md](TEST_REPORT.md) for actual results. The earlier Floci-specific S3/DynamoDB/SQS report is preserved in [docs/service-validation.md](service-validation.md).