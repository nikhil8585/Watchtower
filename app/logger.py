"""
app/logger.py
=============
Centralised Loguru logger configuration for Watchtower.

All modules import ``logger`` from this module — never from loguru directly.
This ensures a single configuration point and consistent formatting.

Sinks
-----
* **Console (stderr)** — coloured, human-readable, DEBUG+ in dev / INFO+ in prod.
* **Rotating file**    — plain text, daily rotation, 30-day retention, gzip
  compressed.  ``diagnose=False`` so local variables are never written to
  disk (avoids accidental credential leakage).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger as _logger

from app.constants import (
    LOG_FILE_FORMAT,
    LOG_FORMAT,
    LOG_RETENTION,
    LOG_ROTATION,
    LOGS_DIR,
)


def setup_logger(
    log_level: str = "INFO",
    log_dir: Optional[Path] = None,
) -> None:
    """
    Configure the global Loguru logger.

    This function is idempotent: calling it multiple times replaces the
    previous configuration rather than stacking sinks.

    Parameters
    ----------
    log_level:
        Minimum log level for both sinks.  Case-insensitive.
        Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    log_dir:
        Directory for rotating log files.
        Defaults to ``<project_root>/logs/``.
    """
    _logger.remove()  # Remove all existing handlers first

    effective_dir: Path = log_dir or LOGS_DIR
    effective_dir.mkdir(parents=True, exist_ok=True)

    level = log_level.upper()

    # ── Console sink ─────────────────────────────────────────────────────────
    _logger.add(
        sys.stderr,
        level=level,
        format=LOG_FORMAT,
        colorize=True,
        backtrace=True,
        diagnose=True,   # show local variables on console for debugging
    )

    # ── File sink ─────────────────────────────────────────────────────────────
    # One log file per day; older files are gzipped.
    # enqueue=True makes writes non-blocking and thread-safe.
    # diagnose=False prevents secrets in local vars from reaching disk.
    log_file = effective_dir / "watchtower_{time:YYYY-MM-DD}.log"
    _logger.add(
        str(log_file),
        level=level,
        format=LOG_FILE_FORMAT,
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression="gz",
        backtrace=True,
        diagnose=False,
        enqueue=True,
        encoding="utf-8",
    )

    _logger.info(
        "Logger configured — level={}, log_dir={}",
        level,
        effective_dir,
    )


# ---------------------------------------------------------------------------
# Public re-export
# ---------------------------------------------------------------------------
# Every module in Watchtower does:
#   from app.logger import logger
# This is the one authoritative logger instance.

logger = _logger
