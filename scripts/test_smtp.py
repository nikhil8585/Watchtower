"""
scripts/test_smtp.py
====================
Standalone Gmail SMTP test for Watchtower.

Loads credentials from .env and sends a real test email.
Run this BEFORE deploying to confirm SMTP is working.

Usage (from project root, inside venv):
    python scripts/test_smtp.py
"""

from __future__ import annotations

import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import os

# Load .env from project root
env_path = PROJECT_ROOT / ".env"
if not env_path.exists():
    print(f"[ERROR] .env not found at: {env_path}")
    sys.exit(1)

load_dotenv(dotenv_path=env_path)

SMTP_HOST        = os.getenv("SMTP_HOST", "")
SMTP_PORT_STR    = os.getenv("SMTP_PORT", "587")
SMTP_EMAIL       = os.getenv("SMTP_EMAIL", "")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "")

# ---------------------------------------------------------------------------
# Validate loaded values
# ---------------------------------------------------------------------------

missing = []
if not SMTP_HOST:         missing.append("SMTP_HOST")
if not SMTP_EMAIL:        missing.append("SMTP_EMAIL")
if not SMTP_APP_PASSWORD: missing.append("SMTP_APP_PASSWORD")

if missing:
    print(f"[ERROR] Missing .env values: {', '.join(missing)}")
    sys.exit(1)

try:
    SMTP_PORT = int(SMTP_PORT_STR)
except ValueError:
    print(f"[ERROR] SMTP_PORT must be an integer, got: {SMTP_PORT_STR!r}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Build test email
# ---------------------------------------------------------------------------

now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
subject = "[Watchtower] SMTP Test"
body = (
    "This is a test email from Watchtower.\n\n"
    f"Sent at : {now}\n"
    f"Host    : {SMTP_HOST}:{SMTP_PORT}\n"
    f"From    : {SMTP_EMAIL}\n\n"
    "If you received this, Gmail SMTP is configured correctly.\n\n"
    "---\nWatchtower SMTP test script"
)

msg = MIMEMultipart()
msg["From"]    = SMTP_EMAIL
msg["To"]      = SMTP_EMAIL
msg["Subject"] = subject
msg.attach(MIMEText(body, "plain", "utf-8"))

# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

print(f"\nWatchtower SMTP Test")
print("-" * 40)
print(f"Host     : {SMTP_HOST}:{SMTP_PORT}")
print(f"Email    : {SMTP_EMAIL}")
print(f"Password : {'*' * len(SMTP_APP_PASSWORD.replace(' ', ''))}")
print("-" * 40)
print(f"Connecting...")

try:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.ehlo()
        print("  [OK] EHLO")

        server.starttls()
        print("  [OK] STARTTLS")

        server.ehlo()

        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        print("  [OK] Login")

        server.sendmail(SMTP_EMAIL, [SMTP_EMAIL], msg.as_string())
        print(f"  [OK] Email sent to {SMTP_EMAIL}")

    print("\n" + "-" * 40)
    print("SUCCESS - check your inbox for the test email.")
    print("-" * 40 + "\n")
    sys.exit(0)

except smtplib.SMTPAuthenticationError as e:
    print(f"\n[FAILED] Authentication error: {e}")
    print("\nTroubleshooting:")
    print("  1. The SMTP_APP_PASSWORD must be a Gmail App Password (16 chars)")
    print("     NOT your regular Gmail password.")
    print("  2. Generate one at: https://myaccount.google.com/apppasswords")
    print("  3. 2-Step Verification must be enabled on your Google account.")
    sys.exit(1)

except smtplib.SMTPException as e:
    print(f"\n[FAILED] SMTP error: {e}")
    sys.exit(1)

except (OSError, TimeoutError) as e:
    print(f"\n[FAILED] Network error: {e}")
    print("Check SMTP_HOST and SMTP_PORT in .env.")
    sys.exit(1)
