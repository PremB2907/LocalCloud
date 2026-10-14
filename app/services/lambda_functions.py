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