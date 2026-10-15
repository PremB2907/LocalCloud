import base64
import json
import zipfile
from io import BytesIO

from app.services.common import isoformat


# Runtimes commonly supported by Floci's Docker-based Lambda executor.
SUPPORTED_RUNTIMES = (
    "python3.12",
    "python3.11",
    "python3.10",
    "nodejs22.x",
    "nodejs20.x",
    "nodejs18.x",
    "java17",
    "go1.x",
    "ruby3.2",
)

# Default IAM role ARN accepted by the local emulator.
DEFAULT_ROLE_ARN = "arn:aws:iam::000000000000:role/lambda-role"


def list_functions(client):
    paginator = client.get_paginator("list_functions")
    functions = []
    for page in paginator.paginate(MaxItems=100):
        for function in page.get("Functions", []):
            functions.append({
                "name": function.get("FunctionName"),
                "arn": function.get("FunctionArn"),
                "runtime": function.get("Runtime"),
                "state": function.get("State", "Active"),
                "last_modified": function.get("LastModified"),
                "memory_size": function.get("MemorySize"),
                "timeout": function.get("Timeout"),
                "handler": function.get("Handler"),
            })
    return functions


def function_configuration(client, function_name):
    function = client.get_function_configuration(FunctionName=function_name)
    return {
        "name": function.get("FunctionName"),
        "arn": function.get("FunctionArn"),
        "runtime": function.get("Runtime"),
        "state": function.get("State"),
        "state_reason": function.get("StateReason"),
        "last_modified": function.get("LastModified"),
        "memory_size": function.get("MemorySize"),
        "timeout": function.get("Timeout"),
        "handler": function.get("Handler"),
        "description": function.get("Description"),
        "code_size": function.get("CodeSize"),
        "last_update_status": function.get("LastUpdateStatus"),
    }


def _build_zip(code, handler):
    """Package function source into an in-memory zip.

    The handler is expected in the form ``filename.function_name``; the source
    is written to ``filename.py`` so the runtime can import it.
    """
    module_name = handler.split(".", 1)[0] if "." in handler else "index"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{module_name}.py", code)
    return buffer.getvalue()


def create_function(client, function_name, runtime, handler, code,
                    role=DEFAULT_ROLE_ARN, description="", memory_size=128, timeout=3):
    """Create a Lambda function from inline source code.

    The source is packaged into a zip and uploaded via ``ZipFile``. The role
    ARN defaults to a local-emulator placeholder.
    """
    if runtime not in SUPPORTED_RUNTIMES:
        raise ValueError(f"Unsupported runtime: {runtime}. Choose one of {', '.join(SUPPORTED_RUNTIMES)}.")
    if "." not in handler:
        raise ValueError("Handler must be in the form filename.function_name.")
    zip_bytes = _build_zip(code, handler)
    response = client.create_function(
        FunctionName=function_name,
        Runtime=runtime,
        Role=role,
        Handler=handler,
        Code={"ZipFile": zip_bytes},
        Description=description,
        MemorySize=memory_size,
        Timeout=timeout,
        Publish=False,
    )
    return {
        "name": response.get("FunctionName"),
        "arn": response.get("FunctionArn"),
        "runtime": response.get("Runtime"),
        "handler": response.get("Handler"),
        "state": response.get("State", "Pending"),
        "last_modified": isoformat(response.get("LastModified")),
    }


def invoke_function(client, function_name, payload=None):
    """Invoke a function synchronously and return its decoded payload."""
    invoke_args = {
        "FunctionName": function_name,
        "InvocationType": "RequestResponse",
        "LogType": "None",
    }
    if payload is not None:
        invoke_args["Payload"] = json.dumps(payload).encode("utf-8")
    response = client.invoke(**invoke_args)
    body = response["Payload"].read()
    response["Payload"].close()
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        decoded = body.decode("utf-8", errors="replace")
    return {
        "status_code": response.get("StatusCode"),
        "executed_version": response.get("ExecutedVersion"),
        "function_error": response.get("FunctionError"),
        "payload": decoded,
    }


def delete_function(client, function_name):
    return client.delete_function(FunctionName=function_name)
