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
from .update_plan import plan_updates

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
        self._snapshot = snapshot
        self._guard = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._scan: dict[str, Any] = {"state": "idle"}
        self._file_cache: dict[int, dict] = {}
        self._downloads: dict[int, dict] = {}
        self._stopped = False
        self._generation = 1

    def snapshot(self) -> dict:
        with self._guard:
            return deepcopy(self._snapshot)

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
        if mode not in ("quick", "deep", "local"):
            return {"ok": False, "error": "Invalid scan mode"}
        with self._guard:
            if self._scan.get("state") == "running":
                return {"ok": False, "error": "An update scan is already running"}
            self._scan = {"state": "running", "mode": mode, "checked": 0, "total": 0, "errors": []}
            snapshot = self.snapshot()
            key = self.api_key
            base_sequence = self.chronicle.overview(snapshot["profileId"])["updates"]
            version_signature = [(x.get("id"), x.get("installedVersion")) for x in snapshot.get("items", [])]
        def worker():
            try:
                if mode == "local":
                    result = local_mo2_candidates(snapshot)
                else:
                    if not key:
                        raise MissingAuth("Fast and full remote scans require a Nexus API key in MO2's native Connect dialog; use MO2 Cached for zero-key results.")
                    result = UpdateScanner(self.cache, NexusClient(key)).scan(snapshot, mode, self._progress)
                with self._guard:
                    if self._snapshot.get("profileId") != snapshot.get("profileId") or [(x.get("id"), x.get("installedVersion")) for x in self._snapshot.get("items", [])] != version_signature:
                        # Never leak a finished scan's status into another MO2 profile.
                        self._scan = {"state": "stale", "reason": "Profile or installed versions changed during scan"}
                    else:
                        for item in self._snapshot.get("items", []):
                            update = result.get("itemRecords", {}).get(str(item.get("id", ""))) or result["records"].get(str(item.get("nexusId") or 0))
                            if update:
                                item["update"] = update
                        self._scan = {"state": "completed", "mode": mode, **result, "checked": result.get("queried", 0), "total": result.get("queried", 0)}
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
        if not key:
            return {"ok": False, "error": "Enter a Nexus API key via MO2's native Connect dialog to list exact downloadable files. Alternatively use Files on Nexus and Mod Manager Download through MO2.", "mo2Login": True}
        try:
            domain = self.snapshot().get("game", "skyrimspecialedition")
            from .scanner import safe_game_slug
            payload = NexusClient(key).get(f"/v1/games/{safe_game_slug(domain)}/mods/{mod_id}/files.json")
            files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list):
                return {"ok": False, "error": "Nexus did not return a files list"}
            items = []
            for file in files:
                if not isinstance(file, dict):
                    continue
                try:
                    fid = int(file.get("file_id") or 0)
                except (TypeError, ValueError):
                    continue
                if fid > 0:
                    items.append({"fileId": fid, "name": str(file.get("name") or file.get("file_name") or f"File {fid}")[:190], "version": str(file.get("version") or ""), "category": int(file.get("category_id") or 0), "sizeInKB": int(file.get("size_kb") or 0), "uploaded": str(file.get("uploaded_time") or ""), "fileName": str(file.get("file_name") or "")[:190]})
            items.sort(key=lambda f: (f["category"] != 1, -f["fileId"]))
            result = {"ok": True, "files": items, "modId": mod_id}
            with self._guard:
                self._file_cache[(domain, mod_id)] = result
            return deepcopy(result)
        except (NexusError, MissingAuth, ValueError) as ex:
            return {"ok": False, "error": str(ex)}

    def request_download(self, mod_id: int, file_id: int, confirmation: str) -> dict:
        if confirmation != "DOWNLOAD":
            return {"ok": False, "error": "Confirmation required"}
        files = self.fetch_files(mod_id)
        if not files.get("ok") or file_id not in {int(f["fileId"]) for f in files["files"]}:
            return {"ok": False, "error": "The selected file must be verified in the Nexus Files list first"}
        result = self.call_ui("download", {"modId": mod_id, "fileId": file_id})
        if result.get("ok"):
            with self._guard:
                self._downloads[int(result["downloadId"])] = {"modId": mod_id, "fileId": file_id, "status": "downloading", "profileId": self._snapshot.get("profileId")}
        return result

    def set_download_status(self, download_id: int, state: str) -> None:
        with self._guard:
            if download_id in self._downloads:
                self._downloads[download_id]["status"] = state

    def downloads(self) -> dict:
        with self._guard:
            return deepcopy({k:v for k,v in self._downloads.items() if v.get("profileId")==self._snapshot.get("profileId")})

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
                    return self._reply({"ok": True, "app": "MO2 Update Arsenal", "live": True, "nexusKeySession": bool(service.api_key), "keyStorage": "RAM only", "features": ["snapshot", "local-check", "nexus-quick", "nexus-deep", "nexus-files", "reviewed-update-workshop", "mo2-download", "mo2-install", "backup", "focus-mod", "chronicle", "verified-achievements", "ranked-full-clear", "persistent-profile-progress", "realm-wallet", "realm-purchase", "realm-save", "siege-targets", "siege-state", "siege-bounties", "siege-mod-focus"]})
                return self._reply({"ok": False, "error": "Unknown endpoint"}, 404)

            def do_POST(self):
                if not self._authorize(True):
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > (MAX_PROGRESS_REQUEST if self.path.split("?", 1)[0] == "/api/progress" else MAX_REALM_REQUEST if self.path.split("?", 1)[0] in ("/api/realm/world", "/api/siege/state") else MAX_REQUEST):
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
                    return self._reply(service.call_ui("install", {"downloadId": download_id}, timeout=180))
                if uri.path == "/api/backup":
                    return self._reply(service.call_ui("backup", {"confirm": data.get("confirm") == "BACKUP"}))
                if uri.path == "/api/nexus-connect":
                    return self._reply(service.call_ui("connect", {}, timeout=180))
                return self._reply({"ok": False, "error": "Unknown command"}, 404)
        return Handler
