import pytest

from app.config import AppConfig


def test_defaults_target_local_floci():
    config = AppConfig.from_env({})
    assert config.endpoint_url == "http://127.0.0.1:4566"
    assert config.region == "us-east-1"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://192.168.1.20:4566",
        "http://user:pass@127.0.0.1:4566",
        "http://127.0.0.1:4566/path",
    ],
)
def test_non_loopback_or_ambiguous_endpoint_is_rejected(endpoint):
    with pytest.raises(ValueError):
        AppConfig.from_env({"endpoint_url": endpoint})