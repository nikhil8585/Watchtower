# Watchtower — Utility Scripts

This directory contains operational and diagnostic scripts for Watchtower.

Scripts in this directory are **not committed** to the public repository as they
may contain environment-specific logic or sensitive test data.

## Creating Your Own Test Scripts

Clone this repo and add scripts locally. They are git-ignored via `scripts/test_*.py`.

### Recommended scripts to create locally

| Script | Purpose |
|--------|---------|
| `scripts/test_smtp.py` | Verify Gmail SMTP credentials work |
| `scripts/test_login.py` | Verify TryRating login selectors |
| `scripts/test_selector.py` | Inspect live DOM to find Request ID selector |
| `scripts/reset_state.py` | Clear `data/state.json` (useful during testing) |
