from app.clients import ClientProvider
from app.config import AppConfig


def test_client_uses_explicit_local_endpoint_credentials_and_s3_path_style(monkeypatch):
    calls = []

    def fake_client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return object()

    monkeypatch.setattr("app.clients.boto3.client", fake_client)
    config = AppConfig.from_env({})
    provider = ClientProvider(config)
    assert provider.get("s3") is provider.get("s3")
    service_name, options = calls[0]
    assert service_name == "s3"
    assert options["endpoint_url"] == "http://127.0.0.1:4566"
    assert options["region_name"] == "us-east-1"
    assert options["aws_access_key_id"] == "test"
    assert options["aws_secret_access_key"] == "test"
    assert options["config"].s3 == {"addressing_style": "path"}


def test_only_known_aws_clients_can_be_created():
    provider = ClientProvider(AppConfig.from_env({}))
    try:
        provider.get("unknown")
    except KeyError as error:
        assert "unknown" in str(error)
    else:
        raise AssertionError("unknown client was accepted")