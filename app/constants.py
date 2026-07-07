"""
app/constants.py
================
Global constants for Watchtower.

This is the single authoritative source for every fixed value in the
application.  Nothing is duplicated elsewhere.

Maintenance
-----------
* All TryRating URLs live here — update when the site changes domains.
* Browser args are tuned for headless OCI Linux — do not modify lightly.
* Timeout values are in milliseconds unless stated otherwise.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------

# Semantic versioning: MAJOR.MINOR.PATCH
# 1.0.0 — Initial production release
# 1.1.0 — Navigation optimisation (stay on survey page), XPath extraction,
#          startup health check, update.sh auto-deploy, URL corrections
APP_VERSION: str = "1.1.0"
APP_NAME: str = "Watchtower"

# ---------------------------------------------------------------------------
# Project-root-relative paths
# ---------------------------------------------------------------------------
# constants.py lives at app/constants.py → .parent → app/ → .parent → root

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
CONFIG_DIR: Path = PROJECT_ROOT / "config"
STATE_FILE: Path = DATA_DIR / "state.json"

# ---------------------------------------------------------------------------
# TryRating URLs
# ---------------------------------------------------------------------------
# Confirmed from live site screenshots (2025-07).
# Base domain: tryrating.com (NOT app.tryrating.com)

TRYRATING_BASE_URL: str = "https://tryrating.com"
TRYRATING_LOGIN_URL: str = "https://tryrating.com/login"
TRYRATING_HOME_URL: str = "https://tryrating.com/app/home"      # post-login landing
TRYRATING_SURVEYS_URL: str = "https://tryrating.com/app/survey/rate"  # confirmed

# ---------------------------------------------------------------------------
# Playwright timeouts  (milliseconds)
# ---------------------------------------------------------------------------

BROWSER_LAUNCH_TIMEOUT: int = 30_000    # max time to launch Chromium
PAGE_LOAD_TIMEOUT: int = 60_000         # max time for a page to load
ELEMENT_WAIT_TIMEOUT: int = 30_000      # default element visibility wait
NAVIGATION_TIMEOUT: int = 60_000        # default navigation wait
SHORT_ELEMENT_TIMEOUT: int = 5_000      # used for optional / quick checks
LOGIN_SUCCESS_TIMEOUT: int = 15_000     # extra time for post-login redirect

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

MAX_LOGIN_RETRIES: int = 3
MAX_BROWSER_RESTART_RETRIES: int = 3
MAX_SMTP_RETRIES: int = 3
SMTP_RETRY_DELAY_SECONDS: int = 30      # seconds between SMTP retry attempts
LOGIN_RETRY_DELAY_SECONDS: float = 2.0  # seconds between login retry attempts

# ---------------------------------------------------------------------------
# Browser launch arguments
# ---------------------------------------------------------------------------
# Optimised for a headless Oracle Linux server with limited shared memory.

BROWSER_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--no-first-run",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--metrics-recording-only",
    "--mute-audio",
    "--disable-default-apps",
    "--safebrowsing-disable-auto-update",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]

# ---------------------------------------------------------------------------
# Log settings
# ---------------------------------------------------------------------------

LOG_ROTATION: str = "1 day"
LOG_RETENTION: str = "30 days"
LOG_FORMAT: str = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)
LOG_FILE_FORMAT: str = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{function}:{line} | "
    "{message}"
)

# ---------------------------------------------------------------------------
# Scheduler job identifiers
# ---------------------------------------------------------------------------

JOB_SURVEY_CHECK: str = "survey_check"
JOB_HEARTBEAT: str = "heartbeat"
JOB_LOG_CLEANUP: str = "log_cleanup"

# ---------------------------------------------------------------------------
# Heartbeat schedule  (daily cron)
# ---------------------------------------------------------------------------

HEARTBEAT_HOUR: int = 8     # 08:00 in the configured timezone
HEARTBEAT_MINUTE: int = 0

# ---------------------------------------------------------------------------
# Wait / jitter times
# ---------------------------------------------------------------------------
# Using seconds here (converted to ms where Playwright requires it).

POST_LOGIN_WAIT_S: float = 2.0       # pause after submitting login form
POST_NAVIGATE_WAIT_S: float = 1.5    # pause after clicking a nav link
POST_SURVEY_CLICK_WAIT_S: float = 3.0  # pause after clicking Get Surveys
JITTER_MIN_MS: int = 500             # minimum random pre-check jitter
JITTER_MAX_MS: int = 5_000           # maximum random pre-check jitter

# How often to run a full auth verification even while on the survey page.
# Catches slow session expiry without penalising every check cycle.
AUTH_CHECK_INTERVAL_S: int = 30 * 60   # 30 minutes

# ---------------------------------------------------------------------------
# State JSON field keys
# ---------------------------------------------------------------------------

STATE_KEY_SEEN_IDS: str = "seen_request_ids"
STATE_KEY_LAST_LOGIN: str = "last_login"
STATE_KEY_LAST_CHECK: str = "last_check"
STATE_KEY_HEARTBEAT: str = "heartbeat"
