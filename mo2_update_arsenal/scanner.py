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

from .version_evidence import compare_versions

API = "https://api.nexusmods.com/v1"
DAY = 24 * 3600
MAX_RESPONSE_BYTES = 8_000_000
MAX_NEXUS_WORKERS = 5
STARTUP_CACHE_SECONDS = 4 * 3600


class NexusError(Exception):
    pass


class MissingAuth(NexusError):
    pass


class QuotaDepleted(NexusError):
    pass


def normalized_version(value: str) -> tuple[int, ...] | None:
    """Only compare unambiguous numeric versions, never sort alpha builds blindly."""
    v = str(value or "").strip().lower().removeprefix("v")
    if len(v) > 96 or not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,10}){0,5}", v):
        return None
    return tuple(map(int, v.split(".")))


def version_verdict(installed: str, latest: str) -> str:
    return compare_versions(installed, latest)["verdict"]


def summary_from_metadata(item: dict, meta: dict, checked_at: float) -> dict[str, Any]:
    if not isinstance(meta, dict) or not meta:
        return {"verdict": "unknown", "checkedAt": checked_at, "reason": "No Nexus metadata"}
    if meta.get("status") not in (None, "published", "Published"):
        return {"verdict": "review", "checkedAt": checked_at, "reason": "Nexus listing status: " + str(meta.get("status"))[:80], "latestVersion": str(meta.get("version") or "")}
    latest = str(meta.get("version") or "")
    comparison = compare_versions(item.get("installedVersion", ""), latest)
    result = comparison["verdict"]
    return {"versionComparison": comparison, "verdict": result, "checkedAt": checked_at,
            "expiresAt": checked_at + STARTUP_CACHE_SECONDS, "freshness": "fresh", "latestVersion": latest, "latestFileUpdate": meta.get("updated_timestamp"), "reason": "Review exact FILE selection and installed option; project versions alone are not an install guarantee." if result == "candidate" else comparison["reason"], "source": "nexus-api"}


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
        self._revision = 0
        self._entry_revisions: dict[str, int] = {}
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

    def revision(self) -> int:
        """Cheap invalidation for immutable dashboard responses."""
        with self.lock:
            return self._revision

    def get_with_revision(self, game: str, mod_id: int) -> tuple[dict | None, int]:
        with self.lock:
            key = f"{game}/{mod_id}"
            obj = self.data["entries"].get(key)
            return (dict(obj) if isinstance(obj, dict) else None), self._entry_revisions.get(key, 0)

    def set(self, game: str, mod_id: int, value: dict) -> None:
        with self.lock:
            self.data["entries"][f"{game}/{mod_id}"] = dict(value)
            self._revision += 1
            self._entry_revisions[f"{game}/{mod_id}"] = self._revision

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
            self._revision += 1
            self._entry_revisions[f"{game}/{mod_id}"] = self._revision
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


def nexus_project_id(item: dict, game: str) -> int:
    """Native ID is not authority for an explicitly different source URL."""
    raw = item.get("nexusId")
    try:
        mid = int(raw or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    if isinstance(raw, bool) or mid <= 0 or item.get("updateEligible") is False:
        return 0
    source = item.get("source") or {}
    url = source.get("url") or ""
    if not url:
        return mid if source.get("type", "unknown") in ("", "unknown", "nexus") else 0
    try:
        parsed = urllib.parse.urlsplit(url)
        match = re.fullmatch(r"/([^/]+)/mods/([0-9]+)/?", parsed.path)
        if (parsed.scheme != "https" or parsed.hostname not in ("www.nexusmods.com", "nexusmods.com")
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not match or match[1].lower() != game.lower() or int(match[2]) != mid):
            return 0
    except (ValueError, TypeError):
        return 0
    return mid


def metadata_identity_matches(metadata: object, game: str, mod_id: int) -> bool:
    if not isinstance(metadata, dict):
        return False
    supplied_id = metadata.get("mod_id")
    return ((supplied_id is None or (type(supplied_id) is int and supplied_id == mod_id))
            and metadata.get("domain_name", game) == game)


class UpdateScanner:
    def __init__(self, cache: Cache, client: NexusClient):
        self.cache = cache
        self.client = client

    def scan(self, snapshot: dict, mode: str = "quick", progress: Callable[[dict], None] | None = None,
             *, cancel_event: threading.Event | None = None) -> dict:
        cancel_event = cancel_event if cancel_event is not None else threading.Event()
        game = safe_game_slug(snapshot.get("game", "skyrimspecialedition"))
        if mode not in ("quick", "deep", "startup"):
            raise ValueError("Mode must be quick, deep or startup")
        cache_epoch = self.cache.begin_scan(game, cancel_event)

        def cancelled() -> bool:
            return not self.cache.scan_is_current(game, cache_epoch, cancel_event)

        grouped: dict[int, list[dict]] = {}
        for item in snapshot.get("items", []):
            mod_id = nexus_project_id(item, game)
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
            try:
                checked_at = float((cached or {}).get("checkedAt", 0))
            except (TypeError, ValueError, OverflowError):
                checked_at = 0
            metadata = (cached or {}).get("meta")
            fresh = (metadata_identity_matches(metadata, game, mod_id) and bool(metadata.get("version"))
                     and metadata.get("status") in (None, "published", "Published")
                     and 0 <= now - checked_at < STARTUP_CACHE_SECONDS)
            should = (mode == "deep" or (mod_id in candidate_ids and mode == "quick")
                      or (mode == "startup" and not fresh)
                      or (cached is not None and (not metadata_identity_matches(metadata, game, mod_id)
                                                 or not 0 <= checked_at <= now)))
            if not cached and mode == "quick" and mod_id not in candidate_ids:
                for item in grouped[mod_id]:
                    info={"verdict": "unverified", "reason": "Not in last month's Nexus activity. Run Full audit to establish a baseline.", "source": "nexus-activity"}
                    item_records[str(item.get("id", ""))]=info
                    output[str(mod_id)]=info
            elif cached and not should:
                for item in grouped[mod_id]:
                    info=summary_from_metadata(item, cached.get("meta", {}), checked_at)
                    item_records[str(item.get("id", ""))]=info
                    output[str(mod_id)]=info
            else:
                uncached.append(mod_id)
        total = len(uncached)
        if progress:
            progress({"checked": 0, "total": total, "fromCache": len(ids) - total,
                      "phase": "querying-nexus", "itemRecords": dict(item_records)})
        # Bound submitted work as well as active connections. A large profile
        # must not allocate thousands of Futures or keep issuing old-profile
        # requests after a switch, quota failure, or authentication failure.
        halt = threading.Event()
        query_lock = threading.Lock()
        queried = 0
        consecutive_failures = 0
        halt_info = None

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
            if not metadata_identity_matches(metadata, game, mod_id):
                raise NexusError(f"Nexus metadata identity did not match project {mod_id}")
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
                        consecutive_failures = 0
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
                        halt_info = {"availability": "authentication-required" if isinstance(ex, MissingAuth) else "rate-limited",
                                     "reason": str(ex)}
                        record(mid, {"verdict": "unknown", "source": "nexus-api", **halt_info})
                    except (NexusError, ValueError) as ex:
                        checked += 1
                        errors.append(f"{mid}: {ex}")
                        consecutive_failures += 1
                        if mode == "startup" and consecutive_failures >= MAX_NEXUS_WORKERS:
                            halt.set()
                            halt_info = {"availability": "unavailable", "reason": "Automatic Nexus checks paused after repeated failures. Check the connection, then retry Check for updates."}
                        record(mid, {"verdict": "unknown", "source": "nexus-api",
                                     "availability": "unavailable", "reason": str(ex)})
                    if progress and not cancelled():
                        # Publish only this finished project's owned rows. Never
                        # copy the entire growing result for every completion.
                        delta = {str(item.get("id", "")): item_records[str(item.get("id", ""))]
                                 for item in grouped[mid] if str(item.get("id", "")) in item_records}
                        progress({"checked": checked, "total": total, "phase": "querying-nexus",
                                  "itemRecords": delta})
        for mid in uncached:
            if str(mid) not in output:
                record(mid, {"verdict": "unknown", "source": "nexus-api",
                             "reason": "Nexus audit was interrupted; latest version could not be verified",
                             **(halt_info or {})})
        was_cancelled = not self.cache.persist_for_scan(game, epoch=cache_epoch, cancel_event=cancel_event)
        if was_cancelled:
            errors.append("Nexus audit was cancelled; verification incomplete")
        return {"records": output, "itemRecords": item_records, "uniqueNexusMods": len(ids),
                "queried": queried, "fromCache": len(ids) - total,
                "cachedOrDeferred": len(ids) - queried, "cancelled": was_cancelled,
                "mode": mode, "errors": errors, "checkedAt": datetime.now(timezone.utc).isoformat(),
                "quotaRemaining": self.client.remaining}



def local_mo2_candidates(snapshot: dict) -> dict:
    output = {}
    item_records = {}
    for item in snapshot.get("items", []):
        mid = nexus_project_id(item, snapshot.get("game", "skyrimspecialedition"))
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
