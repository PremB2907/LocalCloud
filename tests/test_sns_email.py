"""LocalCloud-owned email delivery for SNS email subscriptions.

These tests use a mocked SMTP transport; no real email is sent.
"""

import app.services.smtp as smtp_module
import pytest


@pytest.fixture
def email_env(monkeypatch):
    """Provide a fully configured SMTP environment without real credentials."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "localcloud-test-user")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "localcloud@example.test")
    monkeypatch.setenv("SMTP_USE_TLS", "true")


class FakeSmtpServer:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.starttls_called = False
        self.logged_in = None
        self.sent = []
        FakeSmtpServer.instances.append(self)

    def starttls(self):
        self.starttls_called = True

    def login(self, username, password):
        self.logged_in = (username, password)
        if username != "localcloud-test-user":
            raise self.authentication_failure()

    def sendmail(self, sender, recipients, message):
        self.sent.append((sender, recipients, message))

    def quit(self):
        pass


def authentication_failure(self):
    import smtplib

    return smtplib.SMTPAuthenticationError(
        (535, b"localcloud test failure: bad credentials", None)
    )


@pytest.fixture
def fake_smtp(monkeypatch):
    """Replace the real smtplib.SMTP with the in-memory fake server."""
    import smtplib

    FakeSmtpServer.instances = []
    monkeypatch.setattr(
        smtp_module.smtplib,
        "SMTP",
        lambda host, port, timeout: FakeSmtpServer(host, port, timeout),
    )
    return FakeSmtpServer


def test_missing_smtp_configuration_reports_clear_failure(monkeypatch):
    for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL"):
        monkeypatch.delenv(name, raising=False)

    result = smtp_module.send_email("user@example.com", "LocalCloud test", "Body")

    assert result.ok is False
    assert result.status_code == 503
    assert "SMTP_HOST" in result.detail
    assert "SMTP_PASSWORD" in result.detail
    assert "app-password" not in result.detail


def test_configured_smtp_delivers_and_records_the_message(email_env, fake_smtp):
    result = smtp_module.send_email("user@example.com", "LocalCloud test", "Hello LocalCloud")

    assert result.ok is True
    assert result.status_code == 200
    server = FakeSmtpServer.instances[-1]
    assert server.starttls_called is True
    assert server.logged_in == ("localcloud-test-user", "app-password")
    sender, recipients, message = server.sent[-1]
    assert sender == "localcloud@example.test"
    assert recipients == ["user@example.com"]
    assert b"Hello LocalCloud" in message


def test_configured_smtp_reports_authentication_failures(email_env, monkeypatch):
    class RejectingServer(FakeSmtpServer):
        def login(self, username, password):
            import smtplib

            raise smtplib.SMTPAuthenticationError(535, "bad credentials")

    monkeypatch.setattr(
        smtp_module.smtplib,
        "SMTP",
        lambda host, port, timeout: RejectingServer(host, port, timeout),
    )

    result = smtp_module.send_email("user@example.com", "LocalCloud test", "Hello")

    assert result.ok is False
    assert result.status_code == 502
    assert "authentication" in result.detail.lower()
    assert "app-password" not in result.detail


class FakeSnsClient:
    def subscribe(self, **kwargs):
        return {
            "SubscriptionArn": f"arn:aws:sns:us-east-1:000000000000:{kwargs['Protocol']}-sub",
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }

    def list_subscriptions_by_topic(self, **kwargs):
        return {"Subscriptions": []}

    def publish(self, **kwargs):
        return {"MessageId": "message-1", "ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_paginator(self, operation):
        return self

    def paginate(self, **kwargs):
        return [{"Subscriptions": []}]


def _email_subscription_registry(app):
    registry = app.extensions["console_email_subscriptions"]
    registry.clear()
    return registry


def _register_email_subscription(app, endpoint):
    registry = _email_subscription_registry(app)
    subscription_arn = "arn:aws:sns:us-east-1:000000000000:email-sub"
    registry[subscription_arn] = {
        "topic_arn": "arn:aws:sns:us-east-1:000000000000:localcloud-demo-topic",
        "protocol": "email",
        "endpoint": endpoint,
        "subscription_arn": subscription_arn,
        "status": "smtp_not_configured",
        "attempts": 0,
        "last_delivery": None,
        "last_detail": None,
    }
    return subscription_arn


def test_email_subscription_exposes_localcloud_delivery_status(app, monkeypatch):
    import os

    for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL"):
        os.environ.pop(name, None)
    monkeypatch.setattr("app.routes._client", lambda _name: FakeSnsClient())

    app.extensions["console_email_subscriptions"].clear()
    client = app.test_client()

    response = client.post("/api/services/sns/subscribe", json={
        "topic_arn": "arn:aws:sns:us-east-1:000000000000:localcloud-demo-topic",
        "protocol": "email",
        "endpoint": "user@example.com",
    })
    assert response.status_code == 201, response.json
    subscription_arn = response.json["resource"]["subscription_arn"]
    assert subscription_arn.endswith("email-sub")

    registry = app.extensions["console_email_subscriptions"]
    assert subscription_arn in registry
    assert registry[subscription_arn]["status"] == "smtp_not_configured"
    assert registry[subscription_arn]["attempts"] == 0

    listing = client.get(
        "/api/services/sns/deliveries"
        "?topic_arn=arn:aws:sns:us-east-1:000000000000:localcloud-demo-topic"
    )
    assert listing.status_code == 200
    assert listing.json["deliveries"][0]["status"] == "smtp_not_configured"


def test_email_delivery_succeeds_when_the_smtp_relay_is_configured(app, monkeypatch):
    def configured_send(to, subject, body):
        return smtp_module.SmtpDeliveryResult(ok=True, status_code=200, detail=None)

    monkeypatch.setattr(smtp_module, "send_email", configured_send)
    monkeypatch.setattr("app.routes._client", lambda _name: FakeSnsClient())
    subscription_arn = _register_email_subscription(app, "user@example.com")

    client = app.test_client()
    response = client.post("/api/services/sns/publish", json={
        "topic_arn": "arn:aws:sns:us-east-1:000000000000:localcloud-demo-topic",
        "message": "Hello subscribers",
    })
    assert response.status_code == 201, response.json
    body = response.json
    assert body["message_id"] == "message-1"
    assert body["deliveries"]["delivered"] == [
        {"endpoint": "user@example.com", "ok": True, "status_code": 200}
    ]

    registry = app.extensions["console_email_subscriptions"]
    record = registry[subscription_arn]
    assert record["status"] == "delivered"
    assert record["attempts"] == 1
    assert record["last_detail"] is None


def test_email_delivery_reports_a_clear_failure_without_smtp_credentials(app, monkeypatch):
    def failing_send(to, subject, body):
        return smtp_module.SmtpDeliveryResult(
            ok=False,
            status_code=503,
            detail="Missing or incomplete SMTP settings: SMTP_PASSWORD.",
        )

    monkeypatch.setattr(smtp_module, "send_email", failing_send)
    monkeypatch.setattr("app.routes._client", lambda _name: FakeSnsClient())
    subscription_arn = _register_email_subscription(app, "user@example.com")

    client = app.test_client()
    response = client.post("/api/services/sns/publish", json={
        "topic_arn": "arn:aws:sns:us-east-1:000000000000:localcloud-demo-topic",
        "message": "Hello subscribers",
    })
    assert response.status_code == 201, response.json

    registry = app.extensions["console_email_subscriptions"]
    record = registry[subscription_arn]
    assert record["status"] == "failed"
    assert record["last_detail"] == "Missing or incomplete SMTP settings: SMTP_PASSWORD."
    assert record["attempts"] == 1

    listing = client.get(
        "/api/services/sns/deliveries"
        "?topic_arn=arn:aws:sns:us-east-1:000000000000:localcloud-demo-topic"
    )
    assert listing.status_code == 200
    assert listing.json["deliveries"][0]["last_detail"].startswith("Missing")
