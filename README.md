# Watchtower

Production website monitoring platform.  Version 1.0 monitors TryRating and
sends an email notification the instant a new survey is available.

---

## Architecture

```
run.py
  └─ Config.load()             # validates .env at startup
  └─ setup_logger()            # loguru console + rotating file
  └─ Watchtower(config)
       ├─ StateManager          # atomic JSON state (data/state.json)
       ├─ BrowserManager        # Playwright / Chromium lifecycle
       └─ BlockingScheduler
            ├─ survey_check     # every CHECK_INTERVAL seconds
            ├─ heartbeat        # daily at 08:00 (configurable)
            └─ log_cleanup      # weekly Sunday 03:00
```

Each module has exactly one responsibility.  All selectors live in
`app/selectors.py`.  All constants live in `app/constants.py`.  All
credentials come from `.env`.

---

## Quick Start

### 1. Clone and enter the project

```bash
cd /home/opc/Watchtower
```

### 2. Create and activate the virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

### 4. Configure

```bash
cp .env.example .env
nano .env          # fill in all values
```

Required `.env` values:

| Variable              | Example                        | Notes                          |
|-----------------------|--------------------------------|--------------------------------|
| `WATCHTOWER_NAME`     | `Watchtower-Production`        | Instance label (logs/email)    |
| `CHECK_INTERVAL`      | `60`                           | Seconds between survey checks  |
| `HEADLESS`            | `true`                         | `false` for local debugging    |
| `SLOW_MO`             | `0`                            | ms delay between actions       |
| `DEFAULT_TIMEOUT`     | `30000`                        | Playwright element timeout ms  |
| `TIMEZONE`            | `Asia/Kolkata`                 | IANA timezone string           |
| `LOG_LEVEL`           | `INFO`                         | DEBUG / INFO / WARNING / ERROR |
| `TRYRATING_USERNAME`  | `user@example.com`             | TryRating login email          |
| `TRYRATING_PASSWORD`  | `secret`                       | TryRating password             |
| `SMTP_HOST`           | `smtp.gmail.com`               |                                |
| `SMTP_PORT`           | `587`                          | STARTTLS port                  |
| `SMTP_EMAIL`          | `you@gmail.com`                | Sender and recipient           |
| `SMTP_APP_PASSWORD`   | `abcd efgh ijkl mnop`          | Gmail App Password (not Gmail password) |

### 5. Run manually (test before deploying)

```bash
source venv/bin/activate
python run.py
```

Watch the console output.  You should see:

```
INFO  | Watchtower 1.0.0 starting
INFO  | Browser launched successfully
INFO  | Login successful
INFO  | Navigated to surveys
INFO  | Scheduler active — survey check every 60s
```

Press `Ctrl+C` to stop.

---

## Selectors — Critical First Step

Before running in production, **verify every selector** in
`app/selectors.py` against the live TryRating site:

1. Set `HEADLESS=false` and `SLOW_MO=1000` in `.env`
2. Run `python run.py`
3. Watch the browser — confirm it logs in and reaches the surveys page
4. If it fails, open Chrome DevTools → Inspector → copy the correct
   selector and update `app/selectors.py`
5. Set `HEADLESS=true` and `SLOW_MO=0` before deploying

The selector most likely to need adjustment is `REQUEST_ID_ELEMENT`.

---

## Deploy as a systemd Service

### Create the unit file

```bash
sudo nano /etc/systemd/system/watchtower.service
```

Paste:

```ini
[Unit]
Description=Watchtower — Website Monitoring Platform
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=opc
WorkingDirectory=/home/opc/Watchtower
ExecStart=/home/opc/Watchtower/venv/bin/python /home/opc/Watchtower/run.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable watchtower
sudo systemctl start watchtower
sudo systemctl status watchtower
```

### View logs

```bash
# systemd journal (live)
sudo journalctl -u watchtower -f

# Application log files
tail -f /home/opc/Watchtower/logs/watchtower_$(date +%Y-%m-%d).log
```

---

## Update Procedure

```bash
# 1. Pull latest code
cd /home/opc/Watchtower
git pull origin main

# 2. Install any new dependencies
source venv/bin/activate
pip install -r requirements.txt

# 3. Restart the service
sudo systemctl restart watchtower

# 4. Confirm it restarted cleanly
sudo systemctl status watchtower
sudo journalctl -u watchtower -n 50
```

---

## Troubleshooting

### Watchtower fails to start

```bash
sudo journalctl -u watchtower -n 100 --no-pager
```

Common causes:
- `.env` file missing → `cp .env.example .env` and fill in values
- Missing dependency → `pip install -r requirements.txt`
- Chromium not installed → `playwright install chromium && playwright install-deps chromium`

---

### Login keeps failing

1. Set `HEADLESS=false` locally and run `python run.py`
2. Watch the browser — does it reach the login page?
3. Open DevTools → check the selector for `USERNAME_INPUT` and `PASSWORD_INPUT`
4. Update `app/selectors.py` if needed
5. Verify `TRYRATING_USERNAME` and `TRYRATING_PASSWORD` are correct in `.env`

---

### Surveys not detected (no emails arriving)

1. Check logs: `tail -f logs/watchtower_$(date +%Y-%m-%d).log`
2. Look for "No surveys currently available" vs. element-not-found errors
3. If element errors: update `REQUEST_ID_ELEMENT` in `app/selectors.py`
4. Manually open TryRating, find the Request ID element, copy its selector

---

### Email not sending

1. Verify `SMTP_APP_PASSWORD` is a **Gmail App Password** (16 characters),
   NOT your regular Gmail password
2. Generate at: https://myaccount.google.com/apppasswords
3. Requires 2-Step Verification enabled on your Google Account
4. Check for `SMTPAuthenticationError` in the logs

---

### Check current state

```bash
cat /home/opc/Watchtower/data/state.json
```

---

### Force a survey check immediately

The scheduler runs automatically.  To trigger immediately:

```bash
sudo systemctl restart watchtower
```

The first check runs at the next interval tick (within `CHECK_INTERVAL` seconds of start).

---

## Project Structure

```
Watchtower/
├── app/
│   ├── __init__.py         # Package version
│   ├── config.py           # Config dataclass — loads and validates .env
│   ├── constants.py        # All constants (timeouts, paths, URLs)
│   ├── logger.py           # Loguru setup — import logger from here
│   ├── selectors.py        # ALL Playwright selectors — update here on UI changes
│   ├── state.py            # Atomic JSON state manager
│   ├── browser.py          # Playwright / Chromium lifecycle
│   ├── login.py            # TryRating authentication
│   ├── navigation.py       # Page navigation helpers
│   ├── survey_checker.py   # Survey detection and Request ID extraction
│   ├── notifier.py         # Gmail SMTP email sender
│   └── watchtower.py       # Main orchestrator + APScheduler
├── config/                 # Reserved for future site-specific config files
├── data/
│   └── state.json          # Persistent state (seen IDs, timestamps)
├── logs/                   # Rotating daily log files (auto-created)
├── scripts/                # Reserved for utility / maintenance scripts
├── .env                    # Secrets — never commit
├── .env.example            # Template — safe to commit
├── .gitignore
├── requirements.txt
├── README.md
└── run.py                  # Entry point
```

---

## Extending to New Sites

The architecture supports additional monitored websites without redesign:

1. Add a new `class SiteNameSelectors` to `app/selectors.py`
2. Create `app/site_name_login.py`, `app/site_name_navigation.py`,
   `app/site_name_checker.py` following the same patterns
3. Add site credentials to `.env` and `app/config.py`
4. Register a new job in `app/watchtower.py`

No core files need modification.

---

## Security Notes

- `.env` is in `.gitignore` and must never be committed
- Passwords are never written to log files (`safe_repr()` redacts them)
- `diagnose=False` on the file log sink prevents local variable capture
- All credentials are loaded from environment variables at runtime

---

## License

Private — internal use only.
