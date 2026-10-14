from app.services.common import isoformat


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