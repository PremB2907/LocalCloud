"""Configurable SMTP adapter for LocalCloud email delivery.

LocalCloud delivers SNS email subscriptions itself. The emulator records the
subscription but never sends real email, so LocalCloud owns delivery.

Configuration comes from environment variables only (see ``.env.example``):

- SMTP_HOST: SMTP relay host (required).
- SMTP_PORT: SMTP port (default ``587``).
- SMTP_USERNAME: SMTP user name (required for authenticated relays).
- SMTP_PASSWORD: SMTP password / app password (required).
- SMTP_FROM_EMAIL: sender address (required).
- SMTP_USE_TLS: use STARTTLS (default ``true``).

Credentials are read from the environment and are never logged, stored in the
browser, or written to disk by this module.
"""

import os
import smtplib


DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_USE_TLS = True


def _env(name, default=None):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_bool(name, default):
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_configured():
    """Return True when usable SMTP settings are present in the environment."""
    return bool(_env("SMTP_HOST") and _env("SMTP_USERNAME") and _env("SMTP_FROM_EMAIL")) and bool(
        _env("SMTP_PASSWORD", "")
    )


class SmtpDeliveryResult:
    """Outcome of one email delivery attempt."""

    def __init__(self, ok, status_code=200, detail=""):
        self.ok = ok
        self.status_code = status_code
        self.detail = detail

    def as_dict(self):
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "detail": self.detail,
        }


def is_configured():
    """Return True when usable SMTP settings are present in the environment."""
    return bool(_env("SMTP_HOST") and _env("SMTP_USERNAME") and _env("SMTP_FROM_EMAIL")) and bool(
        _env("SMTP_PASSWORD", "")
    )


def _missing_reason():
    missing = []
    if not _env("SMTP_HOST"):
        missing.append("SMTP_HOST")
    if not _env("SMTP_USERNAME"):
        missing.append("SMTP_USERNAME")
    if not _env("SMTP_PASSWORD"):
        missing.append("SMTP_PASSWORD")
    if not _env("SMTP_FROM_EMAIL"):
        missing.append("SMTP_FROM_EMAIL")
    return "Missing or incomplete SMTP settings: " + ", ".join(missing) + "."


def send_email(to, subject, body):
    """Send one email through the configured SMTP relay.

    ``to`` is a single recipient address. The return value describes the
    outcome; connection and authentication failures are reported as failed
    results instead of raised, so callers can surface friendly messages.
    """
    if not is_configured():
        return SmtpDeliveryResult(
            ok=False, status_code=503, detail=_missing_reason()
        )

    host = _env("SMTP_HOST")
    port = _env("SMTP_PORT", str(DEFAULT_SMTP_PORT))
    username = _env("SMTP_USERNAME")
    password = _env("SMTP_PASSWORD")
    sender = _env("SMTP_FROM_EMAIL")
    use_tls = _env_bool("SMTP_USE_TLS", DEFAULT_SMTP_USE_TLS)

    message = (
        f"From: {sender}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{body}\r\n"
    ).encode("utf-8")

    try:
        server = smtplib.SMTP(host, int(port), timeout=10)
    except OSError as error:
        return SmtpDeliveryResult(ok=False, status_code=502, detail=f"SMTP host {host} is not reachable ({type(error).__name__}).")
    try:
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.sendmail(sender, [to], message)
    except smtplib.SMTPAuthenticationError:
        return SmtpDeliveryResult(
            ok=False, status_code=502, detail="SMTP authentication failed. Check the app password."
        )
    except smtplib.SMTPException as error:
        return SmtpDeliveryResult(ok=False, status_code=502, detail=f"SMTP rejected the message ({type(error).__name__}).")
    except OSError as error:
        return SmtpDeliveryResult(ok=False, status_code=502, detail="The SMTP server did not respond.")
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass
    return SmtpDeliveryResult(ok=True)
