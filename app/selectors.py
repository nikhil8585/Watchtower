"""
app/selectors.py
================
All Playwright selectors for every monitored website.

This is the **only** file that should change when a target site updates
its UI.  No selector strings exist anywhere else in the codebase.

Maintenance workflow
--------------------
1. A check cycle starts failing / logging "element not found".
2. Open the site in Chrome with DevTools (F12 → Inspector).
3. Locate the broken element and copy an updated selector.
4. Update ONLY the relevant constant in this file.
5. Restart Watchtower (``sudo systemctl restart watchtower``).

Selector strategy (preferred order)
-------------------------------------
1. ARIA roles + accessible names  → most stable across redesigns
2. ``data-testid`` / ``data-*`` attributes → dev-stable
3. Visible text content            → readable, language-dependent
4. CSS class / ID                  → last resort; fragile

All values are raw strings — no f-strings, no runtime concatenation.

Initial values
--------------
The selectors below were written against the TryRating platform as of
mid-2025.  **Verify them against the live site before first deployment**
using the DevTools inspector.  Adjust as needed and document the change
with a comment and the date.
"""


class TryRatingSelectors:
    """
    Playwright selectors for the TryRating web application.

    Every attribute on this class is a CSS/text selector string that can
    be passed directly to ``page.locator()``.

    NOTE
    ----
    Run the application once in non-headless debug mode
    (``HEADLESS=false`` in .env) and use browser DevTools to confirm each
    selector resolves to exactly one visible element before going to
    production.
    """

    # ── Authentication ────────────────────────────────────────────────────────
    # Confirmed against live TryRating login page (screenshot verified).

    # Email input — label on page is "Email", field accepts email addresses.
    # input[type='email'] is the most reliable; extras are fallbacks.
    USERNAME_INPUT: str = (
        "input[type='email'], "
        "input[name='email'], "
        "input[placeholder*='email' i]"
    )

    # Password input — standard, confirmed present on login page.
    PASSWORD_INPUT: str = "input[type='password']"

    # Submit button — confirmed label is "Login" (exact text, blue button).
    # Placed first; fallbacks handle any future label changes.
    LOGIN_BUTTON: str = (
        "button:has-text('Login'), "
        "button[type='submit'], "
        "input[type='submit']"
    )

    # An element ONLY present after a successful login.
    # Confirmed: post-login page is tryrating.com/app/home
    # showing the heading "Welcome to TryRating".
    LOGIN_SUCCESS_INDICATOR: str = (
        "h1:has-text('Welcome to TryRating'), "
        "button:has-text('Get a Survey'), "
        ".sidebar, "
        "nav"
    )

    # Element present on the login page — used to detect session expiry.
    # Confirmed: heading is "Login to TryRating", password field always visible.
    LOGIN_PAGE_INDICATOR: str = (
        "input[type='password'], "
        "h1:has-text('Login to TryRating'), "
        "[data-testid='login-form']"
    )

    # URL keyword that identifies the login page.
    # Confirmed login URL path contains 'login'.
    LOGIN_URL_FRAGMENT: str = "login"

    # ── Navigation ────────────────────────────────────────────────────────────

    # Navigation to the surveys page.
    # Two confirmed paths from the screenshots:
    # 1. Left sidebar icon — href contains 'survey'
    # 2. "Get a Survey" blue button on the home page (tryrating.com/app/home)
    # Navigation module falls back to direct URL if both selectors miss.
    SURVEYS_NAV_LINK: str = (
        "a[href*='survey' i], "
        "button:has-text('Get a Survey'), "
        "[data-testid='surveys-nav']"
    )

    # ── Survey page ───────────────────────────────────────────────────────────

    # The "Check Now" / "Get Surveys" / "Check Surveys" call-to-action button.
    # Confirmed from live TryRating UI: button is labelled "Check Now".
    # This button fetches fresh tasks from TryRating's server.
    GET_SURVEYS_BUTTON: str = (
        "button:has-text('Check Now'), "
        "button:has-text('Get Surveys'), "
        "button:has-text('Check Surveys'), "
        "button:has-text('Find Surveys'), "
        "button:has-text('Refresh Surveys'), "
        "button:has-text('Refresh'), "
        "[data-testid='get-surveys-button']"
    )

    # ── Task type launch dialog ────────────────────────────────────────────────
    # Appears when starting a NEW TYPE of task for the first time in a session.
    # Blocks all page interaction until dismissed.
    #
    # Confirmed UI (live screenshot, 2025-07):
    #   Title  : "Now Launching [task-type-name]"  (e.g. "Now Launching SFX-MUSHRA")
    #   Body   : "You are about to begin a different task type.
    #             Please be sure you are familiar with the guidelines."
    #   Buttons: × (top-right close)  |  OK (bottom-right, blue)
    #
    # Dismiss by clicking OK — confirms guideline acknowledgement.
    # Must be checked BOTH after page.reload() and after clicking Check Now.

    # Unique body text used to DETECT the dialog is open.
    TASK_TYPE_DIALOG_INDICATOR: str = (
        "text=You are about to begin a different task type"
    )

    # Primary dismiss action — click OK.
    TASK_TYPE_DIALOG_OK: str = "button:has-text('OK')"

    # Fallback dismiss — × close button (if OK is not found).
    TASK_TYPE_DIALOG_CLOSE: str = (
        "button[aria-label='Close'], "
        "button.close, "
        "[data-dismiss='modal'], "
        "button:has-text('×')"
    )


    # Container for a survey card.
    # XPath: any element containing the text 'Request ID' anywhere inside it.
    # Used when iterating multiple survey cards.
    SURVEY_CARD: str = (
        "xpath=//*[.//text()[normalize-space()='Request ID']]"
    )

    # ── Request ID (XPath-first, confirmed from live screenshot) ──────────────
    # Confirmed UI structure (2025-07):
    #
    #   Request ID        <- label text node
    #   695584131         <- value text node (sibling element below label)
    #
    # All three strategies anchor to the literal label text 'Request ID',
    # so they work regardless of CSS class names or layout changes.

    # Strategy 1 (PRIMARY): following SIBLING element containing only digits >= 6 chars.
    # Works when label and value are siblings inside the same parent.
    REQUEST_ID_XPATH_SIBLING: str = (
        "xpath=//*[normalize-space(text())='Request ID']"
        "/following-sibling::*"
        "[translate(normalize-space(.), '0123456789', '')='' "
        "and string-length(normalize-space(.)) >= 6][1]"
    )

    # Strategy 2 (FALLBACK): following element anywhere in the document after
    # the label, restricted to digit-only content >= 6 chars.
    # Works when label and value are NOT direct siblings.
    REQUEST_ID_XPATH_FOLLOWING: str = (
        "xpath=//*[normalize-space(text())='Request ID']"
        "/following::*"
        "[translate(normalize-space(.), '0123456789', '')='' "
        "and string-length(normalize-space(.)) >= 6][1]"
    )

    # Strategy 3 (BROAD FALLBACK): first following text node after the label,
    # validated by regex in Python. Used when the value is a raw text node,
    # not wrapped in an element.
    REQUEST_ID_XPATH_TEXT_NODE: str = (
        "xpath=//*[normalize-space(text())='Request ID']"
        "/following::text()[normalize-space()][1]"
    )

    # Text / element shown when no surveys are currently available.
    # Confirmed from live screenshot: 'No more surveys' heading.
    NO_SURVEYS_TEXT: str = (
        "text='No more surveys', "
        "text='Please check back later', "
        "text='No surveys available', "
        "text='No results', "
        ".no-surveys, "
        ".empty-state"
    )
