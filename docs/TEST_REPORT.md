# LocalCloud Console Test Report

**Date:** 2026-10-13 (Phase 3 security hardening + Phase F premium UI transformation)
**Workspace:** `/home/prem/Prem Projects/LocalCloud`  
**Floci:** 2.2.0 at `http://127.0.0.1:4566`

## Environment

- Ubuntu 26.04.1 LTS; Python 3.14.4.
- Docker Engine 29.1.3 and Compose 2.40.3. Floci was not running during this audit session (stopped since prior session).
- AWS CLI is absent. Boto3 1.43.111, Flask 3.1.3, and pytest 8.4.2 are installed in the workspace `.venv`.
- Service contracts were reviewed in the official Floci repository's `2.2.0` documentation for all eight services, plus storage documentation.

## Automated Tests

Mocked unit tests (2026-10-13 run):

```text
.venv/bin/pytest -q
23 passed, 7 skipped
```

One new test was added in this session (`test_sns_blocked_publish_is_recorded_in_operations_log`).

Full follow-up on 2026-10-15 after the service usability changes: **42 passed, 12 skipped**. The opt-in live Floci integration file also passed: **11 passed**, covering create/use/cleanup flows for the eight dashboard services.

Focused live follow-up (2026-10-15):

```text
test_index_and_catalog_are_available
test_sns_dashboard_subscribe_to_temporary_topic
test_api_gateway_dashboard_lists_http_apis_and_routes
3 passed
```

This follow-up verified the catalog statuses for SNS `Subscribe` and API Gateway `GetApis`/`GetRoutes`/`CreateApi`, created and listed an email subscription on a temporary SNS topic, and created, listed, and inspected routes for a temporary HTTP API. The temporary topic and API were deleted after the checks. The subsequent 2026-10-15 integration run also verified local HTTP API invocation through a Lambda route.

Live Floci integration tests (from 2026-10-12 baseline — Floci not running in this session):

```text
RUN_FLOCI_INTEGRATION=1 .venv/bin/pytest -q -m integration
7 passed, 17 deselected
```

The seven live tests cover all eight read-only service routes and workflow cases for S3, DynamoDB, SQS, SNS, API Gateway REST, and CloudWatch Logs. They use unique names, check collisions, and verify resource cleanup/absence. No mocked success response is used by the live integration cases.

## Per-Service Evidence

| Service | Live operations verified | Actual inventory / limits |
|---|---|---|
| S3 | List/create bucket; upload; list objects; `HeadObject`; download/content equality; exact-key confirmed delete | Initial inventory empty. Path-style only was tested. Upload refuses to overwrite an existing object key. |
| DynamoDB | `ListTables`, `DescribeTable`, create PAY_PER_REQUEST String-`pk` table, insert, consistent retrieve, duplicate-key refusal | Initial inventory empty. One simple String-key/item path only. |
| SQS | List queues; create standard queue; attributes; send; receive; body match; exact-message confirmed delete; typed, session-owned queue deletion | Initial inventory empty. Receipt handle stayed server-side in app tests. FIFO was not tested. |
| SNS | List topics; create temporary topic; publish with no subscriptions; create/list email subscription; publish to a local SQS subscription; typed topic deletion | Local SQS fan-out is verified. Email subscription creation is verified, but email delivery is not supported by the dashboard. |
| Lambda | Create inline-source function; inspect configuration; invoke; typed, session-owned deletion | One local function completed its invocation and cleanup. Docker socket access is required. |
| EC2 | `DescribeInstances`; `RunInstances` with `KeyName` and OpenSSH user data; stop/start/reboot; `CreateKeyPair`/`DescribeKeyPairs`/confirmed `DeleteKeyPair`; Connect via SSM `SendCommand` + `GetCommandInvocation` (stdout, stderr, exit code, remembered `cwd`); SSH to the published host port with the generated key; `docker exec` shell command; terminate | Floci models EC2 as Docker containers, not public VMs. SSH needs a key pair at launch and an image with OpenSSH (installed via user data); the SSH host port is read with the Docker CLI. Interactive terminal programs are not supported by the browser terminal. SSM commands fail on Alpine (`ami-alpine`) because Floci 2.2.0 uses GNU `timeout --kill-after`, which BusyBox lacks. |
| CloudWatch | `ListMetrics`, `DescribeLogGroups`; dashboard group/stream/event create/write/read; typed, session-owned group deletion | Initial metrics/log inventories were empty. No metric-statistics read was validated. Temporary log data was removed. |
| API Gateway | List REST/HTTP APIs; create/inspect/delete REST API; create HTTP API and Lambda route; inspect route; invoke; delete API | HTTP API invocation returned the expected Lambda response. This is a local Floci `$default` stage, not a public AWS deployment. |

Initial read-only probes for each service area returned HTTP 200. At that time each inventory returned zero resources; EC2 needed the longer timeout noted above. These checks do not imply full AWS compatibility.

Live EC2 shell workflow (`test_ec2_dashboard_launch_stop_start_and_terminate`, 2026-10-15): passed. The test launched a temporary instance, verified the returned `docker exec` shell command by running a command inside that instance's container, then stopped, restarted, and terminated it.

Live SQS/SNS and CloudWatch dashboard workflows (2026-10-15): local SNS-to-SQS delivery passed, including confirmed cleanup of both resources; CloudWatch dashboard group/stream/event creation, readback, and confirmed cleanup passed.

## Browser Checks

- Overview showed Floci `2.2.0`, live counts, actual empty states, and validation evidence.
- S3 bucket creation/upload/list/metadata/download/delete was exercised. The download route returned HTTP 200; the integration test verified the exact response bytes. Exact-key confirmation remained disabled until the requested key was typed.
- DynamoDB table creation, item insertion, and retrieval returned the expected typed item.
- SQS queue creation, send, receive, and delete passed. The browser received no raw receipt handle; deletion required the exact message ID.
- SNS topic creation/publish passed with the `PUBLISH` typed confirmation and server-side no-subscription check.
- API Gateway REST create/resource inspection/delete passed with exact-name confirmation; the HTTP API/Lambda route invoke workflow returned the expected local response.
- CloudWatch Logs stream/event inspection displayed the actual temporary event.
- EC2 Connect: the browser terminal ran `cd /etc && ls | head -3` and `pwd; uname -a` in an Ubuntu 22.04 instance with the prompt following the working directory; the SSH tab showed `ssh -i <key>.pem -p 2200 root@127.0.0.1`, and that command logged in to the instance.
- At 390px viewport width, the navigation drawer worked and the page had no horizontal overflow.

All browser-created resources were cleaned up after verification, and the temporary upload file was removed. Floci health remained HTTP 200.

## Remaining Gaps

EC2 is container-backed rather than a full virtual machine, and SSH key injection failed on this Docker Desktop setup; use the dashboard's local Docker shell command instead. CloudWatch metric statistics were not tested because no metrics existed. API Gateway REST deployment remains unvalidated. IAM enforcement, S3 virtual-hosted addressing, SQS FIFO, SNS email/HTTP/Lambda delivery, pagination with large inventories, and full service parity were not tested.