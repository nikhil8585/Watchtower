"""
app/health.py
=============
Startup self-check for Watchtower.

Runs every check before the scheduler starts.  If any check fails,
the process exits immediately with a clear, actionable error message
rather than discovering the problem 20 minutes into execution.

Checks performed
----------------
1.  .env file exists and is readable
2.  Log directory exists (or can be created) and is writable
3.  Data directory exists (or can be created) and is writable
4.  state.json exists (created with defaults if missing)
5.  TryRating credentials are non-empty (validated by Config)
6.  SMTP credentials are non-empty (validated by Config)
7.  SMTP authentication — real handshake with Gmail, no email sent
8.  Playwright package is importable
9.  Chromium launches — real browser launch + immediate close

Usage::

    from app.health import run_startup_checks
    run_startup_checks(config)   # raises SystemExit on failure
"""

from __future__ import annotations

import smtplib
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from app.config import Config
from app.constants import BROWSER_ARGS, DATA_DIR, LOGS_DIR, STATE_FILE
from app.logger import logger


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Result of a single health check."""
    name: str
    passed: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_env_file(config: Config, results: List[CheckResult]) -> None:
    """Verify the .env file is present and readable."""
    env_path: Path = config.env_path
    if env_path.exists() and env_path.is_file():
        results.append(CheckResult(".env file", True, str(env_path)))
    else:
        results.append(CheckResult(
            ".env file", False,
            f"Not found at: {env_path}  →  cp .env.example .env"
        ))


def _check_log_directory(results: List[CheckResult]) -> None:
    """Ensure the logs directory exists and is writable."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        test_file = LOGS_DIR / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        results.append(CheckResult("Log directory", True, str(LOGS_DIR)))
    except OSError as exc:
        results.append(CheckResult(
            "Log directory", False,
            f"Cannot write to {LOGS_DIR}: {exc}"
        ))


def _check_data_directory(results: List[CheckResult]) -> None:
    """Ensure the data directory and state.json exist (or can be created)."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        test_file = DATA_DIR / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        results.append(CheckResult("Data directory", True, str(DATA_DIR)))
    except OSError as exc:
        results.append(CheckResult(
            "Data directory", False,
            f"Cannot write to {DATA_DIR}: {exc}"
        ))
        return

    # Verify state.json
    if STATE_FILE.exists():
        results.append(CheckResult("state.json", True, str(STATE_FILE)))
    else:
        # StateManager will create it on first use; pre-create here
        try:
            import json
            STATE_FILE.write_text(
                json.dumps(
                    {"seen_request_ids": [], "last_login": "",
                     "last_check": "", "heartbeat": ""},
                    indent=4,
                )
            )
            results.append(CheckResult(
                "state.json", True,
                f"Created at: {STATE_FILE}"
            ))
        except OSError as exc:
            results.append(CheckResult(
                "state.json", False,
                f"Cannot create {STATE_FILE}: {exc}"
            ))


def _check_tryrating_credentials(config: Config, results: List[CheckResult]) -> None:
    """Verify TryRating credentials are non-empty (syntax only, no network)."""
    if config.tryrating_username and config.tryrating_password:
        results.append(CheckResult(
            "TryRating credentials", True,
            f"Username: {config.tryrating_username}"
        ))
    else:
        results.append(CheckResult(
            "TryRating credentials", False,
            "TRYRATING_USERNAME or TRYRATING_PASSWORD is empty in .env"
        ))


def _check_smtp_credentials(config: Config, results: List[CheckResult]) -> None:
    """Verify SMTP credentials are non-empty (syntax only, no network)."""
    if config.smtp_email and config.smtp_app_password:
        results.append(CheckResult(
            "SMTP credentials", True,
            f"Email: {config.smtp_email}, Host: {config.smtp_host}:{config.smtp_port}"
        ))
    else:
        results.append(CheckResult(
            "SMTP credentials", False,
            "SMTP_EMAIL or SMTP_APP_PASSWORD is empty in .env"
        ))


def _check_smtp_auth(config: Config, results: List[CheckResult]) -> None:
    """
    Perform a real SMTP handshake — connect, STARTTLS, authenticate.

    No email is sent.  This verifies:
    - Network connectivity to smtp.gmail.com:587
    - Gmail App Password is valid
    - 2FA / App Password is correctly configured
    """
    try:
        with smtplib.SMTP(
            host=config.smtp_host,
            port=config.smtp_port,
            timeout=15,
        ) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.smtp_email, config.smtp_app_password)
            # Authenticated successfully — do not send any email
        results.append(CheckResult(
            "SMTP authentication", True,
            f"Authenticated as {config.smtp_email}"
        ))
    except smtplib.SMTPAuthenticationError as exc:
        results.append(CheckResult(
            "SMTP authentication", False,
            f"Auth failed — check SMTP_APP_PASSWORD (needs Gmail App Password): {exc}"
        ))
    except (smtplib.SMTPException, OSError, socket.timeout) as exc:
        results.append(CheckResult(
            "SMTP authentication", False,
            f"Connection failed to {config.smtp_host}:{config.smtp_port}: {exc}"
        ))


def _check_playwright_import(results: List[CheckResult]) -> None:
    """Verify the playwright package is importable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        import playwright
        version = getattr(playwright, "__version__", "unknown")
        results.append(CheckResult(
            "Playwright package", True,
            f"Version: {version}"
        ))
    except ImportError as exc:
        results.append(CheckResult(
            "Playwright package", False,
            f"Not installed: {exc}  →  pip install playwright"
        ))


def _check_chromium_launch(config: Config, results: List[CheckResult]) -> None:
    """
    Actually launch Chromium headless and immediately close it.

    This catches:
    - Missing Chromium binary (playwright install chromium not run)
    - Missing system library dependencies (libX11, libnss3, etc.)
    - Sandbox / permission issues on the server
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=BROWSER_ARGS,
            )
            version = browser.version
            browser.close()
        results.append(CheckResult(
            "Chromium launch", True,
            f"Version: {version}"
        ))
    except Exception as exc:
        results.append(CheckResult(
            "Chromium launch", False,
            f"{exc}\n"
            "    Fix: playwright install chromium && playwright install-deps chromium"
        ))


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

def _report_and_exit_if_failed(results: List[CheckResult]) -> None:
    """
    Log all check results.

    If any check failed, log a clear summary and call ``sys.exit(1)``.
    """
    width = 28
    logger.info("=" * 60)
    logger.info("Watchtower Startup Self-Check")
    logger.info("=" * 60)

    failed: List[CheckResult] = []
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        detail = f"  ({r.detail})" if r.detail else ""
        if r.passed:
            logger.info("  [{}]  {:<{w}}{}", status, r.name, detail, w=width)
        else:
            logger.error("  [{}]  {:<{w}}{}", status, r.name, detail, w=width)
            failed.append(r)

    logger.info("=" * 60)

    if not failed:
        logger.info("All checks passed — starting Watchtower.")
        return

    logger.critical(
        "{} check(s) failed. Watchtower cannot start.", len(failed)
    )
    for r in failed:
        logger.critical("  FAILED: {}  →  {}", r.name, r.detail)
    logger.critical("Fix the issues above and restart.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_startup_checks(config: Config) -> None:
    """
    Run all startup health checks.

    Call this immediately after :func:`~app.config.Config.load` and before
    :meth:`~app.watchtower.Watchtower.start`.

    Parameters
    ----------
    config:
        Fully validated application configuration.

    Raises
    ------
    SystemExit
        With exit code ``1`` if any check fails.
    """
    results: List[CheckResult] = []

    _check_env_file(config, results)
    _check_log_directory(results)
    _check_data_directory(results)
    _check_tryrating_credentials(config, results)
    _check_smtp_credentials(config, results)
    _check_smtp_auth(config, results)
    _check_playwright_import(results)
    _check_chromium_launch(config, results)

    _report_and_exit_if_failed(results)
