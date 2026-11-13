"""Durable, profile-scoped MO2 updater operation receipts.

This is an append-only audit trail, NOT a resumable install queue. Reopening
MO2 never reuses a prior session's download IDs or triggers an installation.
Only allowlisted operational metadata is persisted; no API keys, URLs, download
paths, or local backup paths can enter the journal.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ALLOWED_STATES = frozenset((
    "downloading", "paused", "complete", "installing", "installed",
    "failed", "removed", "attention",
))
_REVIEW_ON_RESTART = frozenset(("downloading", "paused", "complete", "installing", "attention"))
_STRINGS = {
    "profileId": 200, "profileName": 150, "game": 80, "gameRuntime": 80,
    "itemId": 180, "modName": 220, "provider": 32, "fileName": 240,
    "installedVersion": 100, "targetVersion": 100, "fileVersion": 100,
}
_NUMBERS = ("downloadId", "modId", "fileId", "assetId")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(ticket: dict, download_id: int, state: str) -> dict:
    """Allowlist the fields before touching disk; strip secrets and paths."""
    if isinstance(download_id, bool) or not isinstance(download_id, int) or download_id <= 0:
        raise ValueError("Invalid native MO2 download ID")
    if state not in _ALLOWED_STATES:
        raise ValueError("Invalid MO2 operation state")
    out: dict[str, Any] = {"downloadId": download_id, "status": state}
    for key, max_length in _STRINGS.items():
        if isinstance(ticket.get(key), str):
            value = ticket[key]
            # A malformed archive name may contain a local absolute path.
            if key == "fileName":
                value = value.replace("\\", "/").rsplit("/", 1)[-1]
            out[key] = value[:max_length]
    if isinstance(ticket.get("lastError"), str) and ticket["lastError"]:
        # Never persist raw native exception text: it may contain user paths,
        # authenticated URLs or provider credentials.
        out["lastError"] = ("MO2 installer canceled; review before retrying"
                            if "cancel" in ticket["lastError"].lower()
                            else "MO2 reported an issue; inspect its native Downloads view")
    for key in _NUMBERS:
        value = download_id if key == "downloadId" else ticket.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            out[key] = value
    out["reviewed"] = ticket.get("reviewed") is True
    out["verifiedUpdate"] = ticket.get("verifiedUpdate") is True
    out["hasProfileBackup"] = bool(ticket.get("backupPath"))
    return out


class OperationJournal:
    """Write-on-transition JSONL ledger with safe, read-only restart recovery."""

    def __init__(self, path: Path, session_id: str | None = None):
        self.path = Path(path)
        self.session_id = session_id or secrets.token_hex(12)
        self.lock = threading.RLock()
        self.records: dict[str, dict] = {}
        self.error = ""
        self._pending_events: list[dict] = []
        self._read_existing()

    def _read_existing(self) -> None:
        seen_events: set[str] = set()
        try:
            with self.path.open("r", encoding="utf-8") as reader:
                for line in reader:
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        key = event.get("key")
                        payload = event.get("ticket")
                        if (not isinstance(key, str) or not isinstance(payload, dict)
                                or not isinstance(event.get("at"), str)
                                or not isinstance(event.get("sessionId"), str)):
                            continue
                        status = payload.get("status")
                        download_id = payload.get("downloadId")
                        validated = _clean(payload, download_id, status)
                        if not validated.get("profileId"):
                            continue
                        event_id = event.get("eventId")
                        if isinstance(event_id, str) and event_id in seen_events:
                            continue
                        validated["hasProfileBackup"] = payload.get("hasProfileBackup") is True
                        self._apply(key, event["sessionId"], event["at"], validated)
                        if isinstance(event_id, str):
                            seen_events.add(event_id)
                    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                        # A torn final line must not erase previously valid receipts.
                        continue
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.error = "Could not read updater history: " + type(exc).__name__

    def _apply(self, key: str, session_id: str, at: str, ticket: dict) -> dict:
        prior = self.records.get(key)
        if prior is None:
            prior = {"key": key, "sessionId": session_id, "firstAt": at, "timeline": []}
            self.records[key] = prior
        prior.update(ticket)
        prior["lastAt"] = at
        prior["timeline"].append({
            "at": at, "status": ticket["status"],
            "note": ticket.get("lastError", "") if ticket["status"] == "complete" else "",
        })
        return prior

    def record(self, ticket: dict, download_id: int, state: str | None = None) -> bool:
        """Record every actual state transition; failure never changes MO2 state."""
        try:
            sanitized = _clean(ticket, download_id, state or str(ticket.get("status") or ""))
            if not sanitized.get("profileId"):
                raise ValueError("Missing MO2 profile ID")
            operation = str(ticket.get("operationId") or download_id)[:100]
            key = self.session_id + ":" + operation
            with self.lock:
                previous = self.records.get(key)
                unchanged = previous and all(previous.get(k) == v for k, v in sanitized.items())
                if not unchanged:
                    at = _stamp()
                    event = {"schemaVersion": 1, "eventId": secrets.token_hex(16),
                             "key": key, "sessionId": self.session_id,
                             "at": at, "ticket": sanitized}
                    self._pending_events.append(event)
                    self._apply(key, self.session_id, at, sanitized)
                if not self._pending_events:
                    return not bool(self.error)
                # Retry only sanitized history writes, never native MO2 commands.
                # Stable event IDs make an uncertain flush/fsync retry replay-safe.
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a+b") as stream:
                        stream.seek(0, os.SEEK_END)
                        if stream.tell():
                            stream.seek(-1, os.SEEK_END)
                            if stream.read(1) != b"\n":
                                stream.write(b"\n")  # Isolate a torn previous line.
                        for event in self._pending_events:
                            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                            stream.write((payload + "\n").encode("utf-8"))
                        stream.flush()
                        os.fsync(stream.fileno())
                    self._pending_events.clear()
                    self.error = ""
                    durable = True
                except OSError as exc:
                    self.error = "Could not save updater history: " + type(exc).__name__
                    durable = False
                return durable
        except (ValueError, TypeError, KeyError):
            return False

    def profile(self, profile_id: str) -> dict:
        """Return ALL history for this profile, never as actionable MO2 tickets."""
        with self.lock:
            result = []
            for raw in self.records.values():
                if raw.get("profileId") != profile_id:
                    continue
                item = deepcopy(raw)
                item["sessionActive"] = raw["sessionId"] == self.session_id
                item["needsReview"] = (not item["sessionActive"] and item["status"] in _REVIEW_ON_RESTART)
                item["actionable"] = False
                result.append(item)
            result.sort(key=lambda x: (x["lastAt"], x["key"]), reverse=True)
            return {"ok": True, "profileId": profile_id, "records": result,
                    "count": len(result), "durable": not bool(self.error),
                    "warning": self.error}

