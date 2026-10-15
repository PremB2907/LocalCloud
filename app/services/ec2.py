from app.services.common import isoformat


def list_instances(client):
    paginator = client.get_paginator("describe_instances")
    instances = []
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instances.append({
                    "id": instance.get("InstanceId"),
                    "state": instance.get("State", {}).get("Name", "unknown"),
                    "image_id": instance.get("ImageId"),
                    "instance_type": instance.get("InstanceType"),
                    "launch_time": isoformat(instance.get("LaunchTime")),
                    "private_ip": instance.get("PrivateIpAddress"),
                    "public_ip": instance.get("PublicIpAddress"),
                    "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
                    "name": next(
                        (tag.get("Value") for tag in instance.get("Tags", []) if tag.get("Key") == "Name"),
                        None,
                    ),
                })
    return instances


def run_instances(client, image_id, instance_type, name=None, count=1):
    """Launch one or more Docker-backed EC2 instances.

    Floci maps AMI IDs to Docker images from its image catalog. Each instance
    becomes a real Docker container. A Name tag is applied when provided.
    """
    response = client.run_instances(
        ImageId=image_id,
        InstanceType=instance_type,
        MinCount=count,
        MaxCount=count,
    )
    instances = response.get("Instances", [])
    instance_ids = [instance.get("InstanceId") for instance in instances if instance.get("InstanceId")]
    if name and instance_ids:
        client.create_tags(
            Resources=instance_ids,
            Tags=[{"Key": "Name", "Value": name}],
        )
    return {
        "instance_ids": instance_ids,
        "count": len(instance_ids),
    }


def start_instances(client, instance_ids):
    response = client.start_instances(InstanceIds=instance_ids)
    return [
        {
            "id": change.get("InstanceId"),
            "previous_state": change.get("PreviousState", {}).get("Name"),
            "current_state": change.get("CurrentState", {}).get("Name"),
        }
        for change in response.get("StartingInstances", [])
    ]


def stop_instances(client, instance_ids):
    response = client.stop_instances(InstanceIds=instance_ids)
    return [
        {
            "id": change.get("InstanceId"),
            "previous_state": change.get("PreviousState", {}).get("Name"),
            "current_state": change.get("CurrentState", {}).get("Name"),
        }
        for change in response.get("StoppingInstances", [])
    ]


def terminate_instances(client, instance_ids):
    response = client.terminate_instances(InstanceIds=instance_ids)
    return [
        {
            "id": change.get("InstanceId"),
            "previous_state": change.get("PreviousState", {}).get("Name"),
            "current_state": change.get("CurrentState", {}).get("Name"),
        }
        for change in response.get("TerminatingInstances", [])
    ]
