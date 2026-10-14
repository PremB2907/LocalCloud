import os

import pytest


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("RUN_FLOCI_INTEGRATION") != "1", reason="set RUN_FLOCI_INTEGRATION=1 to use live Floci")
def test_live_read_routes(app):
    client = app.test_client()
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json["connected"] is True

    for service in ("s3", "dynamodb", "sqs", "sns", "lambda", "ec2", "cloudwatch", "api_gateway"):
        response = client.get(f"/api/services/{service}")
        assert response.status_code == 200, (service, response.json)
        assert response.json["ok"] is True