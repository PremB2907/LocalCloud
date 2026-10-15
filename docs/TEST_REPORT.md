# LocalCloud Console Test Report

**Date:** 2026-10-15 (session-owned mutation guards, operations view, documentation)  
**Workspace:** `/home/prem/Prem Projects/LocalCloud`  
**Floci:** endpoint `http://127.0.0.1:4566` was not reachable during this session (`curl` connection refused). Docker socket access required `sudo` and was unavailable from this environment, so the container was not started.

## Environment

- Ubuntu Linux; Python from workspace `.venv`.
- Boto3 1.43.111, Flask 3.1.3, and pytest 8.4.2.
- Browser tooling was available for UI shell checks against a locally started Flask process. Live Floci-backed UI mutations were not executed.

## Automated Tests

Baseline before this session's edits (mocked unit tests):

```text
.venv/bin/pytest -q
23 passed, 7 skipped
```

After this session's edits:

```text
.venv/bin/pytest -q
29 passed, 7 skipped
```

```text
node --check app/static/app.js
.venv/bin/python -m compileall -q app run.py tests
```

JavaScript syntax check and Python compileall both succeeded.

Live Floci integration tests were **not executed** in this session because Floci was not reachable:

```text
RUN_FLOCI_INTEGRATION=1 .venv/bin/pytest -q -m integration
```

Historical 2026-10-12 result (not re-run): `7 passed, 17 deselected`.

Skipped tests are the seven `@pytest.mark.integration` cases that require `RUN_FLOCI_INTEGRATION=1`.

## New unit coverage (2026-10-15)

Ownership guards were unit-tested without calling Floci:

- S3 empty-bucket delete refuses pre-existing buckets (`403`).
- SQS queue delete refuses pre-existing queues (`403`).
- SNS topic delete refuses pre-existing topics (`403`).
- DynamoDB scan refuses tables not created in this session (`403`).
- Lambda invoke refuses functions not created in this session (`403`).
- EC2 start refuses instances not launched in this session (`403`).

## Browser Checks

Flask was started on `127.0.0.1:5000` with Floci disconnected. Verification covered the dashboard shell (navigation, command palette, theme toggle, operations page, service empty/error presentation) rather than live resource mutations. Results are recorded in the session delivery report.

## Remaining Gaps

Lambda create/invoke/delete and EC2 lifecycle remain unvalidated against live Floci. Session-owned bucket/table/queue/topic/log-group deletion is implemented and unit-tested for ownership, but not live-integration tested in this session. CloudWatch metric statistics, API Gateway deployment/invocation, SNS subscriber delivery, SQS FIFO, and IAM enforcement remain unvalidated.
