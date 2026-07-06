"""
app/config.py
=============
Centralised configuration for Watchtower.

All runtime settings are loaded from a .env file located at the project
root.  The :class:`Config` dataclass is the single source of truth for
every tuneable parameter in the application.

Design decisions
----------------
* Dataclass + ``__post_init__`` validation  → explicit contract, zero
  surprises at runtime.
* ``Config.load()`` classmethod              → clean, testable factory;
  no hidden global state.
* ``python-dotenv`` override=False           → system environment takes
  priority over the .env file (useful in CI / container overrides).
* All secrets are type-validated but never logged (see safe_repr).
* Path values use :class:`pathlib.Path` throughout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require(key: str, raw: Optional[str]) -> str:
    """Return *raw* if it is non-empty, otherwise raise ``EnvironmentError``."""
    if not raw or not raw.strip():
        raise EnvironmentError(
            f"Required environment variable '{key}' is missing or empty. "
            "Check your .env file."
        )
    return raw.strip()


def _require_int(key: str, raw: Optional[str]) -> int:
    """Parse *raw* as a positive integer or raise ``EnvironmentError``."""
    value = _require(key, raw)
    try:
        parsed = int(value)
    except ValueError:
        raise EnvironmentError(
            f"Environment variable '{key}' must be an integer, got: '{value}'"
        )
    if parsed <= 0:
        raise EnvironmentError(
            f"Environment variable '{key}' must be a positive integer, got: {parsed}"
        )
    return parsed


def _require_float(key: str, raw: Optional[str]) -> float:
    """Parse *raw* as a non-negative float or raise ``EnvironmentError``."""
    value = _require(key, raw)
    try:
        parsed = float(value)
    except ValueError:
        raise EnvironmentError(
            f"Environment variable '{key}' must be a number, got: '{value}'"
        )
    if parsed < 0:
        raise EnvironmentError(
            f"Environment variable '{key}' must be >= 0, got: {parsed}"
        )
    return parsed


def _require_bool(key: str, raw: Optional[str]) -> bool:
    """Parse *raw* as a boolean (``true/false/1/0/yes/no``) or raise."""
    value = _require(key, raw).lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    raise EnvironmentError(
        f"Environment variable '{key}' must be a boolean "
        f"(true/false/1/0/yes/no), got: '{value}'"
    )


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """
    Immutable, fully-validated application configuration.

    Instantiate via :meth:`Config.load` — do **not** construct directly.

    Attributes
    ----------
    watchtower_name : str
        Human-readable name of this Watchtower instance.
    check_interval : int
        Survey polling interval in **seconds**.
    headless : bool
        Run Chromium in headless mode (``True`` in production).
    slow_mo : float
        Playwright slow-motion delay in milliseconds.  Zero in production.
    default_timeout : int
        Default Playwright element-wait timeout in milliseconds.
    timezone : str
        IANA timezone string used for scheduled jobs and log timestamps.
    log_level : str
        Loguru log level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
    tryrating_username : str
        TryRating login e-mail / username.
    tryrating_password : str
        TryRating login password.  **Never logged.**
    smtp_host : str
        SMTP server hostname (e.g. ``smtp.gmail.com``).
    smtp_port : int
        SMTP server port (e.g. ``587`` for STARTTLS).
    smtp_email : str
        Sender / recipient e-mail address.
    smtp_app_password : str
        SMTP authentication token or app password.  **Never logged.**
    env_path : Path
        Absolute path to the .env file that was loaded.
    """

    # -- General --------------------------------------------------------------
    watchtower_name: str
    check_interval: int          # seconds
    headless: bool
    slow_mo: float               # milliseconds
    default_timeout: int         # milliseconds
    timezone: str
    log_level: str

    # -- TryRating credentials ------------------------------------------------
    tryrating_username: str
    tryrating_password: str      # never log

    # -- SMTP -----------------------------------------------------------------
    smtp_host: str
    smtp_port: int
    smtp_email: str
    smtp_app_password: str       # never log

    # -- Meta -----------------------------------------------------------------
    env_path: Path = field(compare=False, repr=False)

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def __post_init__(self) -> None:
        """Validate inter-field constraints after construction."""
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_log_levels:
            raise EnvironmentError(
                f"LOG_LEVEL must be one of {valid_log_levels}, "
                f"got: '{self.log_level}'"
            )
        if self.smtp_port not in range(1, 65536):
            raise EnvironmentError(
                f"SMTP_PORT must be between 1 and 65535, got: {self.smtp_port}"
            )
        if "@" not in self.smtp_email:
            raise EnvironmentError(
                f"SMTP_EMAIL does not look like a valid address: '{self.smtp_email}'"
            )

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    def load(cls, env_file: Optional[Path] = None) -> "Config":
        """
        Load and validate configuration from a .env file.

        Parameters
        ----------
        env_file:
            Path to the .env file.  Defaults to ``<project_root>/.env``
            (two directories above this module).

        Returns
        -------
        Config
            A fully validated, immutable :class:`Config` instance.

        Raises
        ------
        EnvironmentError
            If any required variable is missing, empty, or malformed.
        FileNotFoundError
            If the resolved .env path does not exist.
        """
        # Resolve .env path: app/config.py -> app/ -> project_root/
        if env_file is None:
            env_file = Path(__file__).resolve().parent.parent / ".env"

        if not env_file.exists():
            raise FileNotFoundError(
                f".env file not found at expected path: {env_file}\n"
                "Copy .env.example to .env and populate it before starting."
            )

        # Load into os.environ without overriding existing system env vars
        load_dotenv(dotenv_path=env_file, override=False)

        g = os.getenv  # local alias for brevity

        return cls(
            # -- General --------------------------------------------------
            watchtower_name=_require("WATCHTOWER_NAME", g("WATCHTOWER_NAME")),
            check_interval=_require_int("CHECK_INTERVAL", g("CHECK_INTERVAL")),
            headless=_require_bool("HEADLESS", g("HEADLESS")),
            slow_mo=_require_float("SLOW_MO", g("SLOW_MO")),
            default_timeout=_require_int("DEFAULT_TIMEOUT", g("DEFAULT_TIMEOUT")),
            timezone=_require("TIMEZONE", g("TIMEZONE")),
            log_level=_require("LOG_LEVEL", g("LOG_LEVEL")),

            # -- TryRating ------------------------------------------------
            tryrating_username=_require(
                "TRYRATING_USERNAME", g("TRYRATING_USERNAME")
            ),
            tryrating_password=_require(
                "TRYRATING_PASSWORD", g("TRYRATING_PASSWORD")
            ),

            # -- SMTP -----------------------------------------------------
            smtp_host=_require("SMTP_HOST", g("SMTP_HOST")),
            smtp_port=_require_int("SMTP_PORT", g("SMTP_PORT")),
            smtp_email=_require("SMTP_EMAIL", g("SMTP_EMAIL")),
            smtp_app_password=_require(
                "SMTP_APP_PASSWORD", g("SMTP_APP_PASSWORD")
            ),

            # -- Meta -----------------------------------------------------
            env_path=env_file,
        )

    # -------------------------------------------------------------------------
    # Safe representation (no secrets)
    # -------------------------------------------------------------------------

    def safe_repr(self) -> str:
        """
        Return a log-safe string representation.

        Passwords and app tokens are **redacted** — this string is safe
        to write to log files or include in heartbeat emails.
        """
        return (
            f"Config("
            f"name={self.watchtower_name!r}, "
            f"check_interval={self.check_interval}s, "
            f"headless={self.headless}, "
            f"slow_mo={self.slow_mo}ms, "
            f"default_timeout={self.default_timeout}ms, "
            f"timezone={self.timezone!r}, "
            f"log_level={self.log_level!r}, "
            f"tryrating_username={self.tryrating_username!r}, "
            f"tryrating_password=<REDACTED>, "
            f"smtp_host={self.smtp_host!r}, "
            f"smtp_port={self.smtp_port}, "
            f"smtp_email={self.smtp_email!r}, "
            f"smtp_app_password=<REDACTED>"
            f")"
        )
