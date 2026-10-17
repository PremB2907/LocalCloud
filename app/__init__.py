from flask import Flask
import os

from app.clients import ClientProvider
from app.config import AppConfig
from app.routes import console
from app.services.ec2_metrics_collector import Ec2MetricsCollector


def _load_dotenv(path=".env"):
    """Load ``KEY=VALUE`` lines from ``.env`` into ``os.environ`` without
    overwriting variables already present in the environment.

    A small, dependency-free subset of the dotenv format: blank lines and
    comments are skipped, an optional ``export `` prefix is stripped, and
    optional surrounding single or double quotes are removed. Values are
    never printed, logged, or committed; ``.env`` itself is gitignored.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def create_app(test_config=None):
    _load_dotenv()
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
    app.extensions["ec2_metrics_collector"] = Ec2MetricsCollector(
        get_ec2_client=lambda: app.extensions["aws_clients"].get("ec2"),
        interval_seconds=int(os.environ.get("LC_EC2_METRICS_INTERVAL", "60")),
        max_instances=int(os.environ.get("LC_EC2_METRICS_MAX_INSTANCES", "10")),
    )
    app.extensions["recent_operations"] = []
    app.extensions["console_created_buckets"] = set()
    app.extensions["console_created_tables"] = set()
    app.extensions["console_created_apis"] = set()
    app.extensions["console_created_instances"] = set()
    app.extensions["console_created_functions"] = set()
    app.extensions["console_created_log_groups"] = set()
    app.extensions["console_created_queues"] = set()
    app.extensions["console_created_topics"] = set()
    app.extensions["console_created_key_pairs"] = set()
    app.extensions["received_messages"] = {}
    app.extensions["console_email_subscriptions"] = {}
    app.register_blueprint(console)
    return app