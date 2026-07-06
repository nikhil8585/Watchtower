"""
app/watchtower.py
=================
Central orchestrator for the Watchtower monitoring platform.

Responsibilities
----------------
* Own the top-level application lifecycle (start → run → shutdown).
* Wire together all subsystems: browser, state, scheduler, jobs.
* Register OS signal handlers for graceful shutdown (SIGINT, SIGTERM).
* Ensure no scheduled job terminates the process — all exceptions are
  caught and logged; the scheduler continues.
* Provide a clean extension point for future monitored sites (UHRS,
  Prolific, Appen) without architectural changes.

Scheduler design
----------------
APScheduler's ``BlockingScheduler`` owns the main thread after
:meth:`Watchtower.start` is called.  Jobs run in the scheduler's thread
pool.  ``max_instances=1`` and ``coalesce=True`` guarantee that no
survey-check job overlaps with itself under any scheduling drift.
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
    HEARTBEAT_HOUR,
    HEARTBEAT_MINUTE,
    JOB_HEARTBEAT,
    JOB_LOG_CLEANUP,
    JOB_SURVEY_CHECK,
    LOGS_DIR,
)
from app.logger import logger
from app.login import ensure_authenticated
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

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """
        Initialise all subsystems and start the monitoring loop.

        Execution flow:
        1. Register OS signal handlers.
        2. Launch Chromium.
        3. Authenticate with TryRating.
        4. Navigate to the surveys page.
        5. Start APScheduler (this call blocks until shutdown).
        """
        logger.info(
            "=== Watchtower {} starting — instance={!r} ===",
            APP_VERSION,
            self._config.watchtower_name,
        )
        logger.info("Effective configuration: {}", self._config.safe_repr())

        self._register_signal_handlers()
        self._browser.launch()

        # Initial authentication
        if not ensure_authenticated(
            self._browser.page, self._config, self._state
        ):
            logger.critical(
                "Initial authentication failed — cannot start monitoring. "
                "Check credentials and network connectivity."
            )
            self._shutdown()
            return

        # Initial navigation
        if not navigate_to_surveys(self._browser.page):
            logger.critical(
                "Initial navigation to surveys page failed. "
                "Check TRYRATING_SURVEYS_URL and selectors."
            )
            self._shutdown()
            return

        logger.info("Initialisation complete — starting scheduler.")
        self._running = True
        self._start_scheduler()   # blocks until shutdown

    # -------------------------------------------------------------------------
    # Graceful shutdown
    # -------------------------------------------------------------------------

    def _shutdown(self) -> None:
        """
        Gracefully terminate all subsystems.

        Called from signal handlers or after a fatal startup error.
        Safe to call multiple times (idempotent).
        """
        if not self._running and self._scheduler is None:
            # Already shut down or never started
            return

        logger.info("=== Watchtower shutting down ===")
        self._running = False

        if self._scheduler and self._scheduler.running:
            try:
                self._scheduler.shutdown(wait=False)
                logger.debug("Scheduler stopped.")
            except Exception as exc:
                logger.debug("Scheduler shutdown error: {}", exc)

        self._browser.close()
        self._state.update_heartbeat()
        logger.info("=== Watchtower shutdown complete ===")

    # -------------------------------------------------------------------------
    # Signal handling
    # -------------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        """Attach SIGINT and SIGTERM to the graceful shutdown handler."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle_signal)
        logger.debug("Signal handlers registered for SIGINT and SIGTERM.")

    def _handle_signal(self, signum: int, frame) -> None:  # noqa: ANN001
        """OS signal callback — triggers graceful shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info(
            "Signal {} received — initiating graceful shutdown.", sig_name
        )
        self._shutdown()

    # -------------------------------------------------------------------------
    # Scheduler
    # -------------------------------------------------------------------------

    def _start_scheduler(self) -> None:
        """
        Configure and start the APScheduler.

        This method blocks the calling thread until the scheduler is
        stopped (via :meth:`_shutdown` or an OS signal).
        """
        self._scheduler = BlockingScheduler(
            timezone=self._config.timezone,
            job_defaults={
                "misfire_grace_time": 60,
                "coalesce": True,
                "max_instances": 1,
            },
        )

        # ── Job 1: Survey monitor ─────────────────────────────────────────────
        self._scheduler.add_job(
            func=self._job_survey_check,
            trigger=IntervalTrigger(seconds=self._config.check_interval),
            id=JOB_SURVEY_CHECK,
            name="Survey Monitor",
            replace_existing=True,
        )

        # ── Job 2: Daily heartbeat ────────────────────────────────────────────
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
            misfire_grace_time=3_600,  # 1 hour — tolerate system sleep/boot
        )

        # ── Job 3: Log cleanup (weekly, Sunday 03:00) ─────────────────────────
        self._scheduler.add_job(
            func=self._job_log_cleanup,
            trigger=CronTrigger(
                day_of_week="sun",
                hour=3,
                minute=0,
                timezone=self._config.timezone,
            ),
            id=JOB_LOG_CLEANUP,
            name="Log Cleanup",
            replace_existing=True,
        )

        logger.info(
            "Scheduler active — survey check every {}s, heartbeat daily at {:02d}:{:02d} {}.",
            self._config.check_interval,
            HEARTBEAT_HOUR,
            HEARTBEAT_MINUTE,
            self._config.timezone,
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
        APScheduler job: execute one survey monitoring cycle.

        All exceptions are caught so the scheduler never terminates due
        to an application-level error.  The browser is restarted if the
        error appears to be browser-related.
        """
        try:
            logger.info("--- Survey check starting ---")

            # ── Browser health check ──────────────────────────────────────────
            if not self._browser.is_alive:
                logger.warning("Browser is unresponsive — restarting.")
                self._browser.restart()

            page = self._browser.page

            # ── Session guard ─────────────────────────────────────────────────
            if not ensure_authenticated(page, self._config, self._state):
                logger.error(
                    "Re-authentication failed — skipping this cycle. "
                    "Will retry next interval."
                )
                return

            # ── Navigation guard ──────────────────────────────────────────────
            if not navigate_to_surveys(page):
                logger.error(
                    "Navigation to surveys failed — skipping this cycle."
                )
                return

            # ── Check for surveys ─────────────────────────────────────────────
            request_id = check_for_new_survey(page)
            self._state.update_last_check()

            if request_id is None:
                logger.info("--- Survey check complete: no new surveys ---")
                return

            # ── Duplicate check ───────────────────────────────────────────────
            if self._state.has_seen(request_id):
                logger.info(
                    "Request ID {} already seen — suppressing duplicate notification.",
                    request_id,
                )
                logger.info("--- Survey check complete: known survey ---")
                return

            # ── New survey ────────────────────────────────────────────────────
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
                    "--- Survey check complete: survey recorded but email failed ---"
                )

        except Exception as exc:
            logger.exception(
                "Unhandled exception in survey check job: {}. "
                "Attempting browser restart.",
                exc,
            )
            try:
                self._browser.restart()
            except Exception as restart_exc:
                logger.error(
                    "Browser restart failed: {}. Will retry next cycle.", restart_exc
                )

    def _job_heartbeat(self) -> None:
        """APScheduler job: send the daily heartbeat email."""
        try:
            uptime_s = time.monotonic() - self._start_time
            logger.info("Sending daily heartbeat — uptime {:.1f}h.", uptime_s / 3600)
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
        """
        APScheduler job: remove compressed log archives older than 30 days.

        Loguru's ``retention`` parameter handles rotation automatically.
        This job provides an additional safety sweep for any orphaned
        ``.gz`` files that bypass Loguru's cleanup (e.g. after a crash).
        """
        try:
            from datetime import timedelta

            logger.info("Running weekly log cleanup.")
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
            removed = 0

            if not LOGS_DIR.exists():
                logger.debug("Logs directory does not exist — skipping cleanup.")
                return

            for gz_file in LOGS_DIR.glob("*.gz"):
                mtime = datetime.fromtimestamp(
                    gz_file.stat().st_mtime, tz=timezone.utc
                )
                if mtime < cutoff:
                    gz_file.unlink()
                    removed += 1
                    logger.debug("Removed expired log archive: {}", gz_file.name)

            logger.info("Log cleanup complete — {} archive(s) removed.", removed)
        except Exception as exc:
            logger.exception("Unhandled exception in log cleanup job: {}", exc)
