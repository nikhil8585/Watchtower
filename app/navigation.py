"""
app/navigation.py
=================
Page navigation helpers for Watchtower.

Single responsibility: get the browser to the correct page.

Strategy
--------
1. Try clicking the sidebar / nav link (fast; works when already logged in
   and the nav is rendered).
2. Fall back to a direct URL ``goto`` (works after a fresh login or if the
   sidebar is not yet rendered).

All navigation is performed with ``wait_until="domcontentloaded"`` rather
than ``"networkidle"`` to avoid hanging on pages that stream data or have
long-polling connections.
"""

from __future__ import annotations

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app.constants import (
    POST_NAVIGATE_WAIT_S,
    SHORT_ELEMENT_TIMEOUT,
    TRYRATING_SURVEYS_URL,
)
from app.logger import logger
from app.selectors import TryRatingSelectors


def navigate_to_surveys(page: Page) -> bool:
    """
    Navigate the browser to the TryRating surveys page.

    Tries two strategies in order:

    1. **Sidebar link** — clicks the surveys nav link if visible.
       Fast and mimics real user behaviour (lower bot-detection risk).
    2. **Direct URL** — falls back to ``page.goto(TRYRATING_SURVEYS_URL)``
       if the sidebar link is not available.

    Parameters
    ----------
    page:
        Active, authenticated Playwright page.

    Returns
    -------
    bool
        ``True`` if the surveys page was successfully reached.
        ``False`` if both strategies failed.
    """
    # ── Strategy 1: sidebar / nav link ────────────────────────────────────────
    try:
        nav_link = page.locator(TryRatingSelectors.SURVEYS_NAV_LINK).first
        nav_link.wait_for(state="visible", timeout=SHORT_ELEMENT_TIMEOUT)
        nav_link.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(int(POST_NAVIGATE_WAIT_S * 1000))
        logger.info("Navigated to surveys via sidebar nav link.")
        return True

    except PlaywrightTimeoutError:
        logger.debug(
            "Sidebar nav link not found (timeout {}ms) — falling back to direct URL.",
            SHORT_ELEMENT_TIMEOUT,
        )
    except Exception as exc:
        logger.debug(
            "Sidebar nav click raised an unexpected error: {} — falling back.", exc
        )

    # ── Strategy 2: direct URL ────────────────────────────────────────────────
    try:
        page.goto(TRYRATING_SURVEYS_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(int(POST_NAVIGATE_WAIT_S * 1000))
        logger.info("Navigated to surveys via direct URL: {}", TRYRATING_SURVEYS_URL)
        return True

    except Exception as exc:
        logger.error(
            "Navigation to surveys page failed completely: {}", exc
        )
        return False


def is_on_surveys_page(page: Page) -> bool:
    """
    Return ``True`` if the current page appears to be the surveys page.

    Used as a lightweight pre-check before running the survey detector,
    to avoid scraping the wrong page after an unexpected redirect.
    """
    return "survey" in page.url.lower()
