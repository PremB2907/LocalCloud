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