"""Incremental Nexus metadata checks with conservative version decisions.

Network runs outside the MO2 UI thread. The API key never reaches the HTML layer.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
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

from .version_evidence import compare_versions, normalized_version
from .file_advisor import NexusFileIndex
from .native_nexus import normalize_native_files

API = "https://api.nexusmods.com/v1"
DAY = 24 * 3600
MAX_RESPONSE_BYTES = 8_000_000
MAX_NEXUS_WORKERS = 5
STARTUP_CACHE_SECONDS = 4 * 3600
LOG = logging.getLogger(__name__)


class NexusError(Exception):
    pass


class MissingAuth(NexusError):
    pass


class QuotaDepleted(NexusError):
    pass


def version_verdict(installed: str, latest: str) -> str:
    return compare_versions(installed, latest)["verdict"]


def respect_ignored_version(item, result):
    ignored = item.get("ignoredVersion")
    if (result.get("verdict") == "candidate" and ignored
            and compare_versions(ignored, result.get("latestVersion"))["verdict"] == "current"):
        result["availableVerdict"] = "candidate"
        result["verdict"] = "ignored"
        result["reason"] += " MO2 records this specific release version as ignored."


def summary_from_metadata(item: dict, meta: dict, checked_at: float, files: NexusFileIndex | None = None) -> dict[str, Any]:
    if not isinstance(meta, dict) or not meta:
        return {"verdict": "unknown", "checkedAt": checked_at, "reason": "No Nexus metadata"}
    if meta.get("available") is False or meta.get("status") not in (None, "published", "Published"):
        return {"verdict": "review", "checkedAt": checked_at, "reason": "Nexus listing status: " + str(meta.get("status"))[:80], "latestVersion": "", "projectVersion": str(meta.get("version") or ""), "source": "nexus-api"}
    summary = {"checkedAt": checked_at, "expiresAt": checked_at + STARTUP_CACHE_SECONDS,
               "freshness": "fresh", "projectVersion": str(meta.get("version") or ""),
               "latestFileUpdate": meta.get("updated_timestamp"), "source": "nexus-api"}
    if files is not None:
        summary.update(files.resolve(item))
    else:
        summary.update(verdict="review", evidenceScope="project-only", latestVersion="",
                       reason="Project metadata alone cannot identify the installed Nexus file.")
    if summary["verdict"] == "review" and item.get("latestKnownVersion"):
        summary["mo2Comparison"] = compare_versions(item.get("installedVersion"), item["latestKnownVersion"])
        if summary["mo2Comparison"]["verdict"] == "current":
            summary["reason"] += " MO2's last known version matches your installation."
    respect_ignored_version(item, summary)
    from .source_adapters import preview_image_url
    image = preview_image_url(meta.get("picture_url"), "nexus")
    if image:
        summary.update(thumbnailUrl=image, thumbnailKind="image")
    if isinstance(meta.get("author"), str) and meta["author"].strip():
        summary["author"] = {"name": meta["author"].strip()[:180], "role": "mod author"}
    return summary


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
        self._journal = path.with_suffix(path.suffix + ".projects.jsonl")
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
        self._checkpoint = self.data.get("checkpoint", 0)
        if type(self._checkpoint) is not int or self._checkpoint < 0:
            self._checkpoint = 0
        self._dirty = False
        try:
            self._replay_projects()
        except OSError as exc:
            LOG.warning(f"Could not read the Nexus scan checkpoint ({type(exc).__name__})")

    def _replay_projects(self) -> None:
        # A completed snapshot already includes all journal records through
        # its checkpoint, even if the process exited before clearing the log.
        try:
            stream = self._journal.open("rb")
        except FileNotFoundError:
            return
        with stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    sequence, game, mod_id, value = json.loads(line)
                    if (type(sequence) is not int or sequence <= self._checkpoint or type(game) is not str
                            or not re.fullmatch(r"[a-z0-9_-]{2,64}", game) or type(mod_id) is not int
                            or mod_id <= 0 or not isinstance(value, dict)
                            or not metadata_identity_matches(value.get("meta"), game, mod_id)):
                        continue
                except (ValueError, TypeError, UnicodeError):
                    continue  # An interrupted final write is safe to query again.
                self.data["entries"][f"{game}/{mod_id}"] = value
                self._checkpoint = sequence
                self._dirty = True

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
            self._dirty = True
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
            sequence = self._checkpoint + 1
            payload = json.dumps([sequence, game, mod_id, value], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            # Reserve the number before I/O: a failed flush can leave a
            # complete record that a restarted reader can legitimately see.
            self._checkpoint = sequence
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self._journal.open("ab") as stream:
                    # Frame both ends: a new accepted record can follow a torn tail.
                    stream.write(b"\n" + payload + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise OSError(f"Could not save the Nexus scan checkpoint for {game}/{mod_id}; "
                              "earlier completed projects remain available on restart") from exc
            self.set(game, mod_id, value)
            return True

    def persist_for_scan(self, game: str, *, epoch: int | None,
                         cancel_event: threading.Event) -> bool:
        with self.lock:
            if not self.scan_is_current(game, epoch, cancel_event):
                return False
            if self._dirty:
                self.persist()
            return True

    def persist(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            self.data["checkpoint"] = self._checkpoint
            with tmp.open("w", encoding="utf-8") as stream:
                json.dump(self.data, stream, separators=(",", ":"), ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            # A surviving journal is harmless: replay skips its old sequence numbers.
            self._dirty = False
            try:
                self._journal.unlink(missing_ok=True)
            except OSError as exc:
                LOG.warning(f"Could not remove the compacted Nexus scan checkpoint ({type(exc).__name__})")


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


def public_project_metadata(game, mod_id, project, files):
    """Convert a complete, project-scoped Nexus GraphQL file list into reusable evidence."""
    from .file_advisor import nexus_file_id
    def owned(row):
        return (isinstance(row, dict) and nexus_file_id(row.get("modId")) == mod_id
                and isinstance(row.get("game"), dict) and row["game"].get("domainName") == game)
    if not owned(project) or not isinstance(files, list) or not all(owned(row) for row in files):
        raise NexusError("Public Nexus data did not match the requested game and mod")
    compact = []
    for row in files:
        file_id = nexus_file_id(row.get("fileId"))
        if not file_id:
            raise NexusError("Public Nexus returned an incomplete file identity")
        compact.append({"fileId": file_id, "fileName": str(row.get("uri") or ""), "name": str(row.get("name") or ""),
                        "version": str(row.get("version") or ""), "category": nexus_file_id(row.get("categoryId")),
                        "groupId": nexus_file_id(row.get("groupId"))})
    return {"mod_id": mod_id, "domain_name": game, "status": str(project.get("status") or "").lower(),
            "version": str(project.get("version") or ""), "picture_url": project.get("pictureUrl"),
            "author": project.get("author"), "fileCatalog": {"providerGroups": True, "files": compact}}


def public_nexus_query(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request("https://api.nexusmods.com/v2/graphql", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "Modhunter"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise NexusError("Public Nexus response exceeded safe limit")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
            raise NexusError("Public Nexus could not complete the requested lookup")
        return data["data"]
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise QuotaDepleted("Nexus rate limit reached") from exc
        if exc.code in (401, 403):
            raise MissingAuth("Nexus requires authenticated access to this data") from exc
        raise NexusError(f"Public Nexus returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise NexusError("Public Nexus lookup failed: " + str(exc)) from exc


class PublicNexusClient:
    """Read public project/file metadata. MO2 continues to own all downloads and account access."""
    remaining = None

    def __init__(self, game, request=public_nexus_query):
        self.game = safe_game_slug(game)
        self.request = request
        result = request("query($domain:String!){game(domainName:$domain){id domainName}}", {"domain": game}).get("game")
        if not isinstance(result, dict) or result.get("domainName") != game or not result.get("id"):
            raise NexusError("Public Nexus game identity is unavailable")
        self.game_id = result["id"]

    def get(self, path):
        match = re.fullmatch(rf"/v1/games/{self.game}/mods/([0-9]+)\.json", path)
        if not match:
            raise NexusError("Unsupported public Nexus metadata request")
        mid = int(match[1])
        query = ("query($g:ID!,$m:ID!){mod(gameId:$g,modId:$m){modId game{domainName} name version status pictureUrl author} "
                 "modFiles(gameId:$g,modId:$m){fileId modId game{domainName} name version categoryId groupId uri}}")
        result = self.request(query, {"g": self.game_id, "m": mid})
        return public_project_metadata(self.game, mid, result.get("mod"), result.get("modFiles"))


class NativeScanCatalog:
    """Read version-only public fields before asking MO2 to render a full file list."""

    def __init__(self, game, client_factory=PublicNexusClient):
        self.game = game
        self.client_factory = client_factory
        self.client = None
        self.unavailable = False
        self.lock = threading.Lock()

    def for_installed_files(self, mod_id, metadata, items):
        # MO2 owns authenticated metadata, access decisions and downloads. Its
        # requestFiles also translates every remote description into BBCode HTML
        # on the GUI thread, even when the caller only consumes raw version data.
        if metadata.get("mod_id") != mod_id or metadata.get("domain_name") != self.game:
            return None
        try:
            with self.lock:
                if self.unavailable:
                    return None
                if self.client is None:
                    try:
                        self.client = self.client_factory(self.game)
                    except QuotaDepleted:
                        raise
                    except NexusError:
                        self.unavailable = True
                        return None
            project = self.client.get(f"/v1/games/{self.game}/mods/{mod_id}.json")
        except QuotaDepleted:
            raise
        except NexusError:
            return None
        if (project["status"] != "published" or not metadata.get("version")
                or project["version"] != metadata["version"]):
            return None
        catalog = project["fileCatalog"]
        return catalog if self.covers(catalog, items) else None

    @staticmethod
    def covers(catalog, items):
        index = NexusFileIndex(catalog)
        return all(index.resolve(item).get("evidenceScope") == "exact-file"
                   for item in items if item.get("installedFiles") or item.get("installationArchive"))


class UpdateScanner:
    def __init__(self, cache: Cache, client: NexusClient, *, native_catalog=None):
        self.cache = cache
        self.client = client
        self.native_catalog = native_catalog

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
        item_records: dict[str, dict] = {}
        for item in snapshot.get("items", []):
            if item.get("installedFileEvidenceIncomplete"):
                item_records[str(item.get("id", ""))] = incomplete_file_record()
                continue
            mod_id = nexus_project_id(item, game)
            if mod_id > 0:
                grouped.setdefault(mod_id, []).append(item)
        ids = sorted(grouped)
        now = time.time()
        output: dict[str, dict] = {}
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
        stale_on_startup: list[int] = []
        identifiable = {mid for mid, rows in grouped.items() if any(
            row.get("installedFiles") or row.get("installationArchive") for row in rows)}

        def summarize(mid, metadata, checked_at):
            catalog = metadata.get("fileCatalog")
            index = NexusFileIndex(catalog) if isinstance(catalog, dict) else None
            for item in grouped[mid]:
                info = summary_from_metadata(item, metadata, checked_at, index)
                item_records[str(item.get("id", ""))] = info
                output[str(mid)] = info

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
                     and (mod_id not in identifiable or isinstance(metadata.get("fileCatalog"), dict))
                     and 0 <= now - checked_at < STARTUP_CACHE_SECONDS)
            if fresh and self.native_catalog is not None and metadata.get("fileCatalogRoute") == "public-file-groups":
                fresh = self.native_catalog.covers(metadata["fileCatalog"], grouped[mod_id])
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
                summarize(mod_id, cached.get("meta", {}), checked_at)
            elif cached and mode == "startup":
                # Across repeated interruptions or quota windows, previously
                # unvisited projects must also receive their first check.
                stale_on_startup.append(mod_id)
            else:
                uncached.append(mod_id)
        uncached.extend(stale_on_startup)
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
            if not isinstance(self.client, PublicNexusClient) and "fileCatalog" in metadata:
                metadata = {key: value for key, value in metadata.items() if key != "fileCatalog"}
            if (mod_id in identifiable and "fileCatalog" not in metadata and metadata.get("available") is not False
                    and metadata.get("status") in (None, "published", "Published") and not stopped()):
                if self.native_catalog is not None:
                    catalog = self.native_catalog.for_installed_files(mod_id, metadata, grouped[mod_id])
                    if catalog is not None:
                        return {**metadata, "fileCatalog": catalog, "fileCatalogRoute": "public-file-groups"}
                    if stopped():
                        return None
                try:
                    payload = self.client.get(f"/v1/games/{game}/mods/{mod_id}/files.json")
                except (QuotaDepleted, MissingAuth):
                    halt.set()
                    raise
                if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
                    raise NexusError(f"Nexus file list unavailable for {mod_id}")
                files = normalize_native_files(payload["files"])
                compact = [{k: row[k] for k in ("fileId", "fileName", "name", "version", "category")} for row in files]
                history = payload.get("file_updates")
                if isinstance(history, list):
                    history = [{k: edge.get(k) for k in ("old_file_id", "new_file_id", "old_file_name", "new_file_name")}
                               if isinstance(edge, dict) else {} for edge in history]
                metadata = {**metadata, "fileCatalog": {"files": compact, "file_updates": history}}
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
                        checked_at = time.time()
                        if not self.cache.set_for_scan(game, mid, {"checkedAt": checked_at, "meta": meta},
                                                       epoch=cache_epoch, cancel_event=cancel_event):
                            continue  # Superseded work must not overwrite a newer cache.
                        summarize(mid, meta, checked_at)
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
        for mid, rows in grouped.items():
            if len(rows) > 1:
                output[str(mid)] = {"verdict": "review", "reason": "Multiple installed files share this project. See each installed row."}
        was_cancelled = not self.cache.persist_for_scan(game, epoch=cache_epoch, cancel_event=cancel_event)
        if was_cancelled:
            errors.append("Nexus audit was cancelled; verification incomplete")
        return {"records": output, "itemRecords": item_records, "uniqueNexusMods": len(ids),
                "queried": queried, "fromCache": len(ids) - total,
                "cachedOrDeferred": len(ids) - queried, "cancelled": was_cancelled,
                "mode": mode, "errors": errors, "checkedAt": datetime.now(timezone.utc).isoformat(),
                "quotaRemaining": self.client.remaining}



def incomplete_file_record():
    return {"verdict": "review", "source": "mo2-file-receipt", "evidenceScope": "file-identity",
            "reason": "The installed MO2 file identities are incomplete. Review the installation archive and merged files."}


def local_mo2_candidates(snapshot: dict) -> dict:
    output = {}
    item_records = {}
    for item in snapshot.get("items", []):
        if item.get("installedFileEvidenceIncomplete"):
            item_records[str(item.get("id", ""))] = incomplete_file_record()
            continue
        mid = nexus_project_id(item, snapshot.get("game", "skyrimspecialedition"))
        latest = item.get("latestKnownVersion", "")
        if mid <= 0:
            continue
        verdict = version_verdict(item.get("installedVersion", ""), latest) if latest else "unverified"
        if verdict in ("current", "newer-installed"):
            verdict = "unverified"  # Cached equality cannot prove current server truth.
        info = {"verdict": "review" if verdict == "candidate" else verdict, "projectVersion": latest,
                "latestVersion": "", "source": "mo2-cached-metadata", "evidenceScope": "project-only",
                "reason": "Cached MO2 metadata only. Check the exact installed Nexus file to verify the current release."}
        if latest and item.get("mo2FileStatus") in (1, 2, 3, 5):
            info["mo2Verdict"] = version_verdict(item.get("installedVersion", ""), latest)
        respect_ignored_version(item, info)
        if str(mid) in output:
            output[str(mid)] = {"verdict": "review", "reason": "Several installed rows share this project."}
        else:
            output[str(mid)] = info
        item_records[str(item.get("id", ""))]=info
    return {"records": output, "itemRecords": item_records, "mode": "local", "queried": 0, "errors": [], "uniqueNexusMods": len(output), "checkedAt": datetime.now(timezone.utc).isoformat()}
