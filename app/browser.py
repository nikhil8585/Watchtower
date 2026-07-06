"""
app/browser.py
==============
Playwright Chromium browser lifecycle management.

Responsibilities
----------------
* Launch the browser with production-hardened flags.
* Provide a single :class:`~playwright.sync_api.Page` to the application.
* Detect browser crashes via :attr:`is_alive`.
* Restart cleanly after a crash without leaking OS resources.
* Close all Playwright objects on graceful shutdown.

Usage — context manager (preferred)::

    with BrowserManager(config) as bm:
        bm.page.goto("https://example.com")

Usage — manual::

    bm = BrowserManager(config)
    bm.launch()
    bm.page.goto(...)
    bm.close()
"""

from __future__ import annotations

from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from app.config import Config
from app.constants import BROWSER_ARGS, MAX_BROWSER_RESTART_RETRIES
from app.logger import logger


class BrowserManager:
    """
    Lifecycle manager for a Playwright Chromium browser instance.

    Parameters
    ----------
    config:
        Application configuration.  Provides ``headless``, ``slow_mo``,
        ``default_timeout``, and ``timezone`` values.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # -------------------------------------------------------------------------
    # Context-manager protocol
    # -------------------------------------------------------------------------

    def __enter__(self) -> "BrowserManager":
        self.launch()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def page(self) -> Page:
        """
        The active Playwright :class:`~playwright.sync_api.Page`.

        Raises
        ------
        RuntimeError
            If the browser has not been launched or the page is closed.
        """
        if self._page is None or self._page.is_closed():
            raise RuntimeError(
                "BrowserManager: page is unavailable. "
                "Call launch() before accessing .page."
            )
        return self._page

    @property
    def is_alive(self) -> bool:
        """
        Return ``True`` when the browser process and page are both healthy.

        This is a lightweight check — it does NOT make a network request.
        """
        try:
            return (
                self._browser is not None
                and self._browser.is_connected()
                and self._page is not None
                and not self._page.is_closed()
            )
        except Exception:
            return False

    def launch(self) -> None:
        """
        Start Playwright, launch Chromium, create a context and a page.

        Retries up to ``MAX_BROWSER_RESTART_RETRIES`` times on failure.

        Raises
        ------
        RuntimeError
            If the browser cannot be started after all retries.
        """
        for attempt in range(1, MAX_BROWSER_RESTART_RETRIES + 1):
            try:
                logger.info(
                    "Launching browser — attempt {}/{}.",
                    attempt,
                    MAX_BROWSER_RESTART_RETRIES,
                )
                self._playwright = sync_playwright().start()

                self._browser = self._playwright.chromium.launch(
                    headless=self._config.headless,
                    slow_mo=self._config.slow_mo,
                    args=BROWSER_ARGS,
                )

                self._context = self._browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                    timezone_id=self._config.timezone,
                    java_script_enabled=True,
                )
                self._context.set_default_timeout(self._config.default_timeout)
                self._context.set_default_navigation_timeout(
                    self._config.default_timeout
                )

                self._page = self._context.new_page()

                logger.info("Browser launched successfully (headless={}).", self._config.headless)
                return

            except Exception as exc:
                logger.warning(
                    "Browser launch attempt {}/{} failed: {}",
                    attempt,
                    MAX_BROWSER_RESTART_RETRIES,
                    exc,
                )
                self._teardown_silently()

                if attempt == MAX_BROWSER_RESTART_RETRIES:
                    raise RuntimeError(
                        f"Browser failed to launch after "
                        f"{MAX_BROWSER_RESTART_RETRIES} attempt(s)."
                    ) from exc

    def restart(self) -> None:
        """
        Teardown the current browser and start a fresh instance.

        Called by the orchestrator after detecting a crash or an
        unrecoverable page error.
        """
        logger.warning("Restarting browser...")
        self._teardown_silently()
        self.launch()
        logger.info("Browser restarted successfully.")

    def close(self) -> None:
        """
        Gracefully close the browser and release all Playwright resources.

        Safe to call even if the browser was never launched or already
        closed.
        """
        logger.info("Closing browser.")
        self._teardown_silently()

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _teardown_silently(self) -> None:
        """
        Close all Playwright objects in dependency order, swallowing errors.

        Order: page → context → browser → playwright
        """
        objects = [
            (self._page, "page"),
            (self._context, "context"),
            (self._browser, "browser"),
            (self._playwright, "playwright"),
        ]
        for obj, name in objects:
            if obj is not None:
                try:
                    obj.close()
                except Exception as exc:
                    logger.debug("Error closing {}: {}", name, exc)

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
