"""
app/login.py
============
TryRating authentication logic for Watchtower.

Responsibilities
----------------
* Detect whether the current page requires authentication.
* Perform the login flow (navigate → fill → submit → confirm).
* Update the :class:`~app.state.StateManager` on success.
* Retry up to ``MAX_LOGIN_RETRIES`` times before reporting failure.

No credentials are stored in this module — they are consumed directly
from the :class:`~app.config.Config` instance at call time.
"""

from __future__ import annotations

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app.config import Config
from app.constants import (
    LOGIN_RETRY_DELAY_SECONDS,
    LOGIN_SUCCESS_TIMEOUT,
    MAX_LOGIN_RETRIES,
    SHORT_ELEMENT_TIMEOUT,
    TRYRATING_LOGIN_URL,
)
from app.logger import logger
from app.selectors import TryRatingSelectors
from app.state import StateManager


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------

def is_login_required(page: Page) -> bool:
    """
    Return ``True`` if the browser is currently on a login/auth page.

    Two independent signals are checked:

    1. The page URL contains the ``LOGIN_URL_FRAGMENT`` keyword.
    2. A login-form element is visible on the page.

    Checking both signals avoids false-positives on pages that happen to
    contain the word "login" in unrelated content.

    Parameters
    ----------
    page:
        The active Playwright page.
    """
    url = page.url.lower()
    if TryRatingSelectors.LOGIN_URL_FRAGMENT in url:
        logger.debug("Login required — URL contains '{}'.", TryRatingSelectors.LOGIN_URL_FRAGMENT)
        return True

    try:
        page.locator(TryRatingSelectors.LOGIN_PAGE_INDICATOR).first.wait_for(
            state="visible",
            timeout=SHORT_ELEMENT_TIMEOUT,
        )
        logger.debug("Login required — login form detected on page.")
        return True
    except PlaywrightTimeoutError:
        return False


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def perform_login(
    page: Page,
    config: Config,
    state: StateManager,
) -> bool:
    """
    Authenticate with TryRating.

    Steps
    -----
    1. Navigate to the login page.
    2. Fill the username and password fields.
    3. Click the submit button.
    4. Wait for a post-login success indicator.
    5. Update the state manager with the login timestamp.

    Retries the full sequence up to ``MAX_LOGIN_RETRIES`` times.

    Parameters
    ----------
    page:
        Active Playwright page.
    config:
        Application configuration — credentials are read here.
    state:
        State manager — ``last_login`` timestamp updated on success.

    Returns
    -------
    bool
        ``True`` on successful authentication, ``False`` after all
        retries are exhausted.
    """
    for attempt in range(1, MAX_LOGIN_RETRIES + 1):
        try:
            logger.info(
                "Login attempt {}/{} — navigating to {}.",
                attempt,
                MAX_LOGIN_RETRIES,
                TRYRATING_LOGIN_URL,
            )
            page.goto(TRYRATING_LOGIN_URL, wait_until="domcontentloaded")

            # Fill username
            username_locator = page.locator(TryRatingSelectors.USERNAME_INPUT).first
            username_locator.wait_for(state="visible")
            username_locator.clear()
            username_locator.fill(config.tryrating_username)
            logger.debug("Username field populated.")

            # Fill password (value intentionally not logged)
            password_locator = page.locator(TryRatingSelectors.PASSWORD_INPUT).first
            password_locator.wait_for(state="visible")
            password_locator.clear()
            password_locator.fill(config.tryrating_password)
            logger.debug("Password field populated.")

            # Submit
            submit_locator = page.locator(TryRatingSelectors.LOGIN_BUTTON).first
            submit_locator.wait_for(state="visible")
            submit_locator.click()
            logger.debug("Login form submitted.")

            # Confirm successful session
            page.locator(TryRatingSelectors.LOGIN_SUCCESS_INDICATOR).first.wait_for(
                state="visible",
                timeout=LOGIN_SUCCESS_TIMEOUT,
            )

            state.update_last_login()
            logger.info("Login successful.")
            return True

        except PlaywrightTimeoutError as exc:
            logger.warning(
                "Login attempt {}/{} timed out: {}",
                attempt,
                MAX_LOGIN_RETRIES,
                exc,
            )
        except Exception as exc:
            logger.warning(
                "Login attempt {}/{} raised an unexpected error: {}",
                attempt,
                MAX_LOGIN_RETRIES,
                exc,
            )

        if attempt < MAX_LOGIN_RETRIES:
            logger.info(
                "Waiting {}s before retry.", LOGIN_RETRY_DELAY_SECONDS
            )
            page.wait_for_timeout(int(LOGIN_RETRY_DELAY_SECONDS * 1000))

    logger.error(
        "Authentication failed — all {} login attempts exhausted.",
        MAX_LOGIN_RETRIES,
    )
    return False


# ---------------------------------------------------------------------------
# Session guard (used by the orchestrator)
# ---------------------------------------------------------------------------

def ensure_authenticated(
    page: Page,
    config: Config,
    state: StateManager,
) -> bool:
    """
    Verify the session is active; log in if it is not.

    This is the primary entry point used by :class:`~app.watchtower.Watchtower`
    before each monitoring cycle.

    Parameters
    ----------
    page:
        Active Playwright page.
    config:
        Application configuration.
    state:
        State manager.

    Returns
    -------
    bool
        ``True`` if the session is (or becomes) authenticated.
    """
    if is_login_required(page):
        logger.info("Session is not authenticated — starting login flow.")
        return perform_login(page, config, state)

    logger.debug("Session is already authenticated — skipping login.")
    return True
