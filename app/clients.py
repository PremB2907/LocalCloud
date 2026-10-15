import boto3
from botocore.config import Config


SERVICE_CLIENTS = {
    "s3": "s3",
    "dynamodb": "dynamodb",
    "sqs": "sqs",
    "sns": "sns",
    "lambda": "lambda",
    "ec2": "ec2",
    "ssm": "ssm",
    "cloudwatch": "cloudwatch",
    "logs": "logs",
    "apigateway": "apigateway",
    "apigatewayv2": "apigatewayv2",
}


class ClientProvider:
    def __init__(self, app_config):
        self.app_config = app_config
        self._clients = {}

    def get(self, service):
        if service not in SERVICE_CLIENTS:
            raise KeyError(f"Unknown AWS-compatible service: {service}")
        if service not in self._clients:
            timeout = 60 if service in ("ec2", "lambda") else 10
            client_config = Config(
                connect_timeout=3,
                read_timeout=timeout,
                retries={"max_attempts": 0},
            )
            if service == "s3":
                client_config = Config(
                    s3={"addressing_style": "path"},
                    connect_timeout=3,
                    read_timeout=timeout,
                    retries={"max_attempts": 0},
                )
            self._clients[service] = boto3.client(
                SERVICE_CLIENTS[service],
                endpoint_url=self.app_config.endpoint_url,
                region_name=self.app_config.region,
                aws_access_key_id=self.app_config.access_key_id,
                aws_secret_access_key=self.app_config.secret_access_key,
                config=client_config,
            )
        return self._clients[service]