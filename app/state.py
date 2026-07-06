"""
app/state.py
============
JSON-backed persistent state manager for Watchtower.

Design goals
------------
* **Atomic writes** — uses ``tempfile.mkstemp`` + ``os.replace`` so the
  state file is never left in a partially-written state.
* **Corruption recovery** — invalid JSON is detected, logged, and reset
  to defaults without crashing.
* **No duplicates** — :meth:`record_request_id` is idempotent.
* **No SQLite** — plain JSON only, as specified.
* **Thread-safety** — ``os.replace`` is atomic on Linux (POSIX rename).
  APScheduler's ``max_instances=1`` constraint prevents concurrent access.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.constants import (
    STATE_FILE,
    STATE_KEY_HEARTBEAT,
    STATE_KEY_LAST_CHECK,
    STATE_KEY_LAST_LOGIN,
    STATE_KEY_SEEN_IDS,
)
from app.logger import logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# In-memory state representation
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    """
    Pure data container for the persisted application state.

    This is never serialised directly; :class:`StateManager` handles
    the JSON encoding/decoding.
    """
    seen_request_ids: List[str] = field(default_factory=list)
    last_login: str = ""
    last_check: str = ""
    heartbeat: str = ""


# ---------------------------------------------------------------------------
# State manager
# ---------------------------------------------------------------------------

class StateManager:
    """
    Manages the lifecycle of ``data/state.json``.

    All mutations go through public methods which immediately persist to
    disk.  The caller never manipulates the file directly.

    Parameters
    ----------
    state_file:
        Override the default state file path (useful in tests).
    """

    def __init__(self, state_file: Optional[Path] = None) -> None:
        self._path: Path = state_file or STATE_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._state: AppState = self._load()

    # -------------------------------------------------------------------------
    # Read-only properties
    # -------------------------------------------------------------------------

    @property
    def seen_request_ids(self) -> List[str]:
        """Return a copy of the seen-IDs list (never expose the mutable internal list)."""
        return list(self._state.seen_request_ids)

    @property
    def last_login(self) -> str:
        return self._state.last_login

    @property
    def last_check(self) -> str:
        return self._state.last_check

    @property
    def heartbeat(self) -> str:
        return self._state.heartbeat

    # -------------------------------------------------------------------------
    # Public mutation API
    # -------------------------------------------------------------------------

    def has_seen(self, request_id: str) -> bool:
        """Return ``True`` if *request_id* has already been recorded."""
        return request_id in self._state.seen_request_ids

    def record_request_id(self, request_id: str) -> None:
        """
        Append *request_id* to the seen list and persist atomically.

        This operation is idempotent: calling it twice with the same ID
        has no effect on the second call.
        """
        if request_id in self._state.seen_request_ids:
            logger.debug(
                "Request ID {} is already in state — no change.", request_id
            )
            return
        self._state.seen_request_ids.append(request_id)
        self._save()
        logger.info(
            "Request ID {} recorded. Total seen: {}.",
            request_id,
            len(self._state.seen_request_ids),
        )

    def update_last_login(self) -> None:
        """Record current UTC time as the last successful login timestamp."""
        self._state.last_login = _utcnow_iso()
        self._save()
        logger.debug("last_login updated: {}", self._state.last_login)

    def update_last_check(self) -> None:
        """Record current UTC time as the last completed survey-check timestamp."""
        self._state.last_check = _utcnow_iso()
        self._save()
        logger.debug("last_check updated: {}", self._state.last_check)

    def update_heartbeat(self) -> None:
        """Record current UTC time as the last heartbeat timestamp."""
        self._state.heartbeat = _utcnow_iso()
        self._save()
        logger.debug("heartbeat updated: {}", self._state.heartbeat)

    # -------------------------------------------------------------------------
    # Internal I/O
    # -------------------------------------------------------------------------

    def _load(self) -> AppState:
        """
        Read and deserialise state from disk.

        Returns
        -------
        AppState
            Populated from file, or default-initialised if the file is
            missing or its JSON is invalid.
        """
        if not self._path.exists():
            logger.info(
                "State file not found — initialising defaults at: {}", self._path
            )
            defaults = AppState()
            self._state = defaults
            self._save()
            return defaults

        try:
            raw = self._path.read_text(encoding="utf-8")
            data: dict = json.loads(raw)

            state = AppState(
                seen_request_ids=list(data.get(STATE_KEY_SEEN_IDS, [])),
                last_login=str(data.get(STATE_KEY_LAST_LOGIN, "")),
                last_check=str(data.get(STATE_KEY_LAST_CHECK, "")),
                heartbeat=str(data.get(STATE_KEY_HEARTBEAT, "")),
            )
            logger.info(
                "State loaded — {} known request ID(s), last_check='{}'.",
                len(state.seen_request_ids),
                state.last_check or "never",
            )
            return state

        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning(
                "State file is corrupted ({}) — resetting to defaults: {}",
                exc,
                self._path,
            )
            defaults = AppState()
            self._state = defaults
            self._save()
            return defaults

    def _save(self) -> None:
        """
        Serialise and atomically persist the current in-memory state.

        Uses the ``write-to-temp → fsync → os.replace`` pattern to
        guarantee the state file is never partially written.  On Linux,
        ``os.replace`` is a POSIX ``rename(2)`` call, which is atomic
        within the same filesystem.

        Raises
        ------
        OSError
            If the filesystem write itself fails (disk full, permissions).
        """
        payload: dict = {
            STATE_KEY_SEEN_IDS: self._state.seen_request_ids,
            STATE_KEY_LAST_LOGIN: self._state.last_login,
            STATE_KEY_LAST_CHECK: self._state.last_check,
            STATE_KEY_HEARTBEAT: self._state.heartbeat,
        }

        dir_path = self._path.parent
        tmp_path: Optional[str] = None

        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(dir_path), prefix=".state_", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=4, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())   # flush OS write buffer to storage

            os.replace(tmp_path, str(self._path))  # atomic rename
            tmp_path = None  # prevent cleanup in finally block

        except OSError as exc:
            logger.error("Failed to persist state file: {}", exc)
            raise

        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
