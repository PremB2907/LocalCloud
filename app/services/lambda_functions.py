import base64
import json
import zipfile
from io import BytesIO

from botocore.exceptions import ClientError

from app.services.common import isoformat


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


def _build_lambda_zip(handler_code):
    """Build in-memory ZIP containing lambda_function.py with the provided code."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", handler_code)
    buf.seek(0)
    return buf.read()


DEFAULT_HANDLER_CODE = '''import json

def lambda_handler(event, context):
    """Simple echo handler created by LocalCloud Console."""
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Hello from LocalCloud!",
            "event": event,
        }),
    }
'''


def create_function(client, function_name, runtime="python3.12", handler="lambda_function.lambda_handler",
                     memory_size=128, timeout=30, description="", handler_code=None):
    """Create a Lambda function with a simple inline handler."""
    try:
        client.get_function(FunctionName=function_name)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code not in {"ResourceNotFoundException", "ResourceNotFound", "404", "NotFound"}:
            raise
    else:
        raise ValueError(f"A function named '{function_name}' already exists; it was left unchanged.")

    code_bytes = _build_lambda_zip(handler_code or DEFAULT_HANDLER_CODE)

    response = client.create_function(
        FunctionName=function_name,
        Runtime=runtime,
        Role="arn:aws:iam::000000000000:role/lambda-role",
        Handler=handler,
        Code={"ZipFile": code_bytes},
        Description=description or f"Created by LocalCloud Console",
        Timeout=timeout,
        MemorySize=memory_size,
        Publish=True,
    )
    return response


def invoke_function(client, function_name, payload=None):
    """Invoke a Lambda function and return the response."""
    params = {
        "FunctionName": function_name,
        "InvocationType": "RequestResponse",
        "LogType": "Tail",
    }
    if payload is not None:
        params["Payload"] = json.dumps(payload).encode("utf-8")
    else:
        params["Payload"] = b"{}"

    response = client.invoke(**params)
    result_payload = response.get("Payload")
    body = result_payload.read().decode("utf-8") if result_payload else ""
    log_result = response.get("LogResult", "")

    try:
        decoded_log = base64.b64decode(log_result).decode("utf-8") if log_result else ""
    except Exception:
        decoded_log = log_result

    return {
        "status_code": response.get("StatusCode"),
        "function_error": response.get("FunctionError"),
        "log_output": decoded_log,
        "payload": body,
        "executed_version": response.get("ExecutedVersion"),
    }


def delete_function(client, function_name):
    """Delete a Lambda function."""
    return client.delete_function(FunctionName=function_name)