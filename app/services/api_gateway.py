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