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

Focused live follow-up (2026-10-15):

```text
test_index_and_catalog_are_available
test_sns_dashboard_subscribe_to_temporary_topic
test_api_gateway_dashboard_lists_http_apis_and_routes
3 passed
```

This follow-up verified the catalog statuses for SNS `Subscribe` and API Gateway `GetApis`/`GetRoutes`/`CreateApi`, created and listed an email subscription on a temporary SNS topic, and created, listed, and inspected routes for a temporary HTTP API. The temporary topic and API were deleted after the checks. API invocation remains unvalidated.

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
| SQS | List queues; create standard queue; attributes; send; receive; body match; exact-message confirmed delete | Initial inventory empty. Receipt handle stayed server-side in app tests. FIFO was not tested. |
| SNS | List topics; create temporary topic; verify no subscriptions; publish; create an email subscription; list the subscription; delete topic | Subscription creation and inventory were verified. Email delivery/fan-out was not tested. |
| Lambda | `ListFunctions`; empty-function response; sanitized not-found response for configuration lookup | No functions existed. Successful configuration inspection and invocation were not tested. No invocation control is exposed. |
| EC2 | `DescribeInstances` | Zero instances. Initial 8-second read timed out; the same call passed with a 60-second read timeout. `RunInstances`/lifecycle was not attempted. Floci models EC2 as Docker containers, not public VMs. |
| CloudWatch | `ListMetrics`, `DescribeLogGroups`; temporary group/stream create; log write/read; dashboard stream/event inspection; delete/verify | Initial metrics/log inventories were empty. No metric-statistics read was validated. Temporary log data was removed. |
| API Gateway | List REST/HTTP APIs; create temporary REST and HTTP APIs; inspect REST root resource and HTTP routes; confirmed REST delete; delete HTTP API; verify absence | HTTP API list and route inspection were verified. No API was deployed or invoked. |

Initial read-only probes for each service area returned HTTP 200. At that time each inventory returned zero resources; EC2 needed the longer timeout noted above. These checks do not imply full AWS compatibility.

## Browser Checks

- Overview showed Floci `2.2.0`, live counts, actual empty states, and validation evidence.
- S3 bucket creation/upload/list/metadata/download/delete was exercised. The download route returned HTTP 200; the integration test verified the exact response bytes. Exact-key confirmation remained disabled until the requested key was typed.
- DynamoDB table creation, item insertion, and retrieval returned the expected typed item.
- SQS queue creation, send, receive, and delete passed. The browser received no raw receipt handle; deletion required the exact message ID.
- SNS topic creation/publish passed with the `PUBLISH` typed confirmation and server-side no-subscription check.
- API Gateway REST create/resource inspection/delete passed with exact-name confirmation. No deployment was created.
- CloudWatch Logs stream/event inspection displayed the actual temporary event.
- At 390px viewport width, the navigation drawer worked and the page had no horizontal overflow.

All browser-created resources were cleaned up after verification, and the temporary upload file was removed. Floci health remained HTTP 200.

## Remaining Gaps

Lambda inventory is real, but no function existed for a successful configuration read or invocation. Floci Lambda execution requires the Docker socket, which the active Compose file intentionally does not mount. EC2 creation/lifecycle was not attempted; Floci's EC2 model is container-backed. CloudWatch metric statistics were not tested because no metrics existed. API Gateway deployment and invocation remain unvalidated. IAM enforcement, S3 virtual-hosted addressing, SQS FIFO, SNS subscriber delivery, pagination with large inventories, and full service parity were not tested.