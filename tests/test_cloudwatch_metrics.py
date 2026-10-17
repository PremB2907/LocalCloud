"""Mocked tests for CloudWatch metric publication and statistics.

No real email or network is involved; the SDK client is faked.
"""

from datetime import datetime, timezone

import pytest


class FakeCloudWatchClient:
    def __init__(self):
        self.put_calls = []
        self.stats_calls = []

    def put_metric_data(self, Namespace, MetricData):
        self.put_calls.append((Namespace, MetricData))
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_metric_statistics(self, **kwargs):
        self.stats_calls.append(kwargs)
        now = datetime.now(timezone.utc)
        return {
            "Datapoints": [
                {
                    "Timestamp": now,
                    "Average": 42.5,
                    "Sum": 42.5,
                    "Minimum": 42.5,
                    "Maximum": 42.5,
                    "SampleCount": 1.0,
                    "Unit": "Percent",
                }
            ],
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }


def test_cloudwatch_put_metric_data_publishes_datum(app, monkeypatch):
    client = FakeCloudWatchClient()
    monkeypatch.setattr("app.routes._client", lambda _name: client)
    c = app.test_client()
    response = c.post("/api/services/cloudwatch/metric-data", json={
        "namespace": "AWS/EC2",
        "metric_name": "CPUUtilization",
        "value": 42.5,
        "unit": "Percent",
        "dimensions": [{"name": "InstanceId", "value": "i-123"}],
    })
    assert response.status_code == 201, response.json
    assert response.json["ok"] is True
    assert len(client.put_calls) == 1
    namespace, data = client.put_calls[0]
    assert namespace == "AWS/EC2"
    assert data[0]["MetricName"] == "CPUUtilization"
    assert data[0]["Value"] == 42.5
    assert data[0]["Unit"] == "Percent"
    assert data[0]["Dimensions"] == [{"Name": "InstanceId", "Value": "i-123"}]


def test_cloudwatch_put_metric_data_validates_input(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: FakeCloudWatchClient())
    c = app.test_client()
    response = c.post("/api/services/cloudwatch/metric-data", json={
        "namespace": "", "metric_name": "CPUUtilization", "value": 1,
    })
    assert response.status_code == 400, response.json
    response = c.post("/api/services/cloudwatch/metric-data", json={
        "namespace": "AWS/EC2", "metric_name": "CPUUtilization", "value": "not-a-number",
    })
    assert response.status_code == 400, response.json


def test_cloudwatch_get_metric_statistics_returns_datapoints(app, monkeypatch):
    client = FakeCloudWatchClient()
    monkeypatch.setattr("app.routes._client", lambda _name: client)
    c = app.test_client()
    response = c.get("/api/services/cloudwatch/metric-statistics", query_string={
        "namespace": "AWS/EC2",
        "metric_name": "CPUUtilization",
        "dimensions": '[{"name": "InstanceId", "value": "i-123"}]',
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-02T00:00:00Z",
        "period": "300",
        "statistic": "Average",
    })
    assert response.status_code == 200, response.json
    assert response.json["ok"] is True
    datapoints = response.json["datapoints"]
    assert len(datapoints) == 1
    assert datapoints[0]["average"] == 42.5
    assert datapoints[0]["unit"] == "Percent"
    assert "T" in datapoints[0]["timestamp"]
    kwargs = client.stats_calls[0]
    assert kwargs["Namespace"] == "AWS/EC2"
    assert kwargs["MetricName"] == "CPUUtilization"
    assert kwargs["Dimensions"] == [{"Name": "InstanceId", "Value": "i-123"}]
    assert kwargs["Statistics"] == ["Average"]
    assert kwargs["Period"] == 300


def test_cloudwatch_get_metric_statistics_validates_input(app, monkeypatch):
    monkeypatch.setattr("app.routes._client", lambda _name: FakeCloudWatchClient())
    c = app.test_client()
    response = c.get("/api/services/cloudwatch/metric-statistics", query_string={
        "namespace": "", "metric_name": "CPUUtilization",
    })
    assert response.status_code == 400, response.json
    response = c.get("/api/services/cloudwatch/metric-statistics", query_string={
        "namespace": "AWS/EC2", "metric_name": "CPUUtilization",
        "statistic": "Median",
    })
    assert response.status_code == 400, response.json
    response = c.get("/api/services/cloudwatch/metric-statistics", query_string={
        "namespace": "AWS/EC2", "metric_name": "CPUUtilization",
        "period": "not-a-number",
    })
    assert response.status_code == 400, response.json
