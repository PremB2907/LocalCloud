class TopicHasSubscriptions(Exception):
    pass


# Protocols delivered by LocalCloud itself. The Floci emulator only delivers
# to SQS queues, so email subscriptions are handled by LocalCloud's SMTP
# adapter and must not block publishing.
LOCAL_CLOUD_PROTOCOLS = {"email", "email-json"}


def topic_subscriptions(client, topic_arn):
    """List every subscription recorded for a topic by the emulator."""
    pages = client.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic_arn)
    return [
        subscription
        for page in pages
        for subscription in page.get("Subscriptions", [])
    ]


def _local_sqs_endpoint(subscription, region):
    endpoint = subscription.get("Endpoint", "")
    parts = endpoint.split(":")
    return (
        subscription.get("Protocol") == "sqs"
        and len(parts) == 6
        and parts[:5] == ["arn", "aws", "sqs", region, "000000000000"]
        and bool(parts[5])
    )


def local_sqs_subscriptions(subscriptions, region):
    return [subscription for subscription in subscriptions if _local_sqs_endpoint(subscription, region)]


def email_subscriptions(subscriptions):
    return [
        subscription
        for subscription in subscriptions
        if subscription.get("Protocol") in LOCAL_CLOUD_PROTOCOLS
    ]


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
    """Publish to local SQS subscriptions and return the emulator message ID.

    Email subscriptions are delivered by LocalCloud itself (see the SMTP
    adapter), so they never block this publish. Any other protocol that is
    not a local SQS queue still blocks publishing.
    """
    subscriptions = topic_subscriptions(client, topic_arn)
    for subscription in subscriptions:
        if (
            subscription.get("Protocol") not in LOCAL_CLOUD_PROTOCOLS
            and not _local_sqs_endpoint(subscription, region)
        ):
            raise TopicHasSubscriptions(
                "Publishing is allowed only when every subscription targets a local SQS queue or a LocalCloud email subscription."
            )
    return client.publish(TopicArn=topic_arn, Message=message)


def topic_attributes(client, topic_arn):
    response = client.get_topic_attributes(TopicArn=topic_arn)
    return response.get("Attributes", {})


def subscribe(client, topic_arn, protocol, endpoint):
    return client.subscribe(TopicArn=topic_arn, Protocol=protocol, Endpoint=endpoint)