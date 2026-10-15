from flask import Flask

from app.clients import ClientProvider
from app.config import AppConfig
from app.routes import console


def create_app(test_config=None):
    config = AppConfig.from_env()
    overrides = test_config or {}
    config_values = {
        "endpoint_url": overrides.get("FLOCI_ENDPOINT_URL", config.endpoint_url),
        "region": overrides.get("AWS_REGION", config.region),
        "access_key_id": overrides.get("AWS_ACCESS_KEY_ID", config.access_key_id),
        "secret_access_key": overrides.get("AWS_SECRET_ACCESS_KEY", config.secret_access_key),
    }
    config = AppConfig(**config_values)
    config.validate()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        TESTING=bool(overrides.get("TESTING", False)),
        MAX_CONTENT_LENGTH=11 * 1024 * 1024,
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "::1"],
    )
    app.extensions["floci_config"] = config
    app.extensions["aws_clients"] = ClientProvider(config)
    app.extensions["recent_operations"] = []
    app.extensions["console_created_tables"] = set()
    app.extensions["console_created_apis"] = set()
    app.extensions["console_created_instances"] = set()
    app.extensions["console_created_functions"] = set()
    app.extensions["received_messages"] = {}
    app.register_blueprint(console)
    return app