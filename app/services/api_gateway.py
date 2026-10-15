import hashlib

from botocore.exceptions import BotoCoreError, ClientError


def create_rest_api(client, name):
    existing = client.get_paginator("get_rest_apis").paginate(limit=100)
    if any(api.get("name") == name for page in existing for api in page.get("items", [])):
        raise ValueError("A REST API with this name already exists; it was left unchanged.")
    return client.create_rest_api(name=name, description="Created from the local operations console.")


def list_rest_apis(client):
    pages = client.get_paginator("get_rest_apis").paginate(limit=100)
    apis = [api for page in pages for api in page.get("items", [])]
    return {
        "apis": [
            {
                "id": api.get("id"),
                "name": api.get("name"),
                "description": api.get("description"),
                "created_date": api.get("createdDate").isoformat() if api.get("createdDate") else None,
                "endpoint_type": api.get("endpointConfiguration", {}).get("types", []),
            }
            for api in apis
        ],
        "position": None,
    }


def list_rest_resources(client, api_id):
    response = client.get_resources(restApiId=api_id, embed=["methods"])
    return [
        {
            "id": resource.get("id"),
            "path": resource.get("path"),
            "methods": sorted(resource.get("resourceMethods", {}).keys()),
        }
        for resource in response.get("items", [])
    ]


def list_http_apis(client):
    pages = client.get_paginator("get_apis").paginate(MaxResults="100")
    apis = [api for page in pages for api in page.get("Items", [])]
    return {
        "apis": [
            {
                "id": api.get("ApiId"),
                "name": api.get("Name"),
                "protocol_type": api.get("ProtocolType"),
                "description": api.get("Description"),
                "created_date": api.get("CreatedDate").isoformat() if api.get("CreatedDate") else None,
            }
            for api in apis
        ],
        "next_token": None,
    }


def create_http_api(client, name):
    existing = client.get_paginator("get_apis").paginate(MaxResults="100")
    if any(api.get("Name") == name for page in existing for api in page.get("Items", [])):
        raise ValueError("An HTTP API with this name already exists; it was left unchanged.")
    api = client.create_api(Name=name, ProtocolType="HTTP")
    try:
        client.create_stage(ApiId=api["ApiId"], StageName="$default", AutoDeploy=True)
    except (BotoCoreError, ClientError, KeyError):
        client.delete_api(ApiId=api["ApiId"])
        raise
    return api


def list_http_routes(client, api_id):
    response = client.get_routes(ApiId=api_id, MaxResults="100")
    return [
        {
            "id": route.get("RouteId"),
            "key": route.get("RouteKey"),
            "target": route.get("Target"),
            "authorization_type": route.get("AuthorizationType"),
        }
        for route in response.get("Items", [])
    ]


def create_http_lambda_route(client, lambda_client, api_id, route_key, function_name, region):
    existing_routes = client.get_routes(ApiId=api_id, MaxResults="100").get("Items", [])
    if any(route.get("RouteKey") == route_key for route in existing_routes):
        raise ValueError("A route with this key already exists; it was left unchanged.")

    function = lambda_client.get_function_configuration(FunctionName=function_name)
    function_arn = function.get("FunctionArn")
    if not function_arn:
        raise ValueError("The selected Lambda function did not return an ARN.")

    statement_id = "localcloud-" + hashlib.sha256(f"{api_id}:{route_key}".encode("utf-8")).hexdigest()[:24]
    lambda_client.add_permission(
        FunctionName=function_name,
        StatementId=statement_id,
        Action="lambda:InvokeFunction",
        Principal="apigateway.amazonaws.com",
        SourceArn=f"arn:aws:execute-api:{region}:000000000000:{api_id}/*/*/*",
    )
    integration = None
    try:
        integration = client.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{function_arn}/invocations",
            IntegrationMethod="POST",
            PayloadFormatVersion="2.0",
            TimeoutInMillis=30000,
        )
        route = client.create_route(
            ApiId=api_id,
            RouteKey=route_key,
            Target=f"integrations/{integration['IntegrationId']}",
            AuthorizationType="NONE",
        )
    except (BotoCoreError, ClientError, KeyError):
        if integration and integration.get("IntegrationId"):
            client.delete_integration(ApiId=api_id, IntegrationId=integration["IntegrationId"])
        lambda_client.remove_permission(FunctionName=function_name, StatementId=statement_id)
        raise
    return {
        "id": route.get("RouteId"),
        "key": route.get("RouteKey"),
        "target": route.get("Target"),
        "integration_id": integration["IntegrationId"],
    }


def delete_http_api(client, api_id):
    return client.delete_api(ApiId=api_id)