"""
run.py
======
Watchtower entry point.

This is the ONLY script executed directly.  It:
1. Ensures the project root is on ``sys.path`` for relative imports.
2. Bootstraps the logger at INFO level for startup messages.
3. Loads and validates configuration via :class:`~app.config.Config`.
4. Reconfigures the logger at the level specified in .env.
5. Instantiates and starts :class:`~app.watchtower.Watchtower`.

Usage::

    # Inside the virtualenv:
    python run.py

    # Via systemd (see deployment docs):
    ExecStart=/home/opc/Watchtower/venv/bin/python /home/opc/Watchtower/run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is importable regardless of cwd
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Imports (after sys.path is set)
# ---------------------------------------------------------------------------
from app.config import Config
from app.health import run_startup_checks
from app.logger import logger, setup_logger
from app.watchtower import Watchtower


def main() -> None:
    """Bootstrap and launch Watchtower."""

    # Phase 1: bootstrap logger at INFO so startup messages are visible
    # even before the .env log level is known.
    setup_logger("INFO")
    logger.info("Watchtower bootstrap — loading configuration.")

    # Phase 2: load and validate configuration
    try:
        config = Config.load()
    except FileNotFoundError as exc:
        logger.critical(
            "Cannot start — .env file not found: {}\n"
            "Copy .env.example to .env and fill in all required values.",
            exc,
        )
        sys.exit(1)
    except EnvironmentError as exc:
        logger.critical(
            "Cannot start — configuration error: {}", exc
        )
        sys.exit(1)

    # Phase 3: reconfigure logger at the level specified in .env
    setup_logger(config.log_level)
    logger.info("Configuration loaded: {}", config.safe_repr())

    # Phase 4: startup self-check — verifies all dependencies before
    # entering the scheduler.  Calls sys.exit(1) on any failure.
    run_startup_checks(config)

    # Phase 5: run
    watcher = Watchtower(config)
    watcher.start()


if __name__ == "__main__":
    main()
