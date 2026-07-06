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

    # Primary email / username input on the login form
    USERNAME_INPUT: str = (
        "input[type='email'], "
        "input[name='email'], "
        "input[name='username'], "
        "input[placeholder*='email' i], "
        "input[placeholder*='username' i]"
    )

    # Password input on the login form
    PASSWORD_INPUT: str = "input[type='password']"

    # The submit / "Sign in" button
    LOGIN_BUTTON: str = (
        "button[type='submit'], "
        "input[type='submit'], "
        "button:has-text('Sign in'), "
        "button:has-text('Log in'), "
        "button:has-text('Login')"
    )

    # An element that is ONLY present after a successful login.
    # Used to confirm the session is established.
    # Examples: top navigation bar, user avatar, dashboard heading.
    LOGIN_SUCCESS_INDICATOR: str = (
        "nav.authenticated, "
        ".dashboard-header, "
        "[data-testid='user-menu'], "
        ".user-avatar, "
        "header .user-name, "
        ".main-nav"
    )

    # Element present on the login page — used to detect session expiry.
    # We check for this after navigating to any page.
    LOGIN_PAGE_INDICATOR: str = (
        "form.login-form, "
        "[data-testid='login-form'], "
        "input[type='password']"
    )

    # URL keyword that identifies the login page.
    # Matched against the lowercased page URL.
    LOGIN_URL_FRAGMENT: str = "login"

    # ── Navigation ────────────────────────────────────────────────────────────

    # Sidebar / top-nav link that leads to the Surveys section
    SURVEYS_NAV_LINK: str = (
        "a:has-text('Surveys'), "
        "nav a[href*='survey' i], "
        "[data-testid='surveys-nav'], "
        ".nav-link:has-text('Surveys')"
    )

    # ── Survey page ───────────────────────────────────────────────────────────

    # The "Get Surveys" / "Check Surveys" / "Refresh" call-to-action button.
    # This button fetches fresh tasks from TryRating's server.
    GET_SURVEYS_BUTTON: str = (
        "button:has-text('Get Surveys'), "
        "button:has-text('Check Surveys'), "
        "button:has-text('Find Surveys'), "
        "button:has-text('Refresh Surveys'), "
        "button:has-text('Refresh'), "
        "[data-testid='get-surveys-button']"
    )

    # Container for a single survey card / table row.
    # Used to iterate over available surveys.
    SURVEY_CARD: str = (
        ".survey-card, "
        ".survey-item, "
        ".task-card, "
        ".task-item, "
        "[data-testid='survey-card'], "
        "[data-testid='task-card'], "
        "li.survey, "
        "tr.survey-row"
    )

    # The element inside a survey card that displays the Request ID.
    # TryRating Request IDs are long numeric strings, e.g. 695584131.
    # This is the PRIMARY selector — update this first if surveys stop being detected.
    REQUEST_ID_ELEMENT: str = (
        "[data-testid='request-id'], "
        ".request-id, "
        "#request-id, "
        "span[class*='request-id' i], "
        "td[class*='request' i], "
        "span[class*='task-id' i], "
        "[data-field='requestId']"
    )

    # Text / element shown when no surveys are currently available.
    # Presence of this element is used to confirm a "no surveys" state
    # rather than a page-load or selector failure.
    NO_SURVEYS_TEXT: str = (
        "text='No surveys available', "
        "text='No tasks available', "
        "text='No results', "
        ".no-surveys, "
        ".empty-state, "
        "[data-testid='empty-surveys']"
    )
