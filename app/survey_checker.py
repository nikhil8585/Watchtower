"""
app/survey_checker.py
=====================
Survey detection and Request ID extraction for Watchtower.

Stale DOM bug fix (v1.1.0)
---------------------------
TryRating is a single-page app.  When a completed or expired survey is
dismissed, the DOM is NOT fully cleared — previous survey elements remain
hidden/cached.  Before this fix, XPath matched those stale elements and
kept returning the old Request ID, causing false-positive "already seen"
suppression of genuinely new surveys.

Fix: ``_is_no_survey_state()`` is called **before** any XPath runs.  If
TryRating is explicitly showing "No more surveys", extraction returns
``None`` immediately without touching the DOM further.

The broad regex fallback (any 6+ digit number on the page) was also
removed — it matched hidden DOM data.  Strategy 3 now only matches digits
that **explicitly follow** the visible "Request ID" label text.

Extraction approach
-------------------
1. **Guard** — visible "No more surveys" text? Return None immediately.
2. **XPath sibling** — element after "Request ID" label, digits-only >= 6.
3. **XPath following** — same constraint, broader document scope.
4. **Label-anchored text scan** — regex ``Request ID <digits>`` in body text.

Confirmed UI structure (2025-07, from live TryRating screenshot)::

    Request ID          <- label
    695584131           <- value (always >= 9 digits)
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
# No-survey state guard
# ---------------------------------------------------------------------------

def _is_no_survey_state(page: Page) -> bool:
    """
    Return ``True`` if TryRating is explicitly showing a "no surveys" state.

    TryRating is a single-page app.  When a survey is completed or expires,
    the DOM is NOT fully cleared — previous survey elements may remain
    hidden/cached.  Without this guard, XPath would match those stale
    elements and return the old Request ID as if it were a live survey.

    Confirmed text from live screenshot:
        * Heading  : ``"No more surveys"``
        * Subtitle : ``"Please check back later."``

    This check runs **before** any XPath extraction.  If either string is
    visible, extraction is skipped entirely and ``None`` is returned.
    """
    try:
        if page.locator("text=No more surveys").is_visible():
            return True
        if page.locator("text=Please check back later").is_visible():
            return True
    except Exception as exc:
        logger.debug("No-survey state check raised (non-fatal): {}", exc)
    return False


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

    Guard
    -----
    First checks for the visible "No more surveys" state.  If TryRating is
    explicitly reporting no surveys, returns ``None`` immediately without
    running any XPath — preventing false positives from stale SPA DOM data.

    Strategies (in priority order)
    --------------------------------
    1. **XPath sibling** — find element with text "Request ID", get the
       first following sibling that is all-digits with >= 6 chars.
    2. **XPath following** — same constraint, broader document scope.
       Works when label and value are not direct siblings.
    3. **Label-anchored text scan** — regex anchored to "Request ID" label
       in full body text.  Only matches digits after the label — the
       previous broad regex fallback was removed because it matched any
       6+ digit number on the page, including stale/hidden DOM data.

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
    # ── Guard: explicit no-survey state ───────────────────────────────────────
    # Must run before XPath — TryRating SPA leaves stale DOM after a survey
    # is completed, so XPath would find the old Request ID in hidden elements.
    if _is_no_survey_state(page):
        logger.debug("'No more surveys' visible — skipping extraction.")
        return None

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

    logger.debug("XPath following strategy found nothing — trying label-anchored text scan")

    # ── Strategy 3: label-anchored text scan ──────────────────────────────────
    # Only matches digits that EXPLICITLY follow the "Request ID" label text.
    # Broad regex fallback was removed — it matched stale hidden DOM data and
    # caused false "already seen" suppression of genuinely new surveys.
    try:
        body_text = page.locator("body").inner_text()
        label_match = re.search(
            r"Request\s+ID[\s:]*(\d{6,})", body_text, re.IGNORECASE
        )
        if label_match:
            extracted = label_match.group(1)
            logger.debug("Request ID via label-anchored text scan: {}", extracted)
            return extracted
    except Exception as exc:
        logger.warning("Label-anchored text scan failed: {}", exc)

    logger.debug("All extraction strategies exhausted — no Request ID found.")
    return None


def check_for_new_survey(page: Page) -> Optional[str]:
    """
    Execute one complete survey-check cycle.

    Steps:
    1. Apply random jitter (anti-bot / rate-limit mitigation).
    2. **Soft reload** the survey page — forces TryRating SPA to re-initialise
       and make a fresh API call for current survey data.  Without this,
       the headless browser session accumulates stale JS component state and
       the "Check Now" click does not actually fetch updated data.
    3. Guard: return None immediately if "No more surveys" is visible.
    4. Click "Check Now" (now on a freshly-loaded page).
    5. Extract a Request ID using the three-strategy XPath approach.

    Why soft reload, not hard reload?
    ----------------------------------
    Survey availability is delivered by TryRating's API, not from browser
    cache.  A soft reload (``F5`` equivalent) re-initialises the SPA and
    triggers fresh API calls.  A hard reload (``Ctrl+Shift+R``) additionally
    forces static JS/CSS files to re-download — unnecessary overhead with
    no benefit for dynamic survey data.

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

    # ── Soft reload ───────────────────────────────────────────────────────────
    # Re-initialises TryRating SPA state so Check Now fetches live data.
    # wait_until="networkidle" ensures the SPA's initial API calls complete
    # before we interact with the page.
    try:
        logger.debug("Soft-reloading survey page to refresh SPA state.")
        page.reload(wait_until="networkidle", timeout=30_000)
        logger.debug("Page reloaded — SPA re-initialised.")
    except PlaywrightTimeoutError:
        # networkidle timed out — TryRating may be slow but page still loaded.
        # Proceed rather than skipping the entire cycle.
        logger.warning(
            "Page reload networkidle timeout — proceeding with current state."
        )
    except Exception as exc:
        logger.warning("Page reload failed ({}): {}", type(exc).__name__, exc)

    # ── Post-reload URL guard ─────────────────────────────────────────────────
    # If the reload redirected us to the login page, bail out.
    # The URL drift detection in watchtower._job_survey_check will handle
    # re-authentication on the next cycle.
    if "/survey/" not in page.url.lower():
        logger.warning(
            "Reload redirected away from survey page (url={}) — "
            "session may have expired. Skipping extraction.",
            page.url,
        )
        return None

    # ── No-survey guard ───────────────────────────────────────────────────────
    # Check visibility BEFORE clicking Check Now so we don't waste a click.
    if _is_no_survey_state(page):
        logger.debug("'No more surveys' visible after reload — no extraction needed.")
        return None

    # ── Click Check Now ───────────────────────────────────────────────────────
    click_check_surveys(page)

    # ── Post-click no-survey guard ────────────────────────────────────────────
    # Check again after the button click — the API response may have
    # updated the page to show "No more surveys".
    if _is_no_survey_state(page):
        logger.debug("'No more surveys' visible after Check Now click.")
        return None

    # ── Extract Request ID ────────────────────────────────────────────────────
    request_id = extract_request_id(page)

    if request_id:
        logger.info("Survey detected — Request ID: {}", request_id)
    else:
        logger.debug("No surveys currently available.")

    return request_id
