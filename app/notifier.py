"""
app/notifier.py
===============
Gmail SMTP email notifications for Watchtower.

Responsibilities
----------------
* Build plain-text email messages for two notification types:
  1. **Survey alert** — a new TryRating survey was detected.
  2. **Heartbeat**    — daily proof-of-life email.
* Transmit messages via Gmail STARTTLS (port 587).
* Retry transient SMTP failures up to ``MAX_SMTP_RETRIES`` times.
* Never expose credentials in logs.

Design notes
------------
* ``smtplib.SMTP`` is used as a context manager — the connection is
  closed after every send attempt, even on error.  This avoids leaked
  connections in long-running processes.
* ``SMTPAuthenticationError`` is not retried — it will not improve
  without human intervention (wrong credentials).
* Both sender and recipient are ``config.smtp_email``.  The spec
  requires sending to self; extend ``_send_email`` recipients list to
  add CCs or separate To/From in a future version.
"""

from __future__ import annotations

import smtplib
import socket
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import Config
from app.constants import APP_VERSION, MAX_SMTP_RETRIES, SMTP_RETRY_DELAY_SECONDS
from app.logger import logger


# ---------------------------------------------------------------------------
# Email builders
# ---------------------------------------------------------------------------

def _build_survey_email(
    config: Config,
    request_id: str,
    detected_at: str,
) -> MIMEMultipart:
    """
    Construct the survey-alert :class:`~email.mime.multipart.MIMEMultipart`.

    Parameters
    ----------
    config:
        Application configuration.
    request_id:
        The TryRating Request ID that triggered this notification.
    detected_at:
        ISO 8601 timestamp of detection.
    """
    hostname = socket.gethostname()
    server_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = "🚨 Watchtower — New TryRating Survey"
    body = (
        "Watchtower has detected a new TryRating survey.\n"
        "\n"
        f"Request ID       : {request_id}\n"
        f"Detected At      : {detected_at}\n"
        f"Hostname         : {hostname}\n"
        f"Server Time      : {server_time}\n"
        "\n"
        "Log in to TryRating (https://www.tryrating.com/app/survey) and accept the survey before it expires.\n"
        "\n"
        "---\n"
        f"Watchtower {APP_VERSION} — automated monitoring"
    )

    msg = MIMEMultipart()
    msg["From"] = config.smtp_email
    msg["To"] = config.smtp_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_heartbeat_email(
    config: Config,
    last_check: str,
    uptime_seconds: float,
) -> MIMEMultipart:
    """
    Construct the daily heartbeat :class:`~email.mime.multipart.MIMEMultipart`.

    Parameters
    ----------
    config:
        Application configuration.
    last_check:
        ISO 8601 timestamp of the most recent completed survey check.
    uptime_seconds:
        Elapsed seconds since the Watchtower process started.
    """
    hostname = socket.gethostname()
    server_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    uptime_hours = uptime_seconds / 3600.0

    subject = f"💚 Watchtower — Daily Heartbeat ({hostname})"
    body = (
        "Watchtower is running normally. No action required.\n"
        "\n"
        f"Server           : {hostname}\n"
        f"Server Time      : {server_time}\n"
        f"Application      : {config.watchtower_name} {APP_VERSION}\n"
        f"Last Survey Check: {last_check or 'N/A'}\n"
        f"Uptime           : {uptime_hours:.1f} hours\n"
        "\n"
        "---\n"
        f"Watchtower {APP_VERSION} — automated monitoring"
    )

    msg = MIMEMultipart()
    msg["From"] = config.smtp_email
    msg["To"] = config.smtp_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _send_email(config: Config, msg: MIMEMultipart) -> bool:
    """
    Transmit *msg* via Gmail STARTTLS SMTP.

    Retries transient failures up to ``MAX_SMTP_RETRIES`` times.
    Authentication errors short-circuit immediately (no retry).

    Parameters
    ----------
    config:
        Application configuration — SMTP settings read here.
    msg:
        Fully-constructed MIME message.

    Returns
    -------
    bool
        ``True`` if the message was accepted by the SMTP server.
    """
    subject = msg.get("Subject", "<no subject>")

    for attempt in range(1, MAX_SMTP_RETRIES + 1):
        try:
            logger.info(
                "Sending email — attempt {}/{} — subject='{}'.",
                attempt,
                MAX_SMTP_RETRIES,
                subject,
            )
            with smtplib.SMTP(
                host=config.smtp_host,
                port=config.smtp_port,
                timeout=30,
            ) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                # smtp_app_password intentionally not logged
                server.login(config.smtp_email, config.smtp_app_password)
                server.sendmail(
                    from_addr=config.smtp_email,
                    to_addrs=[config.smtp_email],
                    msg=msg.as_string(),
                )

            logger.info("Email sent successfully — subject='{}'.", subject)
            return True

        except smtplib.SMTPAuthenticationError as exc:
            # Wrong credentials will not improve with retries.
            logger.error(
                "SMTP authentication failed — verify SMTP_APP_PASSWORD. "
                "Error: {}",
                exc,
            )
            return False

        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            logger.warning(
                "Email attempt {}/{} failed: {}", attempt, MAX_SMTP_RETRIES, exc
            )
            if attempt < MAX_SMTP_RETRIES:
                logger.info(
                    "Retrying email in {}s.", SMTP_RETRY_DELAY_SECONDS
                )
                time.sleep(SMTP_RETRY_DELAY_SECONDS)

    logger.error(
        "All {} email attempts exhausted — subject='{}'.",
        MAX_SMTP_RETRIES,
        subject,
    )
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_survey_notification(
    config: Config,
    request_id: str,
    detected_at: Optional[str] = None,
) -> bool:
    """
    Send an alert email for a newly detected TryRating survey.

    Parameters
    ----------
    config:
        Application configuration.
    request_id:
        The TryRating Request ID that was detected.
    detected_at:
        ISO 8601 detection timestamp.  Defaults to current UTC time.

    Returns
    -------
    bool
        ``True`` if the email was accepted by the SMTP server.
    """
    if detected_at is None:
        detected_at = datetime.now(tz=timezone.utc).isoformat()

    msg = _build_survey_email(config, request_id, detected_at)
    return _send_email(config, msg)


def send_heartbeat(
    config: Config,
    last_check: str,
    uptime_seconds: float,
) -> bool:
    """
    Send the daily heartbeat proof-of-life email.

    Parameters
    ----------
    config:
        Application configuration.
    last_check:
        ISO 8601 timestamp of the last completed survey check
        (sourced from :class:`~app.state.StateManager`).
    uptime_seconds:
        Seconds elapsed since application start.

    Returns
    -------
    bool
        ``True`` if the email was accepted by the SMTP server.
    """
    msg = _build_heartbeat_email(config, last_check, uptime_seconds)
    return _send_email(config, msg)
