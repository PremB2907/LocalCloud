import base64
import re
import shutil
import subprocess

from app.services.common import isoformat


SSH_USER = "root"
SSH_HOST = "127.0.0.1"

# Installs and starts OpenSSH inside the instance container. Floci injects the
# key pair before running user data, but the Docker-library base images ship
# without sshd, so Floci's own sshd start attempt is a no-op until this runs.
INSTALL_SSH_USER_DATA = """#!/bin/sh
if ! command -v sshd >/dev/null 2>&1 && [ ! -x /usr/sbin/sshd ]; then
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq openssh-server
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q openssh-server
  elif command -v yum >/dev/null 2>&1; then
    yum install -y -q openssh-server
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache openssh
  fi
fi
mkdir -p /run/sshd /var/run/sshd
ssh-keygen -A >/dev/null 2>&1
pgrep -x sshd >/dev/null 2>&1 || /usr/sbin/sshd
"""


def _tag(tags, key):
    return next((tag.get("Value") for tag in tags or [] if tag.get("Key") == key), None)


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
                    "key_name": instance.get("KeyName"),
                    "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
                    "name": _tag(instance.get("Tags"), "Name"),
                })
    return instances


def describe_instance(client, instance_id):
    response = client.describe_instances(InstanceIds=[instance_id])
    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            if instance.get("InstanceId") == instance_id:
                return {
                    "id": instance_id,
                    "state": instance.get("State", {}).get("Name", "unknown"),
                    "image_id": instance.get("ImageId"),
                    "instance_type": instance.get("InstanceType"),
                    "private_ip": instance.get("PrivateIpAddress"),
                    "key_name": instance.get("KeyName"),
                    "name": _tag(instance.get("Tags"), "Name"),
                }
    raise LookupError(f"Instance {instance_id} was not found.")


def run_instances(client, image_id, instance_type, name=None, count=1, key_name=None, install_ssh=False):
    """Launch one or more Docker-backed EC2 instances.

    Floci maps AMI IDs to Docker images from its image catalog. Each instance
    becomes a real Docker container. A Name tag is applied when provided.
    """
    params = {
        "ImageId": image_id,
        "InstanceType": instance_type,
        "MinCount": count,
        "MaxCount": count,
    }
    if key_name:
        params["KeyName"] = key_name
    if install_ssh:
        params["UserData"] = base64.b64encode(INSTALL_SSH_USER_DATA.encode()).decode()
    response = client.run_instances(**params)
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


def _state_changes(changes):
    return [
        {
            "id": change.get("InstanceId"),
            "previous_state": change.get("PreviousState", {}).get("Name"),
            "current_state": change.get("CurrentState", {}).get("Name"),
        }
        for change in changes
    ]


def start_instances(client, instance_ids):
    return _state_changes(client.start_instances(InstanceIds=instance_ids).get("StartingInstances", []))


def stop_instances(client, instance_ids):
    return _state_changes(client.stop_instances(InstanceIds=instance_ids).get("StoppingInstances", []))


def terminate_instances(client, instance_ids):
    return _state_changes(client.terminate_instances(InstanceIds=instance_ids).get("TerminatingInstances", []))


def reboot_instances(client, instance_ids):
    client.reboot_instances(InstanceIds=instance_ids)
    return [{"id": iid, "previous_state": "running", "current_state": "running"} for iid in instance_ids]


def list_key_pairs(client):
    response = client.describe_key_pairs()
    return [
        {
            "name": key.get("KeyName"),
            "id": key.get("KeyPairId"),
            "fingerprint": key.get("KeyFingerprint"),
            "type": key.get("KeyType", "rsa"),
            "created": isoformat(key.get("CreateTime")),
        }
        for key in response.get("KeyPairs", [])
    ]


def create_key_pair(client, name):
    response = client.create_key_pair(KeyName=name)
    return {
        "name": response.get("KeyName"),
        "id": response.get("KeyPairId"),
        "fingerprint": response.get("KeyFingerprint"),
        "private_key": response.get("KeyMaterial"),
    }


def delete_key_pair(client, name):
    return client.delete_key_pair(KeyName=name)


_PORT_PATTERN = re.compile(r":(\d+)\s*$")


def find_ssh_endpoint(instance_id, runner=subprocess.run):
    """Best-effort lookup of the Floci instance container and its SSH host port.

    Floci does not expose the SSH host port through DescribeInstances, so this
    asks the local Docker CLI. It returns ``None`` values when Docker is absent
    or needs elevated permissions.
    """
    result = {"container": None, "port": None}
    if not shutil.which("docker"):
        return result
    try:
        listed = runner(
            ["docker", "ps", "-a",
             "--filter", "label=io.floci.service=ec2",
             "--filter", f"label=io.floci.resource-id={instance_id}",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        names = [line.strip() for line in (listed.stdout or "").splitlines() if line.strip()]
        if listed.returncode != 0 or not names:
            return result
        result["container"] = names[0]
        ported = runner(
            ["docker", "port", names[0], "22/tcp"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        for line in (ported.stdout or "").splitlines():
            match = _PORT_PATTERN.search(line.strip())
            if match:
                result["port"] = int(match.group(1))
                break
    except (OSError, subprocess.SubprocessError):
        pass
    return result
