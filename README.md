# LocalCloud Console

A loopback-only Flask dashboard for inspecting and operating selected services on the local Floci 2.2.0 emulator. The console is separate from the emulator; it does not restart Floci or expose its endpoint publicly.

## Prerequisites

- Ubuntu 26.04 LTS (checked on this host).
- Docker Engine and the Docker Compose v2 plugin.
- Git, to obtain the Floci source.

Docker Engine 29.1.3 and Docker Compose v2.40.3 are installed, and the Docker daemon is active. This account cannot access the Docker socket without elevation, so use `sudo` for Docker commands. Docker's [official Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/) list Ubuntu 26.04 as supported; replacing the installed engine is not needed.

## Source and Setup

- Official source: https://github.com/floci-io/floci
- Checkout: `floci/` in this workspace (shallow clone of the official repository).
- The root-level `compose.yaml` pins `floci/floci:2.2.0` to the verified multi-architecture manifest digest `sha256:e97cd0c1dc2aa14e7697fb5ef5018404c4d6345169dbd276315f0bed2c7b0520`. Docker Hub's `latest` and `2.2.0` tags resolved to this same digest when checked on 2026-10-12. The published amd64 image digest is `sha256:bb62e0e721ebbca2a476d4311f81739288b504760c9e29eeca3da2ab638198a1`; the host is `x86_64`. The running endpoint reports Floci `2.2.0`; local daemon digest inspection was unavailable because Docker requires interactive `sudo` in this session. The Compose file binds only `127.0.0.1:4566` and adds no application services.

From this workspace directory, the one containing `compose.yaml`:

The running container was started with the previous `latest` image reference. Editing Compose does not restart it. Running `docker compose up -d` with the pinned reference may recreate it; the shipped image defaults to in-memory storage, so recreation can lose resources held only in memory. No restart or recreation was performed. Inspect/export user resources before applying the new image reference.

```bash
sudo docker compose up -d
```

## Verify

Floci documents `/_floci/health` as its readiness endpoint. Run:

```bash
curl --fail --silent --show-error http://127.0.0.1:4566/_floci/health
```

An HTTP success response means the endpoint answered. Inspect container state with:

```bash
sudo docker compose ps
```

## Verification Result

Verified on 2026-10-12. The first request, made about 9 seconds after container start, reset while Docker reported `health: starting`. A later request succeeded and returned Floci version `2.2.0` with the health endpoint's service entries reporting `running`. This confirms endpoint readiness, not the compatibility of individual AWS APIs. The container was observed running with `127.0.0.1:4566` as its published port. Container logs were not captured because reading them requires interactive `sudo` authentication in the user's terminal.

## API Validation

On 2026-10-12, workspace-local Boto3 tests passed against `http://127.0.0.1:4566` using temporary resources, all of which were cleaned up:

- **S3:** created bucket `floci-val-20261012-s3-9dc472`, uploaded and downloaded `validation/roundtrip.txt`, and verified all 48 bytes matched.
- **DynamoDB:** created table `floci-val-20261012-ddb-9dc472`, inserted an item, retrieved it with a consistent read, and matched the typed item exactly.
- **SQS:** created standard queue `floci-val-20261012-sqs-9dc472`, sent and received one message, and verified its body.

Each tested operation returned HTTP 200. These are basic round-trip checks, not a claim of full AWS compatibility. See [docs/service-validation.md](docs/service-validation.md) for commands, responses, cleanup proof, and untested behavior.

## LocalCloud Console

The Flask dashboard runs independently of the Floci container and binds only to loopback. It uses the existing `.venv` and explicit Boto3 endpoint configuration. It does not alter `compose.yaml`, the Floci image, or upstream source.

Install the pinned Python dependencies into the existing environment:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Start the dashboard from the workspace root:

```bash
FLOCI_ENDPOINT_URL=http://127.0.0.1:4566 \
AWS_REGION=us-east-1 \
AWS_ACCESS_KEY_ID=test \
AWS_SECRET_ACCESS_KEY=test \
.venv/bin/python run.py
```

Open `http://127.0.0.1:5000`. The Flask process is bound to `127.0.0.1`; it is not published to the LAN. Stop the dashboard with Ctrl+C. If Floci is unavailable, the page remains available and marks the connection disconnected.

The Overview resource counts are live list/describe responses, not fixtures. On initial validation the lists were empty. Each page links to the Floci 2.2.0 service documentation and separates passed operations from documented-but-unvalidated operations.

Validated dashboard operations against this Floci instance:

- **S3:** list/create buckets; upload, list, inspect metadata, download, and confirmed delete of a selected object. Upload refuses to overwrite an existing key; file size is limited to 10 MiB.
- **DynamoDB:** list/describe tables; create a PAY_PER_REQUEST table with String `pk`; insert/retrieve a String sample item in a table created by the current dashboard process. Existing items are not overwritten.
- **SQS:** list queues and attributes; create a standard queue; send, receive, and explicitly delete one selected received message. Receipt handles stay server-side. Queues created in the current dashboard session can be deleted after typing the exact queue name.
- **SNS:** list topics/subscriptions, create topics, subscribe endpoints, publish to local SQS subscriptions, and delete topics created in the current dashboard session after typing the exact topic name. Publish is blocked unless every subscription targets an SQS queue in this Floci account and region; email/HTTP/Lambda delivery is not supported.
- **CloudWatch:** list metrics/log groups, create log groups and streams, write timestamped events, and inspect streams/events. Log groups created in the current dashboard session can be deleted after typing the exact group name. Metric reads are not validated.
- **API Gateway:** list REST and HTTP APIs; create/inspect/delete REST APIs; create HTTP APIs with a local `$default` stage, add Lambda-backed routes, and invoke them locally. Public AWS deployment is not provided.
- **Lambda:** list functions, inspect configuration, create functions from inline source (packaged into a zip), invoke synchronously, and delete functions created in this dashboard session. Lambda execution runs Docker containers and requires the Floci Docker socket to be mounted (see `compose.yaml`).
- **EC2:** list instances, launch Docker-backed instances from a Floci catalog AMI, open a root shell in a running instance's container, start/stop any visible instance, and terminate instances created in this dashboard session. EC2 execution runs Docker containers and requires the Floci Docker socket to be mounted (see `compose.yaml`). Choose **Open Shell** and run the displayed `docker exec -it floci-aws-ec2-<instance-id> /bin/sh` command in PowerShell or another terminal. This accesses the local container, not a public VM; SSH key injection is currently broken in this Floci-on-Docker-Desktop setup.

Deletes require an explicit typed confirmation and are restricted to a selected object, received message, or dashboard-created resource. Creates are collision-checked. Dashboard-created resources that have ownership-restricted actions are tracked only in the running Flask process; after a dashboard restart they remain visible but cannot be changed or deleted through those actions. User-created resources persist in Floci's current in-memory mode until deleted through a separately validated API or Floci is restarted. The dashboard never deletes everything.

Run mocked tests and opt-in live Floci integrations with:

```bash
.venv/bin/pytest -q
RUN_FLOCI_INTEGRATION=1 .venv/bin/pytest -q -m integration
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/TEST_REPORT.md](docs/TEST_REPORT.md), and [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md) for architecture, actual test results, and a lab walkthrough.

## Logs

```bash
sudo docker compose logs --tail=100 -f floci
```

## Stop and Restart

Stop and remove the container:

```bash
sudo docker compose down
```

Restart the existing container:

```bash
sudo docker compose restart floci
```

## Known Limitations

- This account requires `sudo` to access Docker, including for logs and container inspection.
- Floci's documented default storage mode is in-memory. This Compose setup has no persistence volume, so container removal loses emulator state.
- The Docker socket is now mounted in `compose.yaml` so EC2 (Docker-backed instances) and Lambda (container execution) can run. Without the socket, EC2 launch and Lambda invocation fail.
- S3 virtual-hosted addressing, DynamoDB features beyond the tested String partition-key path, SQS FIFO behavior, SNS delivery to subscribers, CloudWatch metrics reads, API Gateway deployment/invocation, Lambda versions/aliases/layers, EC2 networking/security groups beyond the Floci defaults, IAM enforcement, and other service operations remain unvalidated.

## Persistence Proposal (Not Active)

The Floci 2.2.0 [storage guide](https://github.com/floci-io/floci/blob/2.2.0/docs/configuration/storage.md) says the published image defaults to `FLOCI_STORAGE_MODE=memory`. Its `persistent` mode writes changes synchronously, with the persistent data path set to `/app/data`. A named volume avoids binding over workspace files. This is a proposal only; it is not enabled in the active Compose file:

```yaml
services:
  floci:
    environment:
      FLOCI_STORAGE_MODE: persistent
      FLOCI_STORAGE_PERSISTENT_PATH: /app/data
    volumes:
      - floci-data:/app/data

volumes:
  floci-data:
```

Before enabling it, inspect/export resources in the currently running memory-backed container and inspect the Compose project's `floci-data` volume for existing data. Recreating the container does not automatically migrate in-memory resources into the volume. Do not use `docker compose down -v` or prune this named volume if its data must be retained. No volume was created or attached for this proposal.