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


def create_log_group(client, group_name, retention_days=None):
    """Create a CloudWatch log group."""
    client.create_log_group(logGroupName=group_name)
    if retention_days:
        client.put_retention_policy(logGroupName=group_name, retentionInDays=retention_days)
    return {"name": group_name}


def create_log_stream(client, group_name, stream_name):
    """Create a log stream within a log group."""
    client.create_log_stream(logGroupName=group_name, logStreamName=stream_name)
    return {"name": stream_name}


def put_log_events(client, group_name, stream_name, messages):
    """Put log events into a log stream."""
    import time
    events = [
        {"timestamp": int(time.time() * 1000), "message": msg}
        for msg in messages
    ]
    return client.put_log_events(
        logGroupName=group_name,
        logStreamName=stream_name,
        logEvents=events,
    )


def delete_log_group(client, group_name):
    """Delete a CloudWatch log group."""
    return client.delete_log_group(logGroupName=group_name)