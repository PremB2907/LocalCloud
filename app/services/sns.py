class TopicHasSubscriptions(Exception):
    pass


def create_topic(client, topic_name):
    existing = client.get_paginator("list_topics").paginate()
    if any(topic.get("TopicArn", "").rsplit(":", 1)[-1] == topic_name for page in existing for topic in page.get("Topics", [])):
        raise ValueError("A topic with this name already exists; it was left unchanged.")
    return client.create_topic(Name=topic_name)


def list_topics(client):
    paginator = client.get_paginator("list_topics")
    topics = []
    for page in paginator.paginate():
        for topic in page.get("Topics", []):
            arn = topic.get("TopicArn", "")
            topics.append({
                "arn": arn,
                "name": arn.rsplit(":", 1)[-1],
            })
    return topics


def publish_to_local_sqs(client, topic_arn, message, region):
    pages = client.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic_arn)
    subscriptions = [
        subscription
        for page in pages
        for subscription in page.get("Subscriptions", [])
    ]
    for subscription in subscriptions:
        endpoint = subscription.get("Endpoint", "")
        parts = endpoint.split(":")
        is_local_sqs = (
            subscription.get("Protocol") == "sqs"
            and len(parts) == 6
            and parts[:5] == ["arn", "aws", "sqs", region, "000000000000"]
            and bool(parts[5])
        )
        if not is_local_sqs:
            raise TopicHasSubscriptions(
                "Publishing is allowed only when every subscription targets a local SQS queue."
            )
    return client.publish(TopicArn=topic_arn, Message=message)


def topic_attributes(client, topic_arn):
    response = client.get_topic_attributes(TopicArn=topic_arn)
    return response.get("Attributes", {})


def subscribe(client, topic_arn, protocol, endpoint):
    return client.subscribe(TopicArn=topic_arn, Protocol=protocol, Endpoint=endpoint)