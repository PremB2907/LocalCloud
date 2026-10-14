from dataclasses import dataclass
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AppConfig:
    endpoint_url: str = "http://127.0.0.1:4566"
    region: str = "us-east-1"
    access_key_id: str = "test"
    secret_access_key: str = "test"

    @classmethod
    def from_env(cls, overrides=None):
        values = {
            "endpoint_url": os.environ.get("FLOCI_ENDPOINT_URL", cls.endpoint_url),
            "region": os.environ.get("AWS_REGION", cls.region),
            "access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", cls.access_key_id),
            "secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", cls.secret_access_key),
        }
        values.update(overrides or {})
        config = cls(**values)
        config.validate()
        return config

    def validate(self):
        endpoint = urlsplit(self.endpoint_url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("FLOCI_ENDPOINT_URL must be an HTTP loopback URL")
        if not self.region or not self.access_key_id or not self.secret_access_key:
            raise ValueError("Local region and emulator credentials must be non-empty")