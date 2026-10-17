import time
from datetime import datetime, timezone

from app.services.common import isoformat


def create_log_group(client, group_name):
    return client.create_log_group(logGroupName=group_name)


def create_log_stream(client, group_name, stream_name):
    return client.create_log_stream(logGroupName=group_name, logStreamName=stream_name)


def put_log_event(client, group_name, stream_name, message):
    return client.put_log_events(
        logGroupName=group_name,
        logStreamName=stream_name,
        logEvents=[{"timestamp": int(time.time() * 1000), "message": message}],
    )


def delete_log_group(client, group_name):
    return client.delete_log_group(logGroupName=group_name)


def list_metrics(client):
    pages = client.get_paginator("list_metrics").paginate()
    metrics = [metric for page in pages for metric in page.get("Metrics", [])]
    return {
        "metrics": [
            {
                "namespace": metric.get("Namespace"),
                "name": metric.get("MetricName"),
                "dimensions": metric.get("Dimensions", []),
            }
            for metric in metrics
        ],
        "next_token": None,
    }


def list_log_groups(client):
    pages = client.get_paginator("describe_log_groups").paginate(limit=50)
    groups = [group for page in pages for group in page.get("logGroups", [])]
    return {
        "log_groups": [
            {
                "name": group.get("logGroupName"),
                "stored_bytes": group.get("storedBytes", 0),
                "retention_days": group.get("retentionInDays"),
                "creation_time": group.get("creationTime"),
            }
            for group in groups
        ],
        "next_token": None,
    }


def list_log_streams(client, group_name):
    response = client.describe_log_streams(
        logGroupName=group_name,
        orderBy="LastEventTime",
        descending=True,
        limit=50,
    )
    return [
        {
            "name": stream.get("logStreamName"),
            "stored_bytes": stream.get("storedBytes", 0),
            "last_event_time": stream.get("lastEventTimestamp"),
            "last_ingestion_time": stream.get("lastIngestionTime"),
        }
        for stream in response.get("logStreams", [])
    ]


def get_log_events(client, group_name, stream_name, limit=100):
    response = client.get_log_events(
        logGroupName=group_name,
        logStreamName=stream_name,
        limit=limit,
        startFromHead=False,
    )
    return {
        "events": [
            {
                "timestamp": event.get("timestamp"),
                "message": event.get("message", ""),
            }
            for event in response.get("events", [])
        ],
        "next_forward_token": response.get("nextForwardToken"),
        "next_backward_token": response.get("nextBackwardToken"),
    }


def put_metric_data(client, namespace, metric_name, value, unit, dimensions,
                    timestamp=None):
    """Publish a single CloudWatch metric datum.

    ``dimensions`` is a list of ``{"name": ..., "value": ...}`` dicts, e.g.
    ``[{"name": "InstanceId", "value": "i-..."}]``. Floci stores the datum and
    auto-synthesizes SampleCount/Sum/Min/Max from the scalar value; the
    timestamp defaults to now when omitted.
    """
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("A metric namespace is required.")
    if not isinstance(metric_name, str) or not metric_name:
        raise ValueError("A metric name is required.")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("A numeric metric value is required.")
    datum = {
        "MetricName": metric_name,
        "Value": float(value),
    }
    if unit:
        datum["Unit"] = unit
    if dimensions:
        datum["Dimensions"] = [
            {"Name": d["name"], "Value": d["value"]} for d in dimensions
        ]
    if timestamp:
        datum["Timestamp"] = _as_datetime(timestamp)
    return client.put_metric_data(Namespace=namespace, MetricData=[datum])


def get_metric_statistics(client, namespace, metric_name, dimensions,
                          start_time, end_time, period, statistic, unit=None):
    """Retrieve aggregated statistics for one metric over a time range.

    Returns datapoints with ISO 8601 timestamps so they serialize cleanly to
    JSON for the frontend graph.
    """
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("A metric namespace is required.")
    if not isinstance(metric_name, str) or not metric_name:
        raise ValueError("A metric name is required.")
    valid_statistics = {"Average", "Sum", "Minimum", "Maximum", "SampleCount"}
    if statistic not in valid_statistics:
        raise ValueError(
            f"Statistic must be one of: {', '.join(sorted(valid_statistics))}."
        )
    kwargs = {
        "Namespace": namespace,
        "MetricName": metric_name,
        "StartTime": _as_datetime(start_time) if start_time else None,
        "EndTime": _as_datetime(end_time) if end_time else None,
        "Period": int(period),
        "Statistics": [statistic],
    }
    if dimensions:
        kwargs["Dimensions"] = [
            {"Name": d["name"], "Value": d["value"]} for d in dimensions
        ]
    if unit:
        kwargs["Unit"] = unit
    response = client.get_metric_statistics(**kwargs)
    datapoints = response.get("Datapoints", [])
    return {
        "datapoints": [
            {
                "timestamp": _isoformat(dp.get("Timestamp")),
                "average": dp.get("Average"),
                "sum": dp.get("Sum"),
                "minimum": dp.get("Minimum"),
                "maximum": dp.get("Maximum"),
                "sample_count": dp.get("SampleCount"),
                "unit": dp.get("Unit"),
            }
            for dp in datapoints
        ]
    }


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    raise ValueError("Timestamp must be a datetime or ISO 8601 string.")


def _isoformat(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
