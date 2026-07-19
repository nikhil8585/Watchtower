"""
app/survey_checker.py
=====================
Survey detection and Request ID extraction for Watchtower.

How the check cycle works (v1.1.0)
-------------------------------------
Every 60 seconds:

1. Jitter — random 0.5–5s delay (anti-bot mitigation)
2. Soft page.reload(wait_until="networkidle")
   - Forces TryRating SPA to re-initialise and make a fresh API call
   - Fixes stale headless session state (the root cause of missed surveys)
3. Post-reload URL guard — if session expired and redirected to login,
   bail out and let watchtower.py handle re-auth next cycle
4. ALWAYS click "Check Now"
   - This is TryRating's server-fetch trigger
   - Must run regardless of what the page shows post-reload
   - The initial post-reload state is the LAST KNOWN state, not live data
5. Post-click no-survey check — if "No more surveys" is confirmed after
   the button has fetched fresh data, return None
6. XPath extraction — three strategies anchored to "Request ID" label text

Stale DOM fix
-------------
TryRating SPA does not fully clear the DOM when a survey is dismissed.
The previous Request ID remains in hidden elements.  Without the
``_is_no_survey_state()`` guard (checked only AFTER clicking Check Now),
XPath would match stale data and keep returning the old ID.

Broad regex removed
-------------------
Strategy 3 no longer contains a free-floating "any 6+ digit number"
fallback.  It is anchored to the "Request ID" label text only.
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

    Checked only AFTER clicking "Check Now" — the button must run first
    to fetch live data.  Checking before the click would give the last
    known (potentially stale) state, not the current server state.

    Confirmed text from live screenshot:
        * Heading  : "No more surveys"
        * Subtitle : "Please check back later."
    """
    try:
        if page.locator("text=No more surveys").is_visible():
            return True
        if page.locator("text=Please check back later").is_visible():
            return True
    except Exception as exc:
        logger.debug("No-survey state check raised (non-fatal): {}", exc)
    return False


def dismiss_task_type_dialog(page: Page) -> bool:
    """
    Detect and dismiss the "Now Launching" task-type dialog if present.

    TryRating shows this modal overlay whenever the session encounters a
    NEW type of task for the first time.  Until dismissed, the dialog
    blocks all interaction with the survey page underneath — including
    XPath extraction of the Request ID.

    Confirmed UI from live screenshot:
        Title  : "Now Launching [task-type-name]"
        Body   : "You are about to begin a different task type.
                  Please be sure you are familiar with the guidelines."
        Buttons: × (top-right close) | OK (bottom-right, blue)

    Strategy
    --------
    1. Detect the dialog using its unique body text (short 2-second
       timeout — fast no-op when not present).
    2. Click OK (primary, confirms guideline acknowledgement).
    3. Fall back to the × close button if OK is not found.

    Called at two points per cycle:
    * After ``page.reload()`` — a task from the previous cycle may have
      left the dialog pending.
    * After ``click_check_surveys()`` — a newly returned task type
      triggers the dialog immediately after Check Now responds.

    Returns
    -------
    bool
        ``True`` if the dialog was found and dismissed.
    """
    try:
        # Fast detection: 2-second timeout so normal cycles (no dialog)
        # add only ~50ms of overhead via the immediate is_visible() check.
        indicator = page.locator(TryRatingSelectors.TASK_TYPE_DIALOG_INDICATOR)
        indicator.wait_for(state="visible", timeout=2_000)

        logger.info(
            "Task type launch dialog detected — dismissing before extraction."
        )

        # Primary: click OK
        try:
            ok_btn = page.locator(TryRatingSelectors.TASK_TYPE_DIALOG_OK).first
            ok_btn.wait_for(state="visible", timeout=3_000)
            ok_btn.click()
            page.wait_for_timeout(600)  # let animation finish
            logger.info("Task type dialog dismissed via OK button.")
            return True
        except PlaywrightTimeoutError:
            pass

        # Fallback: click × close button
        try:
            close_btn = page.locator(
                TryRatingSelectors.TASK_TYPE_DIALOG_CLOSE
            ).first
            close_btn.wait_for(state="visible", timeout=3_000)
            close_btn.click()
            page.wait_for_timeout(600)
            logger.info("Task type dialog dismissed via close button.")
            return True
        except PlaywrightTimeoutError:
            logger.warning(
                "Task type dialog detected but neither OK nor close button "
                "was clickable — extraction may fail."
            )
            return False

    except PlaywrightTimeoutError:
        # Dialog not present — normal case, fast path.
        return False
    except Exception as exc:
        logger.warning("Unexpected error in dismiss_task_type_dialog: {}", exc)
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

    Call this only AFTER ``click_check_surveys()`` has run and the page
    has been confirmed to NOT be in a no-survey state.

    Strategies (in priority order)
    --------------------------------
    1. **XPath sibling** — element after "Request ID" label, digits-only >= 6.
    2. **XPath following** — same constraint, broader document scope.
    3. **Label-anchored text scan** — regex anchored to "Request ID" label.

    Returns
    -------
    str | None
        Request ID string (e.g. ``"695584131"``), or ``None``.
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
    # Only matches digits that explicitly follow the "Request ID" label.
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
    1. Random jitter (anti-bot mitigation).
    2. Soft page reload — re-initialises TryRating SPA, forces fresh API call.
    3. Post-reload URL guard — bail if session expired.
    4. Always click "Check Now" — TryRating's server-fetch trigger.
    5. Post-click no-survey check — if confirmed empty, return None.
    6. Extract Request ID via XPath.

    Why always click Check Now (even when "No more surveys" shows)?
    ----------------------------------------------------------------
    After reload, TryRating renders its LAST KNOWN state before clicking
    Check Now.  The page may show "No more surveys" even if a new survey
    has just become available on TryRating's server.  Clicking the button
    is what triggers the actual live fetch.  Only the post-click state is
    the ground truth.

    Why soft reload, not hard reload?
    ----------------------------------
    Survey data comes from TryRating's API, not browser cache.  A soft
    reload (F5 equivalent) re-initialises the SPA and triggers fresh API
    calls.  Hard reload (Ctrl+Shift+R) additionally re-downloads JS/CSS
    bundles — wasted bandwidth with no benefit for dynamic data.

    Parameters
    ----------
    page:
        Active Playwright page on ``tryrating.com/app/survey/rate``.

    Returns
    -------
    str | None
        Request ID string if a survey is visible, ``None`` otherwise.
    """
    # ── 1. Jitter ─────────────────────────────────────────────────────────────
    jitter_ms = random.randint(JITTER_MIN_MS, JITTER_MAX_MS)
    logger.debug("Pre-check jitter: {}ms.", jitter_ms)
    page.wait_for_timeout(jitter_ms)

    # ── 2. Soft reload ────────────────────────────────────────────────────────
    # Re-initialises TryRating SPA state so the page fetches live survey data.
    # wait_until="networkidle" waits for the SPA's initial API calls to settle.
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

    # ── 3. Post-reload URL guard ───────────────────────────────────────────────
    # If reload redirected to login, bail out.
    # watchtower._job_survey_check handles re-auth on the next cycle.
    if "/survey/" not in page.url.lower():
        logger.warning(
            "Reload redirected away from survey page (url={}) — "
            "session may have expired. Skipping extraction.",
            page.url,
        )
        return None

    # ── 4. Dismiss task-type dialog (post-reload) ───────────────────────────
    # A "Now Launching" dialog from the previous cycle may still be open
    # after reload.  Dismiss it so Check Now can be reached.
    dismiss_task_type_dialog(page)

    # ── 5. Always click Check Now ─────────────────────────────────────────────
    # "Check Now" is TryRating's server-fetch trigger. It must run on every
    # cycle regardless of what the page shows after reload.
    # The post-reload state is the LAST KNOWN state, not live data.
    # DO NOT gate this on a pre-click no-survey check — the page may show
    # "No more surveys" even when a new survey just appeared on the server.
    click_check_surveys(page)

    # ── 6. Dismiss task-type dialog (post-Check Now) ────────────────────────
    # When TryRating returns a NEW type of task, it immediately renders
    # the "Now Launching" modal over the page.  XPath cannot see the
    # Request ID underneath until this dialog is dismissed.
    dismiss_task_type_dialog(page)

    # ── 7. Post-click no-survey check ─────────────────────────────────────────
    # NOW we trust the state — Check Now has fetched live data from the server.
    if _is_no_survey_state(page):
        logger.debug("'No more surveys' confirmed after Check Now — none available.")
        return None

    # ── 6. Extract Request ID ─────────────────────────────────────────────────
    request_id = extract_request_id(page)

    if request_id:
        logger.info("Survey detected — Request ID: {}", request_id)
    else:
        logger.debug("No surveys currently available.")

    return request_id
