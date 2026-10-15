import time

from botocore.exceptions import ClientError


PENDING_STATUSES = {"Pending", "InProgress", "Delayed"}


def run_shell_command(client, instance_id, script, timeout_seconds=30, poll_interval=0.5, sleep=time.sleep):
    """Run a shell script inside a Floci EC2 instance through SSM Run Command.

    Floci executes ``AWS-RunShellScript`` directly inside the instance's Docker
    container, so no SSM agent or Docker socket access is needed here.
    """
    response = client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script]},
        TimeoutSeconds=max(30, timeout_seconds),
    )
    command_id = response["Command"]["CommandId"]
    deadline = time.monotonic() + timeout_seconds
    invocation = None
    while True:
        try:
            invocation = client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "InvocationDoesNotExist":
                raise
            invocation = None
        if invocation and invocation.get("Status") not in PENDING_STATUSES:
            break
        if time.monotonic() >= deadline:
            break
        sleep(poll_interval)
    status = (invocation or {}).get("Status", "Pending")
    return {
        "command_id": command_id,
        "status": status,
        "finished": status not in PENDING_STATUSES,
        "exit_code": (invocation or {}).get("ResponseCode"),
        "stdout": (invocation or {}).get("StandardOutputContent", ""),
        "stderr": (invocation or {}).get("StandardErrorContent", ""),
    }
