"""
app/watchtower.py
=================
Central orchestrator for the Watchtower monitoring platform.

Survey check optimisation
--------------------------
The browser stays on the survey page permanently.  Every scheduled
check cycle follows the fastest path first:

  1. ``/survey/`` in current URL  →  click "Check Now" directly.
     (Handles query-string variations, e.g. ``?refresh=true``.)

  2. URL drifted elsewhere  →  detect why (login page? error page?)
     and recover silently without a full auth roundtrip.

  3. Periodic full auth (every ``AUTH_CHECK_INTERVAL_S``)  →  catches
     slow session expiry even when still appearing to be on the page.

This eliminates the unnecessary navigate → check → navigate → check
loop that the original design suffered from.  The browser touches the
nav sidebar only when something has actually gone wrong.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.browser import BrowserManager
from app.config import Config
from app.constants import (
    APP_VERSION,
    AUTH_CHECK_INTERVAL_S,
    HEARTBEAT_HOUR,
    HEARTBEAT_MINUTE,
    JOB_HEARTBEAT,
    JOB_LOG_CLEANUP,
    JOB_SURVEY_CHECK,
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

    Parameters
    ----------
    config:
        Fully validated, immutable application configuration.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._state = StateManager()
        self._browser = BrowserManager(config)
        self._scheduler: Optional[BlockingScheduler] = None
        self._start_time: float = time.monotonic()
        self._running: bool = False
        # Tracks the last time a full auth verification was performed.
        # Initialised to 0 so the first cycle always does a full check.
        self._last_auth_check: float = 0.0

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """
        Initialise all subsystems and start the monitoring loop.

        1. Register OS signal handlers.
        2. Launch Chromium.
        3. Authenticate and navigate to the survey page.
        4. Start APScheduler (blocks until shutdown).
        """
        logger.info(
            "=== Watchtower {} starting — instance={!r} ===",
            APP_VERSION,
            self._config.watchtower_name,
        )
        logger.info("Effective configuration: {}", self._config.safe_repr())

        self._register_signal_handlers()
        self._browser.launch()

        # Full session setup on start
        if not self._perform_full_session_setup():
            logger.critical(
                "Initial session setup failed — cannot start monitoring."
            )
            self._shutdown()
            return

        logger.info("Initialisation complete — starting scheduler.")
        self._running = True
        self._start_scheduler()

    # -------------------------------------------------------------------------
    # Session management helpers
    # -------------------------------------------------------------------------

    def _perform_full_session_setup(self) -> bool:
        """
        Authenticate and navigate to the survey page.

        Used on initial startup and after a browser restart.

        Returns
        -------
        bool
            ``True`` if the session is authenticated and the browser is
            on the survey page.
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
        Recover from a drifted or expired session without a browser restart.

        Called when the URL check shows we are no longer on the survey page.

        Returns
        -------
        bool
            ``True`` if the survey page was re-reached.
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
        logger.info("=== Watchtower shutting down ===")
        self._running = False

        if self._scheduler and self._scheduler.running:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as exc:
                logger.debug("Scheduler shutdown error: {}", exc)

        self._browser.close()
        self._state.update_heartbeat()
        logger.info("=== Watchtower shutdown complete ===")

    # -------------------------------------------------------------------------
    # Signal handling
    # -------------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle_signal)
        logger.debug("Signal handlers registered for SIGINT and SIGTERM.")

    def _handle_signal(self, signum: int, frame) -> None:  # noqa: ANN001
        logger.info(
            "Signal {} received — initiating graceful shutdown.",
            signal.Signals(signum).name,
        )
        self._shutdown()

    # -------------------------------------------------------------------------
    # Scheduler
    # -------------------------------------------------------------------------

    def _start_scheduler(self) -> None:
        """Configure APScheduler and block until shutdown."""
        self._scheduler = BlockingScheduler(
            timezone=self._config.timezone,
            job_defaults={
                "misfire_grace_time": 60,
                "coalesce": True,
                "max_instances": 1,
            },
        )

        self._scheduler.add_job(
            func=self._job_survey_check,
            trigger=IntervalTrigger(seconds=self._config.check_interval),
            id=JOB_SURVEY_CHECK,
            name="Survey Monitor",
            replace_existing=True,
        )
        self._scheduler.add_job(
            func=self._job_heartbeat,
            trigger=CronTrigger(
                hour=HEARTBEAT_HOUR,
                minute=HEARTBEAT_MINUTE,
                timezone=self._config.timezone,
            ),
            id=JOB_HEARTBEAT,
            name="Daily Heartbeat",
            replace_existing=True,
            misfire_grace_time=3_600,
        )
        self._scheduler.add_job(
            func=self._job_log_cleanup,
            trigger=CronTrigger(
                day_of_week="sun", hour=3, minute=0,
                timezone=self._config.timezone,
            ),
            id=JOB_LOG_CLEANUP,
            name="Log Cleanup",
            replace_existing=True,
        )

        logger.info(
            "Scheduler active — survey check every {}s, "
            "heartbeat daily at {:02d}:{:02d} {}, "
            "full auth refresh every {}min.",
            self._config.check_interval,
            HEARTBEAT_HOUR,
            HEARTBEAT_MINUTE,
            self._config.timezone,
            AUTH_CHECK_INTERVAL_S // 60,
        )

        try:
            self._scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler received interrupt.")
        finally:
            self._shutdown()

    # -------------------------------------------------------------------------
    # Scheduled jobs
    # -------------------------------------------------------------------------

    def _job_survey_check(self) -> None:
        """
        APScheduler job: optimised survey monitoring cycle.

        Fast path (99% of cycles)
        --------------------------
        ``/survey/`` is in the current URL  →  click "Check Now" directly.
        No navigation, no auth check, no sidebar interaction.

        Recovery paths
        --------------
        * URL drifted elsewhere (but not login) → navigate back to surveys.
        * Login page detected (session expired) → re-auth, then navigate.
        * Browser crashed (``is_alive`` is False) → restart browser, full setup.

        Periodic full auth check
        ------------------------
        Every ``AUTH_CHECK_INTERVAL_S`` seconds (~30 min), a full
        ``ensure_authenticated`` call runs regardless of URL, catching
        slow / silent session expiry before it becomes a problem.
        """
        try:
            logger.info("--- Survey check starting ---")

            # ── 1. Browser health ─────────────────────────────────────────────
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

            # ── 2. Periodic full auth verification ────────────────────────────
            if (now - self._last_auth_check) >= AUTH_CHECK_INTERVAL_S:
                logger.info(
                    "Periodic auth check — {}min interval elapsed.",
                    AUTH_CHECK_INTERVAL_S // 60,
                )
                if not ensure_authenticated(page, self._config, self._state):
                    logger.error(
                        "Periodic auth check failed — skipping cycle."
                    )
                    return
                if not navigate_to_surveys(page):
                    logger.error(
                        "Navigation after auth check failed — skipping cycle."
                    )
                    return
                self._last_auth_check = time.monotonic()

            # ── 3. URL check — fast path vs. recovery ─────────────────────────
            elif "/survey/" not in current_url:
                logger.warning(
                    "Not on survey page (url={}) — recovering.", page.url
                )
                if not self._recover_session():
                    logger.error(
                        "Session recovery failed — skipping cycle."
                    )
                    return

            # ── 4. We are on the survey page — just check ─────────────────────
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

            # ── 5. New survey found ───────────────────────────────────────────
            detected_at = datetime.now(tz=timezone.utc).isoformat()
            logger.info(
                "NEW survey detected — Request ID: {}, detected at: {}",
                request_id,
                detected_at,
            )
            self._state.record_request_id(request_id)
            sent = send_survey_notification(
                self._config, request_id, detected_at
            )
            if sent:
                logger.info("--- Survey check complete: notification sent ---")
            else:
                logger.warning(
                    "--- Survey check complete: survey recorded, email failed ---"
                )

        except Exception as exc:
            logger.exception(
                "Unhandled exception in survey check — attempting recovery: {}",
                exc,
            )
            try:
                self._browser.restart()
                self._perform_full_session_setup()
            except Exception as restart_exc:
                logger.error(
                    "Recovery after exception failed: {}. "
                    "Will retry next cycle.",
                    restart_exc,
                )

    def _job_heartbeat(self) -> None:
        """APScheduler job: send the daily heartbeat email."""
        try:
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
                logger.info("Heartbeat sent and recorded.")
            else:
                logger.warning("Heartbeat email failed — will retry tomorrow.")
        except Exception as exc:
            logger.exception("Unhandled exception in heartbeat job: {}", exc)

    def _job_log_cleanup(self) -> None:
        """APScheduler job: remove compressed log archives older than 30 days."""
        try:
            from datetime import timedelta

            logger.info("Running weekly log cleanup.")
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
            removed = 0

            if not LOGS_DIR.exists():
                return

            for gz_file in LOGS_DIR.glob("*.gz"):
                mtime = datetime.fromtimestamp(
                    gz_file.stat().st_mtime, tz=timezone.utc
                )
                if mtime < cutoff:
                    gz_file.unlink()
                    removed += 1
                    logger.debug("Removed: {}", gz_file.name)

            logger.info("Log cleanup complete — {} archive(s) removed.", removed)
        except Exception as exc:
            logger.exception("Unhandled exception in log cleanup job: {}", exc)
