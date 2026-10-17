"""Mocked tests for the EC2 metrics collector.

No Docker, no network, no real credentials. The EC2 client, the CloudWatch
``put_metric_data`` adapter, and ``docker stats`` are all faked so the tests
run without elevated privileges.
"""

import threading
import time

import pytest

from app.services.ec2_metrics_collector import Ec2MetricsCollector


class FakeEc2Client:
    def __init__(self, instances):
        self.instances = instances  # list of dicts: {InstanceId, State: {Name}}

    def describe_instances(self):
        return {"Reservations": [{"Instances": self.instances}]}


def _make_collector(instances, interval_seconds=10, max_instances=10):
    def get_client():
        return FakeEc2Client(instances)
    return Ec2MetricsCollector(
        get_ec2_client=get_client,
        interval_seconds=interval_seconds,
        max_instances=max_instances,
    )


def test_collector_start_stop_status():
    collector = _make_collector([])
    assert not collector.is_running()
    collector.start()
    assert collector.is_running()
    status = collector.status()
    assert status["enabled"] is True
    assert status["running"] is True
    assert status["namespace"] == "LocalCloud/EC2"
    collector.stop()
    assert not collector.is_running()
    status = collector.status()
    assert status["enabled"] is False
    assert status["running"] is False


def test_collector_start_is_idempotent():
    collector = _make_collector([])
    collector.start()
    thread1 = collector._thread
    collector.start()  # should not create a second thread
    assert collector._thread is thread1
    collector.stop()


def test_collector_collects_running_instance(monkeypatch):
    published = []

    def fake_put(client, namespace, metric_name, value, unit, dimensions, timestamp):
        published.append((namespace, metric_name, value, unit, dimensions))

    import app.services.ec2_metrics_collector as mod
    monkeypatch.setattr(mod, "put_metric_data", fake_put)
    monkeypatch.setattr(
        Ec2MetricsCollector,
        "_docker_stats_one",
        lambda self, instance_id: {
            "cpu_percent": 42.5,
            "mem_percent": 55.0,
            "net_rx_bytes": 1024.0,
            "net_tx_bytes": 2048.0,
            "block_read_bytes": 512.0,
            "block_write_bytes": 256.0,
        },
    )

    instances = [
        {"InstanceId": "i-abc123", "State": {"Name": "running"}},
    ]
    collector = _make_collector(instances, interval_seconds=1)
    collector.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if collector.collections >= 1:
                break
            time.sleep(0.1)
        assert collector.collections >= 1, "collector did not run"
    finally:
        collector.stop()

    assert len(published) >= 1
    namespaces = {p[0] for p in published}
    assert namespaces == {"LocalCloud/EC2"}
    metrics = {p[1] for p in published}
    assert "CpuUtilization" in metrics
    assert "MemoryUtilization" in metrics
    assert "NetworkIn" in metrics
    assert "NetworkOut" in metrics
    assert "DiskReadBytes" in metrics
    assert "DiskWriteBytes" in metrics
    dims = published[0][4]
    assert dims == [{"name": "InstanceId", "value": "i-abc123"}]


def test_collector_skips_non_running_instances(monkeypatch):
    published = []

    def fake_put(client, namespace, metric_name, value, unit, dimensions, timestamp):
        published.append((metric_name, dimensions))

    import app.services.ec2_metrics_collector as mod
    monkeypatch.setattr(mod, "put_metric_data", fake_put)
    monkeypatch.setattr(
        Ec2MetricsCollector,
        "_docker_stats_one",
        lambda self, instance_id: {"cpu_percent": 10.0},
    )

    instances = [
        {"InstanceId": "i-stopped", "State": {"Name": "stopped"}},
        {"InstanceId": "i-terminated", "State": {"Name": "terminated"}},
        {"InstanceId": "i-running", "State": {"Name": "running"}},
    ]
    collector = _make_collector(instances, interval_seconds=1)
    collector.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if collector.collections >= 1:
                break
            time.sleep(0.1)
    finally:
        collector.stop()

    assert collector.collections >= 1
    # Only the running instance yields metrics; stopped/terminated are skipped.
    instance_ids = {d[1][0]["value"] for d in published}
    assert instance_ids == {"i-running"}


def test_collector_no_docker_is_no_op(monkeypatch):
    import app.services.ec2_metrics_collector as mod
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    instances = [{"InstanceId": "i-abc123", "State": {"Name": "running"}}]
    collector = _make_collector(instances, interval_seconds=1)
    collector.start()
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            if collector.collections >= 1:
                break
            time.sleep(0.1)
    finally:
        collector.stop()
    assert collector.collections >= 1
    assert collector.last_error is not None
    assert "docker" in collector.last_error


def test_collector_status_shape():
    collector = _make_collector([])
    status = collector.status()
    for key in (
        "enabled", "running", "interval_seconds", "max_instances",
        "collections", "instances_collected", "last_error", "started_at",
        "namespace",
    ):
        assert key in status, key
    assert status["namespace"] == "LocalCloud/EC2"


def test_collector_respects_max_instances(monkeypatch):
    published = []

    def fake_put(client, namespace, metric_name, value, unit, dimensions, timestamp):
        published.append((metric_name, dimensions))

    import app.services.ec2_metrics_collector as mod
    monkeypatch.setattr(mod, "put_metric_data", fake_put)
    monkeypatch.setattr(
        Ec2MetricsCollector,
        "_docker_stats_one",
        lambda self, instance_id: {"cpu_percent": 10.0},
    )

    instances = [
        {"InstanceId": f"i-{n:03d}", "State": {"Name": "running"}}
        for n in range(5)
    ]
    collector = _make_collector(instances, interval_seconds=1, max_instances=2)
    collector.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if collector.collections >= 1:
                break
            time.sleep(0.1)
    finally:
        collector.stop()
    assert collector.collections >= 1
    assert collector.instances_collected <= 2


def test_collector_thread_is_daemon():
    collector = _make_collector([])
    collector.start()
    assert collector._thread.daemon is True
    collector.stop()


def test_collector_stop_when_not_started_is_safe():
    collector = _make_collector([])
    collector.stop()  # should not raise
    assert not collector.is_running()


def test_collector_concurrent_start_stop(monkeypatch):
    import app.services.ec2_metrics_collector as mod
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    collector = _make_collector([], interval_seconds=1)
    errors = []

    def worker():
        try:
            for _ in range(3):
                collector.start()
                collector.stop()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert not collector.is_running()


def test_docker_stats_parsing():
    """Verify docker stats NetIO and byte parsing without running docker."""
    c = Ec2MetricsCollector(get_ec2_client=lambda: None)
    assert c._parse_percent("42.5%") == 42.5
    assert c._parse_percent("100") == 100.0
    assert c._parse_percent("") is None
    assert c._parse_percent(None) is None
    assert c._parse_bytes("7.6MB") == 7.6 * 1024 * 1024
    assert c._parse_bytes("1.2kB") == 1.2 * 1024
    assert c._parse_bytes("451B") == 451.0
    assert c._parse_bytes("") is None
    assert c._parse_bytes(None) is None
    # NetIO is "rx / tx"
    assert c._parse_netio("7.6MB / 2.1MB", "rx") == 7.6 * 1024 * 1024
    assert c._parse_netio("7.6MB / 2.1MB", "tx") == 2.1 * 1024 * 1024
    assert c._parse_netio("", "rx") is None
