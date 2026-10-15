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


def describe_instance(client, instance_id):
    response = client.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        raise LookupError(f"Instance {instance_id} not found.")
    inst = reservations[0]["Instances"][0]
    return {
        "id": inst.get("InstanceId"),
        "state": inst.get("State", {}).get("Name", "unknown"),
        "state_code": inst.get("State", {}).get("Code"),
        "image_id": inst.get("ImageId"),
        "instance_type": inst.get("InstanceType"),
        "launch_time": isoformat(inst.get("LaunchTime")),
        "private_ip": inst.get("PrivateIpAddress"),
        "public_ip": inst.get("PublicIpAddress"),
        "availability_zone": inst.get("Placement", {}).get("AvailabilityZone"),
        "security_groups": [sg.get("GroupName", "") for sg in inst.get("SecurityGroups", [])],
        "tags": {tag["Key"]: tag["Value"] for tag in inst.get("Tags", [])},
        "subnet_id": inst.get("SubnetId"),
        "vpc_id": inst.get("VpcId"),
        "architecture": inst.get("Architecture"),
        "platform": inst.get("Platform") or inst.get("PlatformDetails"),
        "name": next(
            (tag.get("Value") for tag in inst.get("Tags", []) if tag.get("Key") == "Name"),
            None,
        ),
    }


def run_instances(client, image_id, instance_type="t2.micro", count=1, name=None):
    """Run one or more instances. Floci backs these with Docker containers."""
    params = {
        "ImageId": image_id,
        "InstanceType": instance_type,
        "MinCount": count,
        "MaxCount": count,
    }
    if name:
        params["TagSpecifications"] = [{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": name}],
        }]
    response = client.run_instances(**params)
    return [
        {
            "id": inst.get("InstanceId"),
            "state": inst.get("State", {}).get("Name", "unknown"),
        }
        for inst in response.get("Instances", [])
    ]


def start_instances(client, instance_ids):
    return client.start_instances(InstanceIds=instance_ids)


def stop_instances(client, instance_ids):
    return client.stop_instances(InstanceIds=instance_ids)


def terminate_instances(client, instance_ids):
    return client.terminate_instances(InstanceIds=instance_ids)