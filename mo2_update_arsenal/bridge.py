"""Loopback-only browser-to-MO2 bridge. No remote origins, no file APIs.

The server never calls mobase itself; all privileged commands cross a Qt queued signal.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import urllib.parse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .scanner import Cache, MissingAuth, NexusClient, NexusError, UpdateScanner, local_mo2_candidates
from .gameplay import Chronicle
from html import unescape
import re
from .source_registry import SourceScanner, canonical_source_url, source_record
from .update_plan import PlanError, plan_updates, validate_batch_selection, validate_release_asset

MAX_REQUEST = 16_384
MAX_PROGRESS_REQUEST = 8_000_000
MAX_REALM_REQUEST = 800_000


class UIRequest:
    def __init__(self, cmd: str, payload: dict):
        self.cmd, self.payload = cmd, payload
        self.event = threading.Event()
        self.result: dict[str, Any] = {}

    def finish(self, data: dict) -> None:
        self.result = data
        self.event.set()


class ArsenalService:
    def __init__(self, app_html: str, cache_path: Path, ui_call: Callable[[UIRequest], None], snapshot: dict):
        self.html = app_html
        self.token = secrets.token_urlsafe(36)
        self.ui_call = ui_call
        self.cache = Cache(cache_path)
        self.chronicle = Chronicle(cache_path.with_name("chronicle.json"))
        self.chronicle.set_profile_name(snapshot.get("profileId", ""), snapshot.get("profileName", "Default"))
        self.selected_mod_id = ""
        self.api_key = ""  # Volatile: never persisted, sent to the browser, or included in backup files.
        self.native_nexus_available = False
        self._snapshot = snapshot
        self._guard = threading.RLock()
        self._queue_lock = threading.Lock()  # Serialize reviewed batches; never enqueue the same mod twice.
        self._install_lock = threading.Lock()  # MO2 supports one interactive installer at a time.
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._scan: dict[str, Any] = {"state": "idle"}
        self._file_cache: dict[int, dict] = {}
        self._downloads: dict[int, dict] = {}
        self._stopped = False
        self._generation = 1

    def snapshot(self) -> dict:
        with self._guard:
            snapshot = deepcopy(self._snapshot)
        # The existing MO2 Chronicle persists explicit user source links by
        # profile. Rehydrate them for scans without overwriting MO2 metadata.
        progress = self.chronicle.progress(snapshot.get("profileId", ""))
        rows = progress.get("rows") or {}
        for item in snapshot.get("items", []):
            row = rows.get(str(item.get("id")), {})
            candidate = canonical_source_url(row.get("url", "")) if isinstance(row, dict) else ""
            if candidate and candidate != (item.get("source") or {}).get("url"):
                item["source"] = source_record(candidate, "MO2 Chronicle source link")
                item.pop("update", None)  # Old source verdict is not transferable.
        return snapshot

    def set_snapshot(self, snapshot: dict) -> None:
        with self._guard:
            old = self._snapshot
            same_profile = old.get("profileId") == snapshot.get("profileId")
            old_items = {str(i["id"]): i for i in old.get("items", [])}
            if same_profile:
                for i in snapshot.get("items", []):
                    before = old_items.get(str(i["id"]))
                    if before and before.get("installedVersion") == i.get("installedVersion") and before.get("update"):
                        i["update"] = before["update"]
            awards = self.chronicle.observe(old, snapshot) if same_profile else []
            if not same_profile or old.get("game") != snapshot.get("game"):
                self._file_cache.clear()  # File identity belongs to a specific game/profile.
            if not same_profile:
                self._scan = {"state": "idle", "reason": "MO2 profile changed"}
                self.selected_mod_id = ""
            elif awards or any(old_items.get(str(i["id"]), {}).get("installedVersion") != i.get("installedVersion") for i in snapshot.get("items", [])):
                # A successful install/version change invalidates the old scan verdict.
                self._scan = {"state": "stale", "reason": "Installed versions changed: rescan to verify latest state"}
            self.chronicle.set_profile_name(snapshot.get("profileId", ""), snapshot.get("profileName", "Default"))
            self._snapshot = snapshot
            self._generation += 1

    def set_selected(self, mod_id: str) -> None:
        with self._guard:
            if mod_id != self.selected_mod_id:
                # Selection-only changes are read by /api/revision directly.
                # Do not invalidate the entire mod snapshot on every click.
                self.selected_mod_id = mod_id

    def focus_mod(self, mod_id: str, profile_id: str) -> dict:
        with self._guard:
            if profile_id != self._snapshot.get("profileId"):
                return {"ok": False, "error": "Profile changed; refresh before navigating"}
            m = next((i for i in self._snapshot.get("items", []) if i.get("id") == mod_id), None)
            if m is None:
                return {"ok": False, "error": "Mod is not in the current MO2 profile"}
            internal = m.get("mo2InternalName")
            if not internal:
                return {"ok": False, "error": "Mod has no native MO2 identifier"}
        # No untrusted browser string is used as a native widget target; only the
        # profile's exact mobase internal-name snapshot may cross the Qt queue.
        result = self.call_ui("focus", {"profileId": profile_id, "modId": mod_id, "name": internal}, timeout=12)
        if result.get("ok"):
            self.set_selected(mod_id)
        return result

    def siege_targets(self) -> dict:
        """Only genuine live MO2 entries with a recorded newer Nexus version.

        An unknown, failed, ambiguous, locally generated, or already-current mod
        must never be represented as a pending-update boss.
        """
        from .scanner import version_verdict
        from .lore import describe_enemy_theme
        snapshot = self.snapshot()
        seen = set()
        targets = []
        for item in snapshot.get("items", []):
            update = item.get("update") or {}
            if not str(item.get("id", "")).startswith("live-") or not int(item.get("nexusId") or 0):
                continue
            if update.get("verdict") != "candidate":
                continue
            before = str(item.get("installedVersion") or "")
            target = str(update.get("latestVersion") or "")
            if version_verdict(before, target) != "candidate":
                continue
            mid = str(item["id"])
            if mid in seen:
                continue
            seen.add(mid)
            targets.append({"id": mid, "name": str(item.get("name", mid))[:200],
                            "nexusId": int(item["nexusId"]), "installedVersion": before,
                            "latestVersion": target, "priority": item.get("priority"),
                            "source": update.get("source", "MO2 cached metadata"),
                            "checkedAt": update.get("checkedAt"), "game": snapshot.get("game", "skyrimspecialedition"),
                            "lore": describe_enemy_theme(item)})
        return {"ok": True, "profileId": snapshot.get("profileId", ""),
                "scanState": self.scan_state().get("state", "idle"),
                "targets": targets, "count": len(targets)}

    def siege_boss_defeated(self, profile_id: str, mod_id: str, run_id: str) -> dict:
        # Inspect native snapshot under the same lock as profile replacement.
        with self._guard:
            if profile_id != self._snapshot.get("profileId"):
                return {"ok": False, "error": "Profile changed before boss victory"}
            eligible_ids = {t["id"] for t in self.siege_targets()["targets"]}
            if mod_id not in eligible_ids:
                return {"ok": False, "error": "Target is not a currently outdated MO2 mod"}
            mod = next((m for m in self._snapshot["items"] if m.get("id") == mod_id), None)
            return self.chronicle.claim_mod_boss(profile_id, mod, run_id)

    def set_key(self, key: str) -> None:
        # Key is deliberately not stored in browser/localStorage/on-disk.
        with self._guard:
            self.api_key = str(key).strip()

    def call_ui(self, cmd: str, payload: dict, timeout: float = 20) -> dict:
        req = UIRequest(cmd, payload)
        self.ui_call(req)
        if not req.event.wait(timeout):
            return {"ok": False, "error": "MO2 is busy or its native dialog is open. Retry after returning to MO2."}
        return req.result

    def begin_scan(self, mode: str) -> dict:
        if mode not in ("quick", "deep", "local", "sources", "all"):
            return {"ok": False, "error": "Invalid scan mode"}
        with self._guard:
            if self._scan.get("state") == "running":
                return {"ok": False, "error": "An update scan is already running"}
            self._scan = {"state": "running", "mode": mode, "checked": 0, "total": 0, "errors": []}
            self._file_cache.clear()  # Never review a cached Nexus file list after a fresh audit.
            snapshot = self.snapshot()
            key = self.api_key
            native_nexus = self.native_nexus_available
            base_sequence = self.chronicle.overview(snapshot["profileId"])["updates"]
            version_signature = [(x.get("id"), x.get("installedVersion")) for x in snapshot.get("items", [])]
        def worker():
            try:
                def scan_nexus(which):
                    if which == "local" or (which == "quick" and native_nexus and not key):
                        return local_mo2_candidates(snapshot), "local"
                    if key:
                        client = NexusClient(key)
                    elif native_nexus:
                        import re
                        game = snapshot.get("game", "skyrimspecialedition")
                        def mo2_nexus_get(path):
                            m = re.fullmatch(r"/v1/games/[a-z0-9_-]+/mods/([0-9]+)\.json", path)
                            if m is None:
                                raise NexusError("Unsupported native Nexus request")
                            native = self.call_ui("nexus-metadata",
                                {"game": game, "modId": int(m.group(1))}, timeout=70)
                            if not native.get("ok"):
                                raise NexusError(str(native.get("error") or "MO2 Nexus lookup failed"))
                            return native["meta"]
                        client = NexusClient("mo2-native", request_json=mo2_nexus_get)
                    else:
                        raise MissingAuth("Connect to Nexus in MO2 Settings > Nexus or use a fallback API key.")
                    return UpdateScanner(self.cache, client).scan(snapshot, which, self._progress), which

                effective_mode = mode
                if mode == "sources":
                    result = SourceScanner().scan(snapshot, self._progress)
                elif mode == "all":
                    nexus, coverage = scan_nexus("deep" if key or native_nexus else "local")
                    other = SourceScanner().scan(snapshot, self._progress)
                    result = {**nexus,
                        "itemRecords": {**nexus.get("itemRecords", {}), **other.get("itemRecords", {})},
                        "errors": list(nexus.get("errors", [])) + list(other.get("errors", [])),
                        "queried": int(nexus.get("queried", 0)) + int(other.get("queried", 0)),
                        "sourcesChecked": other.get("sourceProjects", 0),
                        "manualSources": other.get("manualSources", 0),
                        "nexusCoverage": coverage, "mode": "all"}
                else:
                    result, effective_mode = scan_nexus(mode)
                with self._guard:
                    if self._snapshot.get("profileId") != snapshot.get("profileId") or [(x.get("id"), x.get("installedVersion")) for x in self._snapshot.get("items", [])] != version_signature:
                        # Never leak a finished scan's status into another MO2 profile.
                        self._scan = {"state": "stale", "reason": "Profile or installed versions changed during scan"}
                    else:
                        from_source = {str(i.get("id")): i for i in snapshot.get("items", [])}
                        for item in self._snapshot.get("items", []):
                            key = str(item.get("id", ""))
                            scanned_item = from_source.get(key)
                            if scanned_item and scanned_item.get("source"):
                                item["source"] = scanned_item["source"]
                            update = result.get("itemRecords", {}).get(key) or result["records"].get(str(item.get("nexusId") or 0))
                            if update:
                                item["update"] = update
                        self._scan = {"state": "partial" if result.get("errors") else "completed", "mode": effective_mode, **result, "checked": result.get("queried", 0), "total": result.get("queried", 0)}
                        # A full audit is required, following the latest verified install.
                        # Local / cached and quick activity checks cannot crown a profile.
                        self.chronicle.maybe_victory(snapshot["profileId"], self._snapshot, self._scan,
                            scanned_after_update=(base_sequence == self.chronicle.overview(snapshot["profileId"])["updates"]))
                    self._generation += 1
            except (MissingAuth, NexusError, ValueError, OSError) as ex:
                with self._guard:
                    self._scan = {"state": "error", "mode": mode, "error": str(ex), "checked": 0, "total": 0} if self._snapshot.get("profileId") == snapshot.get("profileId") else {"state": "idle", "reason": "Profile changed during scan"}
            except Exception as ex:
                with self._guard:
                    self._scan = {"state": "error", "mode": mode, "error": "Unexpected scan failure: " + type(ex).__name__} if self._snapshot.get("profileId") == snapshot.get("profileId") else {"state": "idle", "reason": "Profile changed during scan"}
        threading.Thread(target=worker, name="ArsenalNexusScan", daemon=True).start()
        return {"ok": True, "state": "running"}

    def _progress(self, data: dict) -> None:
        with self._guard:
            self._scan.update(data)

    def scan_state(self) -> dict:
        with self._guard:
            return deepcopy(self._scan)

    def fetch_files(self, mod_id: int) -> dict:
        if mod_id <= 0 or mod_id not in {int(i.get("nexusId") or 0) for i in self.snapshot().get("items", [])}:
            return {"ok": False, "error": "This Nexus ID is not associated with the active MO2 profile"}
        with self._guard:
            game = self.snapshot().get("game", "skyrimspecialedition")
            cached = self._file_cache.get((game, mod_id))
            if cached:
                return deepcopy(cached)
            key = self.api_key
            native_nexus = self.native_nexus_available
        if not key and not native_nexus:
            return {"ok": False, "error": "Connect to Nexus in MO2 Settings > Nexus or supply a fallback key.", "mo2Login": True}
        try:
            domain = self.snapshot().get("game", "skyrimspecialedition")
            from .scanner import safe_game_slug
            if native_nexus and not key:
                response = self.call_ui("nexus-files",
                    {"game": safe_game_slug(domain), "modId": mod_id}, timeout=70)
                if not response.get("ok"):
                    return response
                files = response.get("files")
            else:
                payload = NexusClient(key).get(f"/v1/games/{safe_game_slug(domain)}/mods/{mod_id}/files.json")
                files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list):
                return {"ok": False, "error": "Nexus did not return a files list"}
            items = []
            for file in files:
                if not isinstance(file, dict):
                    continue
                try:
                    fid = int(file.get("fileId") or file.get("file_id") or 0)
                except (TypeError, ValueError):
                    continue
                if fid > 0:
                    items.append({"fileId": fid, "name": str(file.get("name") or file.get("fileName") or file.get("file_name") or f"File {fid}")[:190], "version": str(file.get("version") or ""), "category": int(file.get("category", file.get("category_id", 0)) or 0), "sizeInKB": int(file.get("sizeInKB", file.get("size_kb", 0)) or 0), "uploaded": str(file.get("uploaded_time") or ""), "fileName": str(file.get("fileName") or file.get("file_name") or "")[:190], "description": unescape(re.sub(r"<[^>]*>", " ", str(file.get("description") or "")))[:500].strip()})
            items.sort(key=lambda f: (f["category"] != 1, -f["fileId"]))
            result = {"ok": True, "files": items, "modId": mod_id}
            with self._guard:
                self._file_cache[(domain, mod_id)] = result
            return deepcopy(result)
        except (NexusError, MissingAuth, ValueError) as ex:
            return {"ok": False, "error": str(ex)}

    def request_download(self, mod_id: int, file_id: int, confirmation: str,
                         item_id: str = "", installed_version: str = "") -> dict:
        if confirmation != "DOWNLOAD":
            return {"ok": False, "error": "Confirmation required"}
        files = self.fetch_files(mod_id)
        if not files.get("ok") or file_id not in {int(f["fileId"]) for f in files["files"]}:
            return {"ok": False, "error": "The selected file must be verified in the Nexus Files list first"}
        result = self.call_ui("download", {"modId": mod_id, "fileId": file_id})
        if result.get("ok"):
            with self._guard:
                self._downloads[int(result["downloadId"])] = {"provider": "nexus", "modId": mod_id,
                    "fileId": file_id, "itemId": item_id, "installedVersion": installed_version,
                    "status": "downloading", "profileId": self._snapshot.get("profileId")}
        return result

    def queue_reviewed_downloads(self, payload: dict) -> dict:
        """Serialize reviewed batches so concurrent clicks cannot enqueue duplicates."""
        with self._queue_lock:
            return self._queue_reviewed_downloads_once(payload)

    def _queue_reviewed_downloads_once(self, payload: dict) -> dict:
        """Preflight a complete Nexus + GitHub release queue before ANY downloads.

        One reviewed queue can contain both sources. It never overwrites mods,
        never auto-installs, and reports every attempted native download.
        """
        snapshot = self.snapshot()
        raw = payload.get("entries")
        if (payload.get("profileId") != snapshot.get("profileId") or
                payload.get("confirm") != "QUEUE_REVIEWED_DOWNLOADS" or
                not isinstance(raw, list) or not raw):
            return {"ok": False, "error": "Active profile, reviewed entries and confirmation required", "queued": [], "failed": []}
        nexus_entries = [x for x in raw if isinstance(x, dict) and x.get("kind") != "source"]
        source_entries = [x for x in raw if isinstance(x, dict) and x.get("kind") == "source"]
        if len(nexus_entries) + len(source_entries) != len(raw):
            return {"ok": False, "error": "Invalid source entry", "queued": [], "failed": []}
        try:
            approved = validate_batch_selection(snapshot, {**payload, "entries": nexus_entries}) if nexus_entries else []
            sources = [validate_release_asset(snapshot, x) for x in source_entries]
            seen_rows = [x["itemId"] for x in approved + sources]
            if len(set(seen_rows)) != len(seen_rows):
                raise PlanError("Duplicate MO2 row selected in mixed-source batch")
        except PlanError as exc:
            return {"ok": False, "error": str(exc), "queued": [], "failed": []}
        # A queued, completed, or installed archive belongs to this profile
        # until it is removed or a different installed version is scanned.
        with self._guard:
            already_queued = {str(ticket.get("itemId")) for ticket in self._downloads.values()
                              if ticket.get("profileId") == snapshot.get("profileId")
                              and ticket.get("itemId")
                              and ticket.get("status") not in ("failed", "removed")}
        for item in approved + sources:
            if item["itemId"] in already_queued:
                return {"ok": False, "queued": [], "failed": [],
                        "error": "An update for this MO2 mod is already queued. Finish its download or installation first."}
        # Preflight ALL Nexus file identities. GitHub assets were validated
        # against server-owned release results above, never client URLs.
        for item in approved:
            available = self.fetch_files(item["modId"])
            if not available.get("ok"):
                return {"ok": False, "queued": [], "failed": [{"itemId": item["itemId"],
                        "error": available.get("error", "Nexus file list unavailable")}]}
            if item["fileId"] not in {int(f["fileId"]) for f in available.get("files", [])}:
                return {"ok": False, "queued": [], "failed": [{"itemId": item["itemId"],
                        "error": "Exact Nexus file is no longer in the live Files list"}]}
        queued, failed = [], []
        for item in approved + sources:
            live = self.snapshot()
            current = next((x for x in live.get("items", []) if x.get("id") == item["itemId"]), None)
            same = bool(live.get("profileId") == snapshot.get("profileId") and current is not None
                        and str(current.get("installedVersion") or "") == item["installedVersion"]
                        and (current.get("update") or {}).get("verdict") == "candidate")
            if not same:
                failed.append({"itemId": item["itemId"], "error": "Profile or update verdict changed"})
                break
            if item.get("kind") == "source":
                try:
                    renewed = validate_release_asset(live, {
                        "kind": "source", "itemId": item["itemId"],
                        "assetId": item["assetId"], "installedVersion": item["installedVersion"],
                        "reviewed": True, "riskAcknowledged": True})
                except PlanError as exc:
                    failed.append({"itemId": item["itemId"], "error": str(exc)})
                    break
                result = self.call_ui("download-url", {"url": renewed["url"],
                    "name": renewed["name"], "itemId": renewed["itemId"]})
                if result.get("ok"):
                    with self._guard:
                        self._downloads[int(result["downloadId"])] = {
                            "provider": "github", "name": renewed["name"],
                            "itemId": item["itemId"], "installedVersion": item["installedVersion"],
                            "status": "downloading", "profileId": live.get("profileId")}
            else:
                if int(current.get("nexusId") or 0) != item["modId"]:
                    failed.append({"itemId": item["itemId"], "error": "Nexus project changed"})
                    break
                result = self.request_download(item["modId"], item["fileId"], "DOWNLOAD",
                                               item["itemId"], item["installedVersion"])
            if result.get("ok"):
                queued.append({"itemId": item["itemId"], "provider": item.get("provider", "nexus"),
                               "downloadId": result.get("downloadId")})
            else:
                failed.append({"itemId": item["itemId"], "error": str(result.get("error") or "Download rejected")})
        return {"ok": True, "partial": bool(failed), "queued": queued, "failed": failed,
                "message": f"Queued {len(queued)} reviewed archive(s). Install each separately in MO2."}

    def set_download_status(self, download_id: int, state: str) -> None:
        with self._guard:
            if download_id in self._downloads:
                # Late download callbacks must not turn an installed/active installer
                # back into an actionable "complete" ticket.
                if self._downloads[download_id]["status"] not in ("installing", "installed"):
                    self._downloads[download_id]["status"] = state

    def downloads(self) -> dict:
        with self._guard:
            return deepcopy({k:v for k,v in self._downloads.items() if v.get("profileId")==self._snapshot.get("profileId")})

    def install_reviewed_download(self, download_id: int) -> dict:
        """Open MO2's native installer once for an exact completed profile ticket.

        Reject stale or switched profiles, prevent overlapping installers, and
        preserve retryability after cancellation. The browser never installs files.
        """
        if isinstance(download_id, bool) or download_id <= 0:
            return {"ok": False, "error": "Invalid MO2 download ID"}
        if not self._install_lock.acquire(blocking=False):
            return {"ok": False, "error": "Another MO2 installer is already open"}
        try:
            with self._guard:
                ticket = self._downloads.get(download_id)
                if not ticket or ticket.get("profileId") != self._snapshot.get("profileId"):
                    return {"ok": False, "error": "Download does not belong to this active MO2 profile"}
                if ticket.get("status") != "complete":
                    return {"ok": False, "error": "This download is not ready to install"}
                item_id = ticket.get("itemId")
                if item_id:
                    current = next((m for m in self._snapshot.get("items", [])
                                    if str(m.get("id")) == item_id), None)
                    if (current is None or
                            str(current.get("installedVersion") or "") != ticket.get("installedVersion")):
                        return {"ok": False, "error": "Installed mod changed since queueing; rescan and review this file again"}
                ticket["status"] = "installing"
            result = self.call_ui("install", {"downloadId": download_id}, timeout=180)
            with self._guard:
                if self._downloads.get(download_id) is ticket:
                    if result.get("ok"):
                        ticket["status"] = "installed"
                        ticket["verifiedUpdate"] = result.get("verifiedUpdate") is True
                        ticket["installedName"] = str(result.get("installedName") or "")
                        ticket["backupPath"] = str((result.get("backup") or {}).get("backupPath") or "")
                    elif "MO2 is busy" in str(result.get("error") or ""):
                        # Native installation may still be running after the HTTP
                        # timeout. Do not enable a second installer blindly.
                        ticket["status"] = "attention"
                    else:
                        ticket["status"] = "complete"  # Cancelled/failed, may be retried.
            return result
        finally:
            self._install_lock.release()

    def start(self) -> str:
        if self._server:
            return f"http://127.0.0.1:{self._server.server_port}/"
        self._stopped = False
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        srv.daemon_threads = True
        self._server = srv
        self._thread = threading.Thread(target=srv.serve_forever, daemon=True, name="ArsenalLoopbackHTTP")
        self._thread.start()
        return f"http://127.0.0.1:{srv.server_port}/"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._stopped = True

    def handler(self):
        service = self
        class Handler(BaseHTTPRequestHandler):
            server_version = "MO2Arsenal/0.3"
            sys_version = ""

            def log_message(self, fmt, *args):
                return

            def _headers(self, typ="application/json; charset=utf-8", length=0):
                self.send_response(200)
                self.send_header("Content-Type", typ)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                self.end_headers()

            def _reply(self, obj, status=200):
                payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(payload)

            def _valid_host(self):
                allowed = {"127.0.0.1", "localhost", "[::1]"}
                host = self.headers.get("Host", "")
                try:
                    name, port = host.rsplit(":", 1)
                    return name in allowed and port.isdigit() and int(port) == self.server.server_port
                except (ValueError, TypeError):
                    return False

            def _authorize(self, state_change=False):
                if not self._valid_host():
                    self._reply({"ok": False, "error": "Bad host"}, 403)
                    return False
                if state_change:
                    origin = self.headers.get("Origin", "")
                    if origin != f"http://127.0.0.1:{self.server.server_port}" and origin != f"http://localhost:{self.server.server_port}":
                        self._reply({"ok": False, "error": "Cross-origin command rejected"}, 403)
                        return False
                if not hmac.compare_digest(self.headers.get("X-Arsenal-Token", ""), service.token):
                    self._reply({"ok": False, "error": "Unauthorized session"}, 403)
                    return False
                return True

            def do_GET(self):
                if not self._valid_host():
                    return self._reply({"ok": False, "error": "Bad host"}, 403)
                uri = urllib.parse.urlsplit(self.path)
                if uri.path == "/":
                    boot = json.dumps({"active": True, "token": service.token}, ensure_ascii=False).replace("<", "\\u003c")
                    html = service.html.replace("<!--__MO2_BOOT__-->", f"<script>window.__MO2_BRIDGE_BOOT__={boot}</script>")
                    output = html.encode("utf-8")
                    self._headers("text/html; charset=utf-8", len(output))
                    return self.wfile.write(output)
                if not self._authorize():
                    return
                if uri.path == "/api/snapshot":
                    return self._reply({"ok": True, "catalog": service.snapshot(), "generation": service._generation})
                if uri.path == "/api/revision":
                    return self._reply({"ok": True, "generation": service._generation, "profileId": service._snapshot.get("profileId"), "selectedModId": service.selected_mod_id})
                if uri.path == "/api/siege/targets":
                    return self._reply(service.siege_targets())
                if uri.path == "/api/siege/bounties":
                    return self._reply(service.chronicle.boss_bounties(service.snapshot().get("profileId", "")))
                if uri.path == "/api/siege/state":
                    return self._reply(service.chronicle.siege_state(service.snapshot().get("profileId", "")))
                if uri.path == "/api/realm":
                    return self._reply(service.chronicle.realm_wallet(service.snapshot().get("profileId", "")))
                if uri.path == "/api/realm/world":
                    return self._reply(service.chronicle.realm_world(service.snapshot().get("profileId", "")))
                if uri.path == "/api/game":
                    return self._reply(service.chronicle.overview(service.snapshot().get("profileId", "")))
                if uri.path == "/api/progress":
                    return self._reply({"ok": True, "profileId": service.snapshot().get("profileId"), "progress": service.chronicle.progress(service.snapshot().get("profileId", ""))})
                if uri.path == "/api/scan":
                    return self._reply({"ok": True, "scan": service.scan_state()})
                if uri.path == "/api/update-plan":
                    return self._reply(plan_updates(service.snapshot()))
                if uri.path == "/api/downloads":
                    return self._reply({"ok": True, "downloads": service.downloads()})
                if uri.path == "/api/files":
                    query = urllib.parse.parse_qs(uri.query)
                    try:
                        mid = int(query.get("modId", ["0"])[0])
                    except (ValueError, TypeError):
                        mid = 0
                    return self._reply(service.fetch_files(mid))
                if uri.path == "/api/capabilities":
                    return self._reply({"ok": True, "app": "MO2 Update Arsenal", "live": True, "nexusKeySession": bool(service.api_key), "nativeNexusAvailable": bool(service.native_nexus_available), "keyStorage": "RAM only", "features": ["snapshot", "local-check", "nexus-quick", "nexus-deep", "nexus-files", "native-mo2-nexus-login", "github-release-checks", "gitlab-release-checks", "manual-source-review", "github-direct-downloads", "mixed-source-batch-updates", "reviewed-update-workshop", "mo2-download", "mo2-install", "backup", "focus-mod", "chronicle", "verified-achievements", "ranked-full-clear", "persistent-profile-progress", "realm-wallet", "realm-purchase", "realm-save", "siege-targets", "siege-state", "siege-bounties", "siege-mod-focus"]})
                return self._reply({"ok": False, "error": "Unknown endpoint"}, 404)

            def do_POST(self):
                if not self._authorize(True):
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > (MAX_PROGRESS_REQUEST if self.path.split("?", 1)[0] in ("/api/progress", "/api/update-queue") else MAX_REALM_REQUEST if self.path.split("?", 1)[0] in ("/api/realm/world", "/api/siege/state") else MAX_REQUEST):
                        return self._reply({"ok": False, "error": "Request size rejected"}, 413)
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("Request must be an object")
                except (ValueError, json.JSONDecodeError):
                    return self._reply({"ok": False, "error": "Invalid JSON"}, 400)
                uri = urllib.parse.urlsplit(self.path)
                if uri.path == "/api/siege/boss/defeated":
                    return self._reply(service.siege_boss_defeated(str(data.get("profileId") or ""),
                        str(data.get("modId") or ""), str(data.get("runId") or "")))
                if uri.path == "/api/siege/state":
                    current = service.snapshot()
                    if data.get("profileId") != current.get("profileId"):
                        return self._reply({"ok": False, "error": "Profile changed while saving Siege"}, 409)
                    try:
                        revision = int(data.get("revision", -1))
                    except (TypeError, ValueError):
                        return self._reply({"ok": False, "error": "Invalid siege revision"}, 400)
                    return self._reply(service.chronicle.save_siege_state(current["profileId"], data.get("state"), revision))
                if uri.path == "/api/realm/purchase":
                    current = service.snapshot()
                    if data.get("profileId") != current.get("profileId"):
                        return self._reply({"ok": False, "error": "Profile changed while spending Realm currency"}, 409)
                    return self._reply(service.chronicle.purchase_realm(current["profileId"], str(data.get("sku", "")), str(data.get("txn", ""))))
                if uri.path == "/api/realm/world":
                    current = service.snapshot()
                    if data.get("profileId") != current.get("profileId"):
                        return self._reply({"ok": False, "error": "Profile changed while saving the world"}, 409)
                    try:
                        revision = int(data.get("revision", -1))
                    except (TypeError, ValueError):
                        return self._reply({"ok": False, "error": "Invalid world revision"}, 400)
                    return self._reply(service.chronicle.save_realm_world(current["profileId"], data.get("world"), revision))
                if uri.path == "/api/focus":
                    return self._reply(service.focus_mod(str(data.get("id") or ""), str(data.get("profileId") or "")))
                if uri.path == "/api/progress":
                    current = service.snapshot()
                    if data.get("profileId") != current.get("profileId"):
                        return self._reply({"ok": False, "error": "Profile changed while saving; retry after refresh"}, 409)
                    return self._reply(service.chronicle.set_progress(current["profileId"], data.get("rows"), data.get("prefs"), data.get("verifiedRuntime", ""), {str(m["id"]) for m in current.get("items", [])}))
                if uri.path == "/api/victory/ack":
                    current = service.snapshot()
                    if data.get("profileId") != current.get("profileId"):
                        return self._reply({"ok": False, "error": "Profile changed while acknowledging victory"}, 409)
                    return self._reply(service.chronicle.ack_victory(current["profileId"], str(data.get("victoryId") or "")))
                if uri.path == "/api/refresh":
                    return self._reply(service.call_ui("refresh", {}))
                if uri.path == "/api/runtime":
                    return self._reply(service.call_ui("runtime", {}, timeout=180))
                if uri.path == "/api/scan":
                    return self._reply(service.begin_scan(data.get("mode", "quick")))
                if uri.path == "/api/update-queue":
                    return self._reply(service.queue_reviewed_downloads(data))
                if uri.path == "/api/download":
                    try:
                        result = service.request_download(int(data.get("modId") or 0), int(data.get("fileId") or 0), data.get("confirm"))
                    except (ValueError, TypeError):
                        result = {"ok": False, "error": "Invalid Nexus file identifiers"}
                    return self._reply(result)
                if uri.path == "/api/install":
                    try:
                        download_id = int(data.get("downloadId") or 0)
                    except (ValueError, TypeError):
                        download_id = 0
                    if data.get("confirm") != "INSTALL" or not service.downloads().get(download_id, {}).get("status") == "complete":
                        return self._reply({"ok": False, "error": "Download is not complete or install was not confirmed"})
                    return self._reply(service.install_reviewed_download(download_id))
                if uri.path == "/api/backup":
                    return self._reply(service.call_ui("backup", {"confirm": data.get("confirm") == "BACKUP"}))
                if uri.path == "/api/nexus-connect":
                    return self._reply(service.call_ui("connect", {}, timeout=180))
                return self._reply({"ok": False, "error": "Unknown command"}, 404)
        return Handler
