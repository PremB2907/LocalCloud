from botocore.exceptions import ClientError

from app.services.common import isoformat


def create_bucket(client, bucket_name):
    existing = {item["Name"] for item in client.list_buckets().get("Buckets", [])}
    if bucket_name in existing:
        raise ValueError("A bucket with this name already exists; it was left unchanged.")
    return client.create_bucket(Bucket=bucket_name)


def list_buckets(client):
    response = client.list_buckets()
    return [
        {
            "name": bucket["Name"],
            "created_at": isoformat(bucket.get("CreationDate")),
        }
        for bucket in response.get("Buckets", [])
    ]


def upload_object(client, bucket_name, key, body, content_type):
    if bucket_name not in {item["Name"] for item in client.list_buckets().get("Buckets", [])}:
        raise LookupError("Bucket not found.")
    try:
        client.head_object(Bucket=bucket_name, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
            raise
    else:
        raise ValueError("An object with this key already exists; it was not overwritten.")
    return client.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=body,
        ContentType=content_type or "application/octet-stream",
    )


def download_object(client, bucket_name, key):
    return client.get_object(Bucket=bucket_name, Key=key)


def delete_object(client, bucket_name, key):
    return client.delete_object(Bucket=bucket_name, Key=key)


def list_objects(client, bucket_name, continuation_token=None):
    options = {"Bucket": bucket_name, "MaxKeys": 1000}
    if continuation_token:
        options["ContinuationToken"] = continuation_token
    response = client.list_objects_v2(**options)
    return {
        "objects": [
            {
                "key": item["Key"],
                "size": item["Size"],
                "etag": item.get("ETag", "").strip('"'),
                "last_modified": isoformat(item.get("LastModified")),
            }
            for item in response.get("Contents", [])
        ],
        "truncated": response.get("IsTruncated", False),
        "continuation_token": response.get("NextContinuationToken"),
    }


def object_metadata(client, bucket_name, key):
    response = client.head_object(Bucket=bucket_name, Key=key)
    return {
        "content_length": response.get("ContentLength"),
        "content_type": response.get("ContentType"),
        "etag": response.get("ETag", "").strip('"'),
        "last_modified": isoformat(response.get("LastModified")),
        "metadata": response.get("Metadata", {}),
        "storage_class": response.get("StorageClass", "STANDARD"),
    }