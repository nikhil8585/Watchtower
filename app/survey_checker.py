"""
app/survey_checker.py
=====================
Survey detection and Request ID extraction for Watchtower.

Extraction approach
-------------------
All extraction anchors to the literal label text **"Request ID"** visible
on the TryRating survey page.  No CSS class names are used.  Three XPath
strategies are attempted in order:

1. **Sibling XPath** — element immediately after the "Request ID" label
   that contains only digits (>= 6).  Works when label and value share a
   parent container.

2. **Following XPath** — same digit constraint, but searches the entire
   document forward from the label.  Handles non-adjacent layouts.

3. **Full-page regex** — scans the complete rendered page text for any
   standalone sequence of 6+ digits.  Last resort; validates with regex.

Confirmed UI structure (2025-07, from live TryRating screenshot)::

    Request ID          <- label
    695584131           <- value (always >= 9 digits)

Only Request ID is extracted.  Task type, estimated time, business data,
descriptions and button labels are all intentionally ignored.
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

# ---------------------------------------------------------------------------
# Compiled regex
# ---------------------------------------------------------------------------

# Matches a standalone numeric sequence of 6 or more digits.
# TryRating IDs are typically 9 digits (e.g. 695584131).
# Lower bound of 6 avoids false-positives on short numbers in body text.
_REQUEST_ID_RE: re.Pattern[str] = re.compile(r"\b(\d{6,})\b")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _regex_extract(text: str) -> Optional[str]:
    """Return the first 6+-digit sequence in *text*, or ``None``."""
    match = _REQUEST_ID_RE.search(text)
    return match.group(1) if match else None


def _try_locator(page: Page, selector: str, timeout: int) -> Optional[str]:
    """
    Attempt to locate an element by *selector* and return its inner text.

    Returns ``None`` on timeout or any error.
    """
    try:
        el = page.locator(selector).first
        el.wait_for(state="attached", timeout=timeout)
        text = el.inner_text().strip()
        return text if text else None
    except PlaywrightTimeoutError:
        return None
    except Exception as exc:
        logger.debug("Locator '{}' raised: {}", selector[:60], exc)
        return None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def click_check_surveys(page: Page) -> bool:
    """
    Click the "Check Now" button if it is present on the surveys page.

    Confirmed button label from live TryRating screenshot: **"Check Now"**.

    Returns
    -------
    bool
        ``True`` if the button was found and clicked.
    """
    try:
        btn = page.locator(TryRatingSelectors.GET_SURVEYS_BUTTON).first
        btn.wait_for(state="visible", timeout=SHORT_ELEMENT_TIMEOUT)
        btn.click()
        page.wait_for_timeout(int(POST_SURVEY_CLICK_WAIT_S * 1000))
        logger.info("'Check Now' button clicked — waiting for survey data.")
        return True
    except PlaywrightTimeoutError:
        logger.debug("'Check Now' button not present — skipping click.")
        return False
    except Exception as exc:
        logger.warning("Unexpected error clicking 'Check Now' button: {}", exc)
        return False


def extract_request_id(page: Page) -> Optional[str]:
    """
    Extract a Request ID from the current surveys page using XPath.

    Three strategies, in priority order:

    1. **XPath sibling** — find element with text "Request ID", then get
       the first following sibling that is all-digits with >= 6 chars.

    2. **XPath following** — same but searches the whole document forward.
       Handles layouts where label and value are not direct siblings.

    3. **Full-page text scan** — reads all rendered page text and applies
       ``_REQUEST_ID_RE`` regex.  Catches any structure not covered above.

    Parameters
    ----------
    page:
        Active Playwright page on ``tryrating.com/app/survey/rate``.

    Returns
    -------
    str | None
        Request ID string (e.g. ``"695584131"``), or ``None`` if no
        survey is currently visible.
    """
    # ── Strategy 1: XPath sibling ─────────────────────────────────────────────
    raw = _try_locator(
        page,
        TryRatingSelectors.REQUEST_ID_XPATH_SIBLING,
        SHORT_ELEMENT_TIMEOUT,
    )
    if raw:
        candidate = raw.strip()
        if _REQUEST_ID_RE.fullmatch(candidate.replace(" ", "")):
            logger.debug("Request ID via XPath sibling: {}", candidate)
            return candidate
        # Value may have extra whitespace or label prefix — extract with regex
        extracted = _regex_extract(candidate)
        if extracted:
            logger.debug("Request ID extracted from sibling text: {}", extracted)
            return extracted

    logger.debug("XPath sibling strategy found nothing — trying following::*")

    # ── Strategy 2: XPath following ───────────────────────────────────────────
    raw = _try_locator(
        page,
        TryRatingSelectors.REQUEST_ID_XPATH_FOLLOWING,
        SHORT_ELEMENT_TIMEOUT,
    )
    if raw:
        extracted = _regex_extract(raw.strip())
        if extracted:
            logger.debug("Request ID via XPath following: {}", extracted)
            return extracted

    logger.debug("XPath following strategy found nothing — scanning full page text")

    # ── Strategy 3: full-page text scan ───────────────────────────────────────
    try:
        body_text = page.locator("body").inner_text()

        # Find the "Request ID" label in the page text and grab what follows it
        label_match = re.search(
            r"Request\s+ID[\s:]*(\d{6,})", body_text, re.IGNORECASE
        )
        if label_match:
            extracted = label_match.group(1)
            logger.debug("Request ID via full-page text scan: {}", extracted)
            return extracted

        # Broader regex as absolute last resort
        extracted = _regex_extract(body_text)
        if extracted:
            logger.debug(
                "Request ID via broad regex (no label anchor): {}", extracted
            )
            return extracted

    except Exception as exc:
        logger.warning("Full-page text scan failed: {}", exc)

    logger.debug("All extraction strategies exhausted — no Request ID found.")
    return None


def check_for_new_survey(page: Page) -> Optional[str]:
    """
    Execute one complete survey-check cycle.

    Steps:
    1. Apply random jitter (anti-bot / rate-limit mitigation).
    2. Click the "Check Now" button to refresh survey availability.
    3. Extract a Request ID using the three-strategy XPath approach.

    Parameters
    ----------
    page:
        Active Playwright page on ``tryrating.com/app/survey/rate``.

    Returns
    -------
    str | None
        Request ID string if a survey is visible, ``None`` otherwise.
    """
    jitter_ms = random.randint(JITTER_MIN_MS, JITTER_MAX_MS)
    logger.debug("Pre-check jitter: {}ms.", jitter_ms)
    page.wait_for_timeout(jitter_ms)

    click_check_surveys(page)

    request_id = extract_request_id(page)

    if request_id:
        logger.info("Survey detected — Request ID: {}", request_id)
    else:
        logger.debug("No surveys currently available.")

    return request_id
