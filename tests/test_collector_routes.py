"""Mocked tests for EC2 metrics collector routes."""

from unittest.mock import MagicMock


def test_collector_status_endpoint(app, monkeypatch):
    collector = MagicMock()
    collector.status.return_value = {
        "enabled": True,
        "running": True,
        "interval_seconds": 60,
        "max_instances": 10,
        "collections": 5,
        "instances_collected": 2,
        "last_error": None,
        "started_at": 1234567890.0,
        "namespace": "LocalCloud/EC2",
    }
    monkeypatch.setitem(app.extensions, "ec2_metrics_collector", collector)
    client = app.test_client()
    response = client.get("/api/services/ec2/metrics/collector/status")
    assert response.status_code == 200, response.json
    assert response.json["ok"] is True
    assert response.json["collector"]["enabled"] is True
    assert response.json["collector"]["running"] is True
    assert response.json["collector"]["namespace"] == "LocalCloud/EC2"
    collector.status.assert_called_once()


def test_collector_start_endpoint(app, monkeypatch):
    collector = MagicMock()
    collector.status.return_value = {
        "enabled": True,
        "running": True,
        "interval_seconds": 30,
        "max_instances": 5,
        "collections": 0,
        "instances_collected": 0,
        "last_error": None,
        "started_at": None,
        "namespace": "LocalCloud/EC2",
    }
    monkeypatch.setitem(app.extensions, "ec2_metrics_collector", collector)
    client = app.test_client()
    response = client.post("/api/services/ec2/metrics/collector/start", json={
        "interval_seconds": 30,
        "max_instances": 5,
    })
    assert response.status_code == 200, response.json
    assert response.json["ok"] is True
    collector.start.assert_called_once()


def test_collector_start_validation(app, monkeypatch):
    collector = MagicMock()
    collector.status.return_value = {
        "enabled": False,
        "running": False,
        "interval_seconds": 60,
        "max_instances": 10,
        "collections": 0,
        "instances_collected": 0,
        "last_error": None,
        "started_at": None,
        "namespace": "LocalCloud/EC2",
    }
    monkeypatch.setitem(app.extensions, "ec2_metrics_collector", collector)
    client = app.test_client()
    response = client.post("/api/services/ec2/metrics/collector/start", json={
        "interval_seconds": 5,  # below minimum of 10
    })
    assert response.status_code == 400, response.json
    collector.start.assert_not_called()


def test_collector_stop_endpoint(app, monkeypatch):
    collector = MagicMock()
    collector.status.return_value = {
        "enabled": False,
        "running": False,
        "interval_seconds": 60,
        "max_instances": 10,
        "collections": 3,
        "instances_collected": 1,
        "last_error": None,
        "started_at": None,
        "namespace": "LocalCloud/EC2",
    }
    monkeypatch.setitem(app.extensions, "ec2_metrics_collector", collector)
    client = app.test_client()
    response = client.post("/api/services/ec2/metrics/collector/stop")
    assert response.status_code == 200, response.json
    assert response.json["ok"] is True
    collector.stop.assert_called_once()
