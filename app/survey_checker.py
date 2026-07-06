"""
app/survey_checker.py
=====================
Survey detection and Request ID extraction for Watchtower.

Single responsibility: given an authenticated page on the surveys URL,
determine whether a new survey is available and return its Request ID.

Extraction strategy
-------------------
1. Click "Get Surveys" / "Check Surveys" if the button exists.
2. Apply random jitter (reduces bot-detection risk).
3. Check for a dedicated request-id element via ``REQUEST_ID_ELEMENT``.
4. Fall back to scanning all survey cards for a long numeric sequence.

Only Request ID is extracted.  All other survey metadata (task type,
estimated time, business data, descriptions, buttons) is intentionally
ignored per the specification.
"""

from __future__ import annotations

import random
import re
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app.constants import (
    JITTER_MAX_MS,
    JITTER_MIN_MS,
    POST_SURVEY_CLICK_WAIT_S,
    SHORT_ELEMENT_TIMEOUT,
)
from app.logger import logger
from app.selectors import TryRatingSelectors

# Matches a standalone sequence of 6 or more digits.
# TryRating Request IDs are typically 9 digits (e.g. 695584131).
# The lower bound of 6 guards against matching unrelated numbers
# like CSS pixel values or short codes.
_REQUEST_ID_PATTERN: re.Pattern[str] = re.compile(r"\b(\d{6,})\b")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_id_from_text(text: str) -> Optional[str]:
    """
    Return the first long numeric sequence found in *text*, or ``None``.

    Parameters
    ----------
    text:
        Raw inner-text from a DOM element.
    """
    match = _REQUEST_ID_PATTERN.search(text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def click_check_surveys(page: Page) -> bool:
    """
    Click the "Get Surveys" / "Check Surveys" button if it is present.

    Parameters
    ----------
    page:
        Active Playwright page on the surveys URL.

    Returns
    -------
    bool
        ``True`` if the button was found and clicked, ``False`` otherwise.
    """
    try:
        btn = page.locator(TryRatingSelectors.GET_SURVEYS_BUTTON).first
        btn.wait_for(state="visible", timeout=SHORT_ELEMENT_TIMEOUT)
        btn.click()
        page.wait_for_timeout(int(POST_SURVEY_CLICK_WAIT_S * 1000))
        logger.info("'Get Surveys' button clicked — waiting for results.")
        return True
    except PlaywrightTimeoutError:
        logger.debug("'Get Surveys' button not present — skipping click.")
        return False
    except Exception as exc:
        logger.warning("Unexpected error clicking 'Get Surveys' button: {}", exc)
        return False


def extract_request_id(page: Page) -> Optional[str]:
    """
    Locate and return a Request ID from the current surveys page.

    Two strategies, in order:

    1. **Dedicated element** — look for an element matching
       ``REQUEST_ID_ELEMENT`` and parse its inner text.
    2. **Card scan** — iterate all ``SURVEY_CARD`` elements and apply
       regex extraction to their combined inner text.

    Parameters
    ----------
    page:
        Active Playwright page, already on the surveys URL.

    Returns
    -------
    str | None
        The first detected Request ID, or ``None`` if no surveys visible.
    """
    # ── Strategy 1: dedicated request-id element ──────────────────────────────
    try:
        el = page.locator(TryRatingSelectors.REQUEST_ID_ELEMENT).first
        el.wait_for(state="visible", timeout=SHORT_ELEMENT_TIMEOUT)
        raw_text = el.inner_text().strip()

        # If the element text is purely numeric, use it directly
        if raw_text.isdigit() and len(raw_text) >= 6:
            logger.debug("Request ID from dedicated element: {}", raw_text)
            return raw_text

        # Otherwise attempt pattern extraction (may include a label)
        extracted = _extract_id_from_text(raw_text)
        if extracted:
            logger.debug("Request ID extracted from element text: {}", extracted)
            return extracted

    except PlaywrightTimeoutError:
        logger.debug("Dedicated request-id element not found — trying card scan.")
    except Exception as exc:
        logger.warning("Error reading request-id element: {}", exc)

    # ── Strategy 2: scan all survey cards ─────────────────────────────────────
    try:
        cards = page.locator(TryRatingSelectors.SURVEY_CARD).all()
        if not cards:
            logger.debug("No survey cards found on page.")
            return None

        logger.debug("Scanning {} survey card(s) for Request ID.", len(cards))
        for card in cards:
            text = card.inner_text()
            extracted = _extract_id_from_text(text)
            if extracted:
                logger.debug("Request ID from card scan: {}", extracted)
                return extracted

    except Exception as exc:
        logger.warning("Error during survey card scan: {}", exc)

    return None


def check_for_new_survey(page: Page) -> Optional[str]:
    """
    Execute a complete survey-check cycle and return a Request ID if found.

    This is the primary entry point called by the scheduler job in
    :class:`~app.watchtower.Watchtower`.

    Steps
    -----
    1. Apply random pre-check jitter (anti-bot / rate-limit mitigation).
    2. Click "Get Surveys" if the button is visible.
    3. Attempt to extract a Request ID.

    Parameters
    ----------
    page:
        Active Playwright page on the TryRating surveys URL.

    Returns
    -------
    str | None
        Detected Request ID string, or ``None`` when no surveys exist.
    """
    jitter_ms = random.randint(JITTER_MIN_MS, JITTER_MAX_MS)
    logger.debug("Applying pre-check jitter: {}ms.", jitter_ms)
    page.wait_for_timeout(jitter_ms)

    click_check_surveys(page)

    request_id = extract_request_id(page)

    if request_id:
        logger.info("Survey detected — Request ID: {}", request_id)
    else:
        logger.debug("No surveys currently available.")

    return request_id
