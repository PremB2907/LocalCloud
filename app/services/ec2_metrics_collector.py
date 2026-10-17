"""EC2 instance metrics collector for LocalCloud.

Floci 2.2.0 does not auto-emit EC2 CloudWatch metrics (CPU, network, disk).
LocalCloud therefore optionally collects those measurements from the
underlying Docker containers backing Floci EC2 instances and publishes them
through the existing CloudWatch ``PutMetricData`` adapter.

Measurements are host/container readings, not native AWS EC2 metrics. They are
published with the ``InstanceId`` dimension so they associate with the correct
EC2 instance, and are clearly distinguishable from emulator-native metrics.
"""

import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone

from app.services.cloudwatch import put_metric_data


class Ec2MetricsCollector:
    """Collects container-level metrics for Floci EC2 instances.

    Uses ``docker stats`` (no stream) to read CPU, memory, network, and block
    I/O for each running EC2 container, then publishes the readings to
    CloudWatch Metrics with namespace ``LocalCloud/EC2`` and an
    ``InstanceId`` dimension.

    The collector runs in a background thread and respects ``interval`` and
    ``max_instances`` limits. It is a no-op when the Docker CLI is unavailable
    or when collection is not enabled.
    """

    NAMESPACE = "LocalCloud/EC2"

    def __init__(self, get_ec2_client=None, interval_seconds=60, max_instances=10):
        self.get_ec2_client = get_ec2_client
        self.interval_seconds = max(10, int(interval_seconds))
        self.max_instances = max(1, int(max_instances))
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.enabled = False
        self.last_error = None
        self.collections = 0
        self.instances_collected = 0
        self.started_at = None

    # ------------------------------------------------------------------ lifecycle

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return  # already running; avoid duplicate collectors
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="localcloud-ec2-metrics")
            self._thread.start()
            self.enabled = True
            self.started_at = time.time()

    def stop(self, timeout=5):
        with self._lock:
            self.enabled = False
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive() and self.enabled

    # ------------------------------------------------------------------ collection loop

    def _run(self):
        while not self._stop.is_set():
            try:
                self._collect_once()
            except Exception as error:  # never kill the collector thread
                self.last_error = f"{type(error).__name__}: {error}"
            self._stop.wait(self.interval_seconds)

    def _collect_once(self):
        self.last_error = None
        client = self._ec2_client()
        if client is None:
            self.last_error = "EC2 client unavailable"
            return
        try:
            instances = client.describe_instances().get("Reservations", [])
        except Exception as error:  # Floci unreachable
            self.last_error = f"describe_instances: {type(error).__name__}"
            return

        instance_ids = []
        for reservation in instances:
            for inst in reservation.get("Instances", []):
                iid = inst.get("InstanceId")
                state = (inst.get("State") or {}).get("Name")
                if iid and state == "running":
                    instance_ids.append(iid)

        # Stopped/terminated instances produce no metrics; skip them.
        collected = 0
        for iid in instance_ids[: self.max_instances]:
            try:
                self._collect_instance(client, iid)
                collected += 1
            except Exception as error:  # one instance must not abort the rest
                self.last_error = f"{iid}: {type(error).__name__}: {error}"

        self.collections += 1
        self.instances_collected = collected

    def _ec2_client(self):
        if self.get_ec2_client is None:
            return None
        try:
            return self.get_ec2_client()
        except Exception:
            return None

    def _collect_instance(self, client, instance_id):
        stats = self._docker_stats_one(instance_id)
        if stats is None:
            return  # container not found / docker unavailable; silently skip

        cpu = stats.get("cpu_percent")
        mem = stats.get("mem_percent")
        net_rx = stats.get("net_rx_bytes")
        net_tx = stats.get("net_tx_bytes")
        blk_read = stats.get("block_read_bytes")
        blk_write = stats.get("block_write_bytes")

        now = datetime.now(timezone.utc).isoformat()
        dims = [{"name": "InstanceId", "value": instance_id}]

        if cpu is not None:
            put_metric_data(client, self.NAMESPACE, "CpuUtilization", cpu,
                            "Percent", dims, now)
        if mem is not None:
            put_metric_data(client, self.NAMESPACE, "MemoryUtilization", mem,
                            "Percent", dims, now)
        if net_rx is not None:
            put_metric_data(client, self.NAMESPACE, "NetworkIn", net_rx,
                            "Bytes", dims, now)
        if net_tx is not None:
            put_metric_data(client, self.NAMESPACE, "NetworkOut", net_tx,
                            "Bytes", dims, now)
        if blk_read is not None:
            put_metric_data(client, self.NAMESPACE, "DiskReadBytes", blk_read,
                            "Bytes", dims, now)
        if blk_write is not None:
            put_metric_data(client, self.NAMESPACE, "DiskWriteBytes", blk_write,
                            "Bytes", dims, now)

    # ------------------------------------------------------------------ docker stats

    def _docker_stats_one(self, instance_id):
        """Return a dict of container stats for one EC2 instance, or None."""
        if not shutil.which("docker"):
            self.last_error = "docker CLI not available"
            return None
        container_name = f"floci-aws-ec2-{instance_id}"
        try:
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "json",
                 container_name],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.last_error = f"docker stats: {type(error).__name__}"
            return None
        if result.returncode != 0 or not result.stdout.strip():
            self.last_error = f"docker stats exit {result.returncode}"
            return None
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            self.last_error = "docker stats: unparseable output"
            return None
        return {
            "cpu_percent": self._parse_percent(data.get("CPUPerc")),
            "mem_percent": self._parse_percent(data.get("MemPerc")),
            "net_rx_bytes": self._parse_netio(data.get("NetIO"), "rx"),
            "net_tx_bytes": self._parse_netio(data.get("NetIO"), "tx"),
            "block_read_bytes": None,  # not in default docker stats output
            "block_write_bytes": None,  # not in default docker stats output
        }

    @staticmethod
    def _parse_percent(value):
        if value is None or value == "":
            return None
        text = str(value).strip()
        if text.endswith("%"):
            text = text[:-1]
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_netio(value, direction):
        """docker stats NetIO is \"A / B\" (rx / tx); extract one direction."""
        if value is None or value == "":
            return None
        text = str(value).strip()
        parts = [p.strip() for p in text.split("/")]
        if len(parts) != 2:
            return None
        raw = parts[0] if direction == "rx" else parts[1]
        return Ec2MetricsCollector._parse_bytes(raw)

    @staticmethod
    def _parse_bytes(value):
        """Parse docker stats byte strings like \"7.6MB\", \"1.2kB\", \"451B\"."""
        if value is None or value == "":
            return None
        text = str(value).strip()
        if not text:
            return None
        multipliers = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
        unit = "B"
        for suffix in ("GB", "MB", "KB", "B"):
            if text.upper().endswith(suffix):
                unit = suffix
                text = text[: -len(suffix)]
                break
        try:
            return float(text) * multipliers[unit]
        except ValueError:
            return None

    # ------------------------------------------------------------------ status

    def status(self):
        with self._lock:
            return {
                "enabled": self.enabled,
                "running": self.is_running(),
                "interval_seconds": self.interval_seconds,
                "max_instances": self.max_instances,
                "collections": self.collections,
                "instances_collected": self.instances_collected,
                "last_error": self.last_error,
                "started_at": self.started_at,
                "namespace": self.NAMESPACE,
            }
