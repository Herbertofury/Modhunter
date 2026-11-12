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

    def scan(self, snapshot: dict, mode: str = "quick", progress: Callable[[dict], None] | None = None) -> dict:
        game = safe_game_slug(snapshot.get("game", "skyrimspecialedition"))
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
        if mode not in ("quick", "deep"):
            raise ValueError("Mode must be quick or deep")
        candidate_ids: set[int]
        if mode == "quick":
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
            fresh = cached and now - float(cached.get("checkedAt", 0)) < DAY
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
        # Bounded parallelism; work halts on quota or auth errors.
        halt = threading.Event()
        def fetch_one(mod_id: int) -> tuple[int, dict]:
            if halt.is_set():
                return mod_id, {"status": "skipped"}
            metadata = self.client.get(f"/v1/games/{game}/mods/{mod_id}.json")
            if not isinstance(metadata, dict):
                raise NexusError(f"Unexpected metadata for {mod_id}")
            return mod_id, metadata
        checked = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_one, mid): mid for mid in uncached}
            for future in concurrent.futures.as_completed(futures):
                mid = futures[future]
                checked += 1
                try:
                    _, meta = future.result()
                    if meta.get("status") == "skipped":
                        unknown = {"verdict": "unknown", "source": "nexus-api",
                                   "reason": "Nexus audit was interrupted; latest version could not be verified"}
                        output[str(mid)] = unknown
                        for item in grouped[mid]:
                            item_records[str(item.get("id", ""))] = unknown
                        continue
                    self.cache.set(game, mid, {"checkedAt": now, "meta": meta})
                    for item in grouped[mid]:
                        info=summary_from_metadata(item, meta, now)
                        item_records[str(item.get("id", ""))]=info
                        output[str(mid)]=info
                except (QuotaDepleted, MissingAuth) as ex:
                    halt.set()
                    errors.append(str(ex))
                    unknown = {"verdict": "unknown", "source": "nexus-api",
                               "reason": "Nexus quota or authentication failed; verification incomplete"}
                    output[str(mid)] = unknown
                    for item in grouped[mid]:
                        item_records[str(item.get("id", ""))] = unknown
                except (NexusError, ValueError) as ex:
                    errors.append(f"{mid}: {ex}")
                    output[str(mid)] = {"verdict": "unknown", "reason": str(ex)}
                    for item in grouped[mid]:item_records[str(item.get("id", ""))]=output[str(mid)]
                if progress:
                    progress({"checked": checked, "total": total, "phase": "querying-nexus"})
        self.cache.persist()
        return {"records": output, "itemRecords": item_records, "uniqueNexusMods": len(ids), "queried": checked, "cachedOrDeferred": len(ids) - checked, "mode": mode, "errors": errors, "checkedAt": datetime.now(timezone.utc).isoformat(), "quotaRemaining": self.client.remaining}


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
