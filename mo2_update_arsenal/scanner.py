"""Incremental Nexus metadata checks with conservative version decisions.

Network runs outside the MO2 UI thread. The API key never reaches the HTML layer.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

API = "https://api.nexusmods.com/v1"
DAY = 24 * 3600
MAX_RESPONSE_BYTES = 8_000_000
MAX_NEXUS_WORKERS = 5


class NexusError(Exception):
    pass


class MissingAuth(NexusError):
    pass


class QuotaDepleted(NexusError):
    pass


def normalized_version(value: str) -> tuple[int, ...] | None:
    """Only compare unambiguous numeric versions, never sort alpha builds blindly."""
    v = str(value or "").strip().lower().removeprefix("v")
    if not re.fullmatch(r"\d+(?:\.\d+){0,5}", v):
        return None
    return tuple(map(int, v.split(".")))


def version_verdict(installed: str, latest: str) -> str:
    a, b = normalized_version(installed), normalized_version(latest)
    if a is None or b is None:
        return "review" if latest else "unknown"
    length = max(len(a), len(b))
    a, b = a + (0,) * (length - len(a)), b + (0,) * (length - len(b))
    return "candidate" if b > a else "current" if a == b else "newer-installed"


def summary_from_metadata(item: dict, meta: dict, checked_at: float) -> dict[str, Any]:
    if not isinstance(meta, dict) or not meta:
        return {"verdict": "unknown", "checkedAt": checked_at, "reason": "No Nexus metadata"}
    if meta.get("status") not in (None, "published", "Published"):
        return {"verdict": "review", "checkedAt": checked_at, "reason": "Nexus listing status: " + str(meta.get("status"))[:80], "latestVersion": str(meta.get("version") or "")}
    latest = str(meta.get("version") or "")
    result = version_verdict(item.get("installedVersion", ""), latest)
    return {"verdict": result, "checkedAt": checked_at, "latestVersion": latest, "latestFileUpdate": meta.get("updated_timestamp"), "reason": "Review exact FILE selection and installed option; project versions alone are not an install guarantee." if result == "candidate" else "Compared Nexus project version with installed metadata.", "source": "nexus-api"}


class NexusClient:
    def __init__(self, api_key: str, request_json: Callable[[str], Any] | None = None):
        self.api_key = api_key.strip()
        self._fetch = request_json
        self.remaining = None

    def get(self, path: str) -> Any:
        if self._fetch:
            return self._fetch(path)
        if not self.api_key:
            raise MissingAuth("Connect Nexus in MO2, or enter a personal API key in the MO2 plugin dialog.")
        if not path.startswith("/v1/games/"):
            raise NexusError("Unsupported API path")
        url = "https://api.nexusmods.com" + path
        req = urllib.request.Request(url, headers={"apikey": self.api_key, "Application-Name": "MO2 Update Arsenal", "User-Agent": "MO2UpdateArsenal/0.2", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=14) as resp:
                remaining = resp.headers.get("X-RL-Hourly-Remaining")
                if remaining and remaining.isdigit():
                    self.remaining = int(remaining)
                content = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise NexusError("Nexus response exceeded safe limit")
                return json.loads(content)
        except urllib.error.HTTPError as ex:
            if ex.code == 429:
                raise QuotaDepleted("Nexus rate limit reached. No further requests sent.") from ex
            if ex.code in (401, 403):
                raise MissingAuth("Nexus API key is invalid or is not authorized") from ex
            if ex.code == 404:
                return {"status": "not_found"}
            raise NexusError(f"Nexus responded HTTP {ex.code}") from ex
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as ex:
            raise NexusError(f"Nexus request failed: {ex}") from ex


def safe_game_slug(game: str) -> str:
    if not re.fullmatch(r"[a-z0-9_-]{2,64}", game or ""):
        raise ValueError("Unsupported Nexus game domain")
    return game


class Cache:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self._scan_epochs: dict[str, int] = {}
        self.data = {"schema": 1, "entries": {}, "activity": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                self.data = raw
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def get(self, game: str, mod_id: int) -> dict | None:
        with self.lock:
            obj = self.data["entries"].get(f"{game}/{mod_id}")
            return dict(obj) if isinstance(obj, dict) else None

    def set(self, game: str, mod_id: int, value: dict) -> None:
        with self.lock:
            self.data["entries"][f"{game}/{mod_id}"] = dict(value)

    def begin_scan(self, game: str, cancel_event: threading.Event) -> int | None:
        """Only the newest scan for a game may replace its shared metadata."""
        with self.lock:
            if cancel_event.is_set():
                return None
            epoch = self._scan_epochs.get(game, 0) + 1
            self._scan_epochs[game] = epoch
            return epoch

    def scan_is_current(self, game: str, epoch: int | None, cancel_event: threading.Event) -> bool:
        with self.lock:
            return epoch is not None and not cancel_event.is_set() and self._scan_epochs.get(game) == epoch

    def set_for_scan(self, game: str, mod_id: int, value: dict, *, epoch: int | None,
                     cancel_event: threading.Event) -> bool:
        # Ownership and cancellation must be checked inside the write lock.
        # A caller's earlier check cannot protect a delayed cache write.
        with self.lock:
            if not self.scan_is_current(game, epoch, cancel_event):
                return False
            self.data["entries"][f"{game}/{mod_id}"] = dict(value)
            return True

    def persist_for_scan(self, game: str, *, epoch: int | None,
                         cancel_event: threading.Event) -> bool:
        with self.lock:
            if not self.scan_is_current(game, epoch, cancel_event):
                return False
            self.persist()
            return True

    def persist(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)


class UpdateScanner:
    def __init__(self, cache: Cache, client: NexusClient):
        self.cache = cache
        self.client = client

    def scan(self, snapshot: dict, mode: str = "quick", progress: Callable[[dict], None] | None = None,
             *, cancel_event: threading.Event | None = None) -> dict:
        cancel_event = cancel_event if cancel_event is not None else threading.Event()
        game = safe_game_slug(snapshot.get("game", "skyrimspecialedition"))
        if mode not in ("quick", "deep"):
            raise ValueError("Mode must be quick or deep")
        cache_epoch = self.cache.begin_scan(game, cancel_event)

        def cancelled() -> bool:
            return not self.cache.scan_is_current(game, cache_epoch, cancel_event)

        grouped: dict[int, list[dict]] = {}
        for item in snapshot.get("items", []):
            mod_id = int(item.get("nexusId") or 0)
            if mod_id > 0:
                grouped.setdefault(mod_id, []).append(item)
        ids = sorted(grouped)
        now = time.time()
        output: dict[str, dict] = {}
        item_records: dict[str, dict] = {}
        errors = []
        candidate_ids: set[int]
        if mode == "quick" and not cancelled():
            data = self.client.get(f"/v1/games/{game}/mods/updated.json?period=1m")
            if not isinstance(data, list):
                raise NexusError("Nexus updated feed is not an array")
            candidate_ids = set()
            for row in data:
                if isinstance(row, dict):
                    try:
                        n = int(row.get("mod_id") or row.get("modId") or 0)
                    except (TypeError, ValueError):
                        continue
                    if n > 0:
                        candidate_ids.add(n)
            candidate_ids.intersection_update(ids)
        else:
            candidate_ids = set(ids)
        uncached: list[int] = []
        for mod_id in ids:
            cached = self.cache.get(game, mod_id)
            # Deep = fresh server truth for every Nexus project, especially
            # after installs; Quick = cheap cached/activity-driven check.
            should = (mode == "deep" or (mod_id in candidate_ids and mode == "quick"))
            if not cached and mode == "quick" and mod_id not in candidate_ids:
                for item in grouped[mod_id]:
                    info={"verdict": "unverified", "reason": "Not in last month's Nexus activity. Run Full audit to establish a baseline.", "source": "nexus-activity"}
                    item_records[str(item.get("id", ""))]=info
                    output[str(mod_id)]=info
            elif cached and not should:
                for item in grouped[mod_id]:
                    info=summary_from_metadata(item, cached.get("meta", {}), float(cached["checkedAt"]))
                    item_records[str(item.get("id", ""))]=info
                    output[str(mod_id)]=info
            else:
                uncached.append(mod_id)
        total = len(uncached)
        if progress:
            progress({"checked": 0, "total": total, "phase": "querying-nexus"})
        # Bound submitted work as well as active connections. A large profile
        # must not allocate thousands of Futures or keep issuing old-profile
        # requests after a switch, quota failure, or authentication failure.
        halt = threading.Event()
        query_lock = threading.Lock()
        queried = 0

        def stopped() -> bool:
            return halt.is_set() or cancelled()

        def fetch_one(mod_id: int) -> dict | None:
            nonlocal queried
            with query_lock:
                if stopped():
                    return None
                queried += 1
            try:
                metadata = self.client.get(f"/v1/games/{game}/mods/{mod_id}.json")
            except (QuotaDepleted, MissingAuth):
                halt.set()
                raise
            if not isinstance(metadata, dict):
                raise NexusError(f"Unexpected metadata for {mod_id}")
            return metadata

        def record(mid: int, info: dict) -> None:
            output[str(mid)] = info
            for item in grouped[mid]:
                item_records[str(item.get("id", ""))] = info

        checked = 0
        cursor = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_NEXUS_WORKERS) as pool:
            pending = {}
            while pending or (cursor < total and not stopped()):
                while len(pending) < MAX_NEXUS_WORKERS and cursor < total and not stopped():
                    mid = uncached[cursor]
                    cursor += 1
                    pending[pool.submit(fetch_one, mid)] = mid
                if not pending:
                    break
                finished, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in finished:
                    mid = pending.pop(future)
                    try:
                        meta = future.result()
                        if meta is None:
                            continue  # Submitted before cancellation, never queried.
                        checked += 1
                        if not self.cache.set_for_scan(game, mid, {"checkedAt": now, "meta": meta},
                                                       epoch=cache_epoch, cancel_event=cancel_event):
                            continue  # Superseded work must not overwrite a newer cache.
                        for item in grouped[mid]:
                            info = summary_from_metadata(item, meta, now)
                            item_records[str(item.get("id", ""))] = info
                            output[str(mid)] = info
                    except (QuotaDepleted, MissingAuth) as ex:
                        checked += 1
                        halt.set()
                        if str(ex) not in errors:
                            errors.append(str(ex))
                        record(mid, {"verdict": "unknown", "source": "nexus-api",
                                     "reason": "Nexus quota or authentication failed; verification incomplete"})
                    except (NexusError, ValueError) as ex:
                        checked += 1
                        errors.append(f"{mid}: {ex}")
                        record(mid, {"verdict": "unknown", "reason": str(ex)})
                    if progress and not cancelled():
                        progress({"checked": checked, "total": total, "phase": "querying-nexus"})
        for mid in uncached:
            if str(mid) not in output:
                record(mid, {"verdict": "unknown", "source": "nexus-api",
                             "reason": "Nexus audit was interrupted; latest version could not be verified"})
        was_cancelled = not self.cache.persist_for_scan(game, epoch=cache_epoch, cancel_event=cancel_event)
        if was_cancelled:
            errors.append("Nexus audit was cancelled; verification incomplete")
        return {"records": output, "itemRecords": item_records, "uniqueNexusMods": len(ids),
                "queried": queried, "cachedOrDeferred": len(ids) - queried, "cancelled": was_cancelled,
                "mode": mode, "errors": errors, "checkedAt": datetime.now(timezone.utc).isoformat(),
                "quotaRemaining": self.client.remaining}



def local_mo2_candidates(snapshot: dict) -> dict:
    output = {}
    item_records = {}
    for item in snapshot.get("items", []):
        mid = int(item.get("nexusId") or 0)
        latest = item.get("latestKnownVersion", "")
        if mid <= 0:
            continue
        verdict = version_verdict(item.get("installedVersion", ""), latest) if latest else "unverified"
        if verdict in ("current", "newer-installed"):
            verdict = "unverified"  # Cached equality cannot prove current server truth.
        info={"verdict": verdict, "latestVersion": latest, "source": "mo2-cached-metadata", "reason": "Cached MO2 metadata only. Connect Nexus for a verified current check."}
        output[str(mid)] = info
        item_records[str(item.get("id", ""))]=info
    return {"records": output, "itemRecords": item_records, "mode": "local", "queried": 0, "errors": [], "uniqueNexusMods": len(output), "checkedAt": datetime.now(timezone.utc).isoformat()}
