"""
app/watchtower.py
=================
Central orchestrator for the Watchtower monitoring platform.

Threading model
---------------
Playwright's sync API uses a greenlet-based event loop that is bound to
the thread where it was created.  Calling any Playwright method from a
different thread raises ``greenlet.error: Cannot switch to a different
thread``.

APScheduler's ``BlockingScheduler`` runs jobs inside a
``ThreadPoolExecutor`` (worker threads), which caused that exact error
every time a session expired and recovery was attempted.

**Fix (v1.1.1):** APScheduler is removed.  The monitoring loop is a plain
``while self._running`` on the main thread with ``time.sleep()``.
Heartbeat and log-cleanup are checked at each cycle and fired when their
schedule is due.  Everything stays on one thread — no greenlet conflicts.

Survey check optimisation
--------------------------
The browser stays on the survey page.  Each cycle:

1. ``/survey/`` in current URL → soft reload, dismiss dialog, click
   "Check Now" directly.  (URL substring check handles query strings.)

2. URL drifted elsewhere → detect why (login? error?) and recover
   without a full auth roundtrip.

3. Periodic full auth (every ``AUTH_CHECK_INTERVAL_S``) → catches slow
   session expiry even while appearing to be on the page.
"""

from __future__ import annotations

import signal
import time
import zoneinfo
from datetime import date, datetime, timezone
from typing import Optional

from app.browser import BrowserManager
from app.config import Config
from app.constants import (
    APP_VERSION,
    AUTH_CHECK_INTERVAL_S,
    HEARTBEAT_HOUR,
    HEARTBEAT_MINUTE,
    LOGS_DIR,
)
from app.logger import logger
from app.login import ensure_authenticated, is_login_required
from app.navigation import navigate_to_surveys
from app.notifier import send_heartbeat, send_survey_notification
from app.state import StateManager
from app.survey_checker import check_for_new_survey


class Watchtower:
    """
    Watchtower monitoring orchestrator.

    Runs entirely on the calling (main) thread.  Never spawns threads.
    All Playwright calls are made in the same thread where the browser
    was launched, satisfying the greenlet single-thread constraint.

    Parameters
    ----------
    config:
        Fully validated, immutable application configuration.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._state = StateManager()
        self._browser = BrowserManager(config)
        self._tz = zoneinfo.ZoneInfo(config.timezone)
        self._start_time: float = time.monotonic()
        self._running: bool = False

        # Auth refresh tracking
        self._last_auth_check: float = 0.0

        # Heartbeat: fire once per day at HEARTBEAT_HOUR:HEARTBEAT_MINUTE
        self._last_heartbeat_date: Optional[date] = None

        # Log cleanup: fire once per calendar week (Sunday 03:00)
        self._last_log_cleanup_week: Optional[int] = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """
        Initialise all subsystems and enter the monitoring loop.

        Blocks until a shutdown signal is received.
        """
        logger.info(
            "=== Watchtower {} starting — instance={!r} ===",
            APP_VERSION,
            self._config.watchtower_name,
        )
        logger.info("Effective configuration: {}", self._config.safe_repr())

        self._register_signal_handlers()
        self._browser.launch()

        if not self._perform_full_session_setup():
            logger.critical(
                "Initial session setup failed — cannot start monitoring."
            )
            self._shutdown()
            return

        logger.info(
            "Initialisation complete — entering monitoring loop "
            "(check_interval={}s, auth_refresh={}min, heartbeat={:02d}:{:02d} {}).",
            self._config.check_interval,
            AUTH_CHECK_INTERVAL_S // 60,
            HEARTBEAT_HOUR,
            HEARTBEAT_MINUTE,
            self._config.timezone,
        )

        self._running = True
        self._main_loop()

    # -------------------------------------------------------------------------
    # Main loop  (single-threaded — no APScheduler, no thread pool)
    # -------------------------------------------------------------------------

    def _main_loop(self) -> None:
        """
        Blocking monitoring loop.

        Runs entirely on the calling thread.  Each iteration:
        1. Survey check.
        2. Heartbeat (if due).
        3. Log cleanup (if due).
        4. Sleep for the remainder of ``check_interval``.

        Shutdown is triggered by ``_shutdown()`` (called from signal
        handlers), which sets ``self._running = False`` and causes the
        interruptible sleep to exit within 1 second.
        """
        while self._running:
            cycle_start = time.monotonic()

            # ── Survey check ─────────────────────────────────────────────────
            try:
                self._job_survey_check()
            except Exception as exc:
                logger.exception(
                    "Unhandled exception in survey check job: {}", exc
                )

            # ── Daily heartbeat ───────────────────────────────────────────────
            try:
                self._maybe_heartbeat()
            except Exception as exc:
                logger.exception("Unhandled exception in heartbeat job: {}", exc)

            # ── Weekly log cleanup ────────────────────────────────────────────
            try:
                self._maybe_log_cleanup()
            except Exception as exc:
                logger.exception("Unhandled exception in log cleanup job: {}", exc)

            # ── Interruptible sleep ───────────────────────────────────────────
            elapsed = time.monotonic() - cycle_start
            sleep_remaining = max(0.0, self._config.check_interval - elapsed)
            logger.debug(
                "Cycle took {:.1f}s — sleeping {:.1f}s.",
                elapsed,
                sleep_remaining,
            )
            self._interruptible_sleep(sleep_remaining)

        self._shutdown()

    def _interruptible_sleep(self, seconds: float) -> None:
        """
        Sleep for *seconds* but wake up within 1 second of a shutdown signal.

        ``time.sleep()`` is not interrupted by Python signal handlers on all
        platforms, so we poll ``self._running`` every second instead.
        """
        end = time.monotonic() + seconds
        while self._running:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))

    # -------------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------------

    def _perform_full_session_setup(self) -> bool:
        """
        Authenticate and navigate to the survey page.

        Used on startup and after a browser restart.
        """
        if not ensure_authenticated(
            self._browser.page, self._config, self._state
        ):
            return False
        if not navigate_to_surveys(self._browser.page):
            return False
        self._last_auth_check = time.monotonic()
        return True

    def _recover_session(self) -> bool:
        """
        Recover from a drifted or expired session without restarting the browser.

        Called when the URL check shows we are no longer on the survey page.
        """
        page = self._browser.page

        if is_login_required(page):
            logger.info(
                "Session expired (login page detected) — re-authenticating."
            )
            if not ensure_authenticated(page, self._config, self._state):
                logger.error("Re-authentication failed.")
                return False
            self._last_auth_check = time.monotonic()

        if not navigate_to_surveys(page):
            logger.error("Navigation recovery failed.")
            return False

        logger.info("Session recovered — back on survey page.")
        return True

    # -------------------------------------------------------------------------
    # Graceful shutdown
    # -------------------------------------------------------------------------

    def _shutdown(self) -> None:
        """Gracefully terminate all subsystems.  Safe to call multiple times."""
        if not self._running and not self._browser.is_alive:
            return  # already shut down
        logger.info("=== Watchtower shutting down ===")
        self._running = False
        self._browser.close()
        self._state.update_heartbeat()
        logger.info("=== Watchtower shutdown complete ===")

    def _register_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle_signal)
        logger.debug("Signal handlers registered for SIGINT and SIGTERM.")

    def _handle_signal(self, signum: int, frame) -> None:  # noqa: ANN001
        logger.info(
            "Signal {} received — initiating graceful shutdown.",
            signal.Signals(signum).name,
        )
        self._running = False  # _main_loop exits at next sleep poll

    # -------------------------------------------------------------------------
    # Survey check job
    # -------------------------------------------------------------------------

    def _job_survey_check(self) -> None:
        """
        One complete survey monitoring cycle.

        Fast path (99% of cycles)
        --------------------------
        ``/survey/`` is in the current URL → reload, dismiss dialog,
        click "Check Now" directly.

        Recovery paths
        --------------
        * URL drifted (not login) → navigate back.
        * Login page detected (session expired) → re-auth, then navigate.
        * Browser crashed → restart, full session setup.

        Periodic full auth check
        ------------------------
        Every ``AUTH_CHECK_INTERVAL_S`` seconds, a full auth verification
        runs regardless of URL.
        """
        logger.info("--- Survey check starting ---")

        # ── 1. Browser health ─────────────────────────────────────────────────
        if not self._browser.is_alive:
            logger.warning("Browser unresponsive — restarting.")
            self._browser.restart()
            if not self._perform_full_session_setup():
                logger.error(
                    "Session setup after restart failed — skipping cycle."
                )
                return

        page = self._browser.page
        now = time.monotonic()
        current_url = page.url.lower()

        # ── 2. Periodic full auth verification ────────────────────────────────
        if (now - self._last_auth_check) >= AUTH_CHECK_INTERVAL_S:
            logger.info(
                "Periodic auth check — {}min interval elapsed.",
                AUTH_CHECK_INTERVAL_S // 60,
            )
            if not ensure_authenticated(page, self._config, self._state):
                logger.error("Periodic auth check failed — skipping cycle.")
                return
            if not navigate_to_surveys(page):
                logger.error(
                    "Navigation after auth check failed — skipping cycle."
                )
                return
            self._last_auth_check = time.monotonic()

        # ── 3. URL check — fast path vs. recovery ─────────────────────────────
        elif "/survey/" not in current_url:
            logger.warning(
                "Not on survey page (url={}) — recovering.", page.url
            )
            if not self._recover_session():
                logger.error("Session recovery failed — skipping cycle.")
                return

        # ── 4. Survey check (on fresh page, dialog dismissed, Check Now clicked)
        request_id = check_for_new_survey(page)
        self._state.update_last_check()

        if request_id is None:
            logger.info("--- Survey check complete: no new surveys ---")
            return

        if self._state.has_seen(request_id):
            logger.info(
                "Request ID {} already seen — suppressing duplicate.",
                request_id,
            )
            return

        # ── 5. New survey ─────────────────────────────────────────────────────
        detected_at = datetime.now(tz=timezone.utc).isoformat()
        logger.info(
            "NEW survey detected — Request ID: {}, detected at: {}",
            request_id,
            detected_at,
        )
        self._state.record_request_id(request_id)
        sent = send_survey_notification(self._config, request_id, detected_at)
        if sent:
            logger.info("--- Survey check complete: notification sent ---")
        else:
            logger.warning(
                "--- Survey check complete: survey recorded, email failed ---"
            )

    # -------------------------------------------------------------------------
    # Heartbeat job
    # -------------------------------------------------------------------------

    def _maybe_heartbeat(self) -> None:
        """Fire the heartbeat email once per day at HEARTBEAT_HOUR:HEARTBEAT_MINUTE."""
        now = datetime.now(tz=self._tz)
        today = now.date()

        if not (
            now.hour == HEARTBEAT_HOUR
            and now.minute == HEARTBEAT_MINUTE
            and self._last_heartbeat_date != today
        ):
            return

        uptime_s = time.monotonic() - self._start_time
        logger.info(
            "Sending daily heartbeat — uptime {:.1f}h.", uptime_s / 3600
        )
        success = send_heartbeat(
            config=self._config,
            last_check=self._state.last_check,
            uptime_seconds=uptime_s,
        )
        if success:
            self._state.update_heartbeat()
            self._last_heartbeat_date = today
            logger.info("Heartbeat sent and recorded.")
        else:
            logger.warning("Heartbeat email failed — will retry next minute.")

    # -------------------------------------------------------------------------
    # Log cleanup job
    # -------------------------------------------------------------------------

    def _maybe_log_cleanup(self) -> None:
        """Remove log archives older than 30 days, once per week (Sunday 03:00)."""
        now = datetime.now(tz=self._tz)
        iso = now.isocalendar()
        current_week = iso.week

        if not (
            now.weekday() == 6  # Sunday
            and now.hour == 3
            and now.minute == 0
            and self._last_log_cleanup_week != current_week
        ):
            return

        logger.info("Running weekly log cleanup.")
        from datetime import timedelta

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
        removed = 0

        if LOGS_DIR.exists():
            for gz_file in LOGS_DIR.glob("*.gz"):
                mtime = datetime.fromtimestamp(
                    gz_file.stat().st_mtime, tz=timezone.utc
                )
                if mtime < cutoff:
                    gz_file.unlink()
                    removed += 1
                    logger.debug("Removed: {}", gz_file.name)

        self._last_log_cleanup_week = current_week
        logger.info(
            "Log cleanup complete — {} archive(s) removed.", removed
        )
