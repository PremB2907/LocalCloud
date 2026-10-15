from urllib.parse import urlsplit


def create_standard_queue(client, queue_name):
    existing = client.list_queues().get("QueueUrls", [])
    if any(urlsplit(url).path.rstrip("/").split("/")[-1] == queue_name for url in existing):
        raise ValueError("A queue with this name already exists; it was left unchanged.")
    return client.create_queue(QueueName=queue_name)


def list_queues(client):
    paginator = client.get_paginator("list_queues")
    queues = []
    for page in paginator.paginate():
        for queue_url in page.get("QueueUrls", []):
            queues.append({
                "name": urlsplit(queue_url).path.rstrip("/").split("/")[-1],
                "url": queue_url,
            })
    return queues


def send_message(client, queue_url, body):
    return client.send_message(QueueUrl=queue_url, MessageBody=body)


def receive_messages(client, queue_url, max_number=5):
    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=max_number,
        WaitTimeSeconds=2,
    )
    return response.get("Messages", [])


def delete_message(client, queue_url, receipt_handle):
    return client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


def queue_attributes(client, queue_url):
    response = client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["All"],
    )
    return response.get("Attributes", {})


def purge_queue(client, queue_url):
    """Remove all messages from a queue."""
    return client.purge_queue(QueueUrl=queue_url)


def delete_queue(client, queue_url):
    """Delete a queue."""
    return client.delete_queue(QueueUrl=queue_url)