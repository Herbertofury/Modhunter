"""Loopback-only browser-to-MO2 bridge. No remote origins, no file APIs.

The server never calls mobase itself; all privileged commands cross a Qt queued signal.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
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
from .update_plan import PlanError, plan_updates, validate_batch_selection, validate_release_asset, risk_reasons
from .file_advisor import advise_nexus_files
from .install_brief import build_install_brief
from .quick_update import choose_nexus_file, choose_release_asset
from .operation_journal import OperationJournal
from .session_discovery import SessionDiscovery
from .intelligence import ModIntelligence
from .source_adapters import compact_source_update, PUBLIC_API_PROVIDERS
from .version import VERSION

MAX_REQUEST = 16_384
MAX_PROGRESS_REQUEST = 8_000_000
MAX_REALM_REQUEST = 800_000
MAX_CACHED_SNAPSHOT = 32 * 1024 * 1024


class UIRequest:
    """An atomic handoff to Qt, with cancellation *before* native execution.

    A caller timing out must not leave a queued mutation that executes later.
    Once Qt owns a mutation, its actual completion remains authoritative; it
    cannot safely be treated as failed and retried just because HTTP timed out.
    """

    def __init__(self, cmd: str, payload: dict, timeout: float | None = None):
        self.cmd, self.payload = cmd, payload
        self.event = threading.Event()
        self.result: dict[str, Any] = {}
        self.deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        self._lock = threading.Lock()
        self._state = "queued"
        self._done_callbacks: list[Callable[[], None]] = []

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def begin(self) -> bool:
        """Atomically claim a queued request on Qt's thread, at most once."""
        with self._lock:
            if self._state != "queued":
                return False
            if self.deadline is None or time.monotonic() < self.deadline:
                self._state = "running"
                return True
        self.cancel_if_pending()
        return False

    def add_done_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if not self.event.is_set():
                self._done_callbacks.append(callback)
                return
        callback()

    def _complete(self, data: dict, *, cancel: bool = False,
                  pending_only: bool = False) -> bool:
        with self._lock:
            if self._state in ("completed", "cancelled"):
                return False
            if pending_only and self._state != "queued":
                return False
            self._state = "cancelled" if cancel else "completed"
            self.result = data
            callbacks, self._done_callbacks = self._done_callbacks, []
            self.event.set()
        # Callbacks remove bookkeeping only, never call Qt from this thread.
        for callback in callbacks:
            callback()
        return True

    def cancel_if_pending(self) -> bool:
        return self._complete({"ok": False, "cancelled": True, "started": False,
            "error": "MO2 was busy. The queued request expired before it started; retry after returning to MO2."},
            cancel=True, pending_only=True)

    def cancel(self) -> bool:
        """Abandon an asynchronous read; native mutations use pending-only."""
        return self._complete({"ok": False, "cancelled": True,
            "error": "MO2 Nexus request timed out. Check its connection and retry."}, cancel=True)

    def finish(self, data: dict) -> bool:
        return self._complete(data)


class ArsenalService:
    def __init__(self, app_html: str, cache_path: Path, ui_call: Callable[[UIRequest], None], snapshot: dict,
                 *, discovery_directory: Path | None = None):
        self.html = app_html
        self.token = secrets.token_urlsafe(36)
        self.ui_call = ui_call
        self.cache = Cache(cache_path)
        self.chronicle = Chronicle(cache_path.with_name("chronicle.json"))
        self.chronicle.set_profile_name(snapshot.get("profileId", ""), snapshot.get("profileName", "Default"))
        self.selected_mod_id = ""
        self.api_key = ""  # Volatile: never persisted, sent to the browser, or included in backup files.
        self.native_nexus_available = False
        self.native_nexus_files_available: bool | None = None
        self.native_nexus_files_error = ""
        self._snapshot = snapshot
        self._items_by_id = {str(i.get("id")): i for i in snapshot.get("items", [])}
        self._labels_to_ids = self._label_index(snapshot)
        self._nexus_ids = {int(i.get("nexusId") or 0) for i in snapshot.get("items", [])}
        self._guard = threading.RLock()
        self._snapshot_build_lock = threading.Lock()
        self._snapshot_payload_cache: tuple[tuple, bytes] | None = None
        self._queue_lock = threading.Lock()  # Serialize reviewed batches; never enqueue the same mod twice.
        self._quick_job: dict[str, Any] = {"state": "idle", "profileId": snapshot.get("profileId"), "queued": [], "skipped": [], "failed": []}
        self._install_lock = threading.Lock()  # MO2 supports one interactive installer at a time.
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._scan: dict[str, Any] = {"state": "idle"}
        self._scan_epoch = 0
        self._scan_cancel = threading.Event()
        self._source_scanner = None
        self._source_cache_path = cache_path.with_name("source-adapter-cache.json")
        self._file_cache: dict[tuple[str, int], dict] = {}
        self._file_epoch = 0
        self._file_inflight: dict[tuple, dict] = {}
        self._downloads: dict[int, dict] = {}
        # MO2 can emit a download-complete signal before startDownload returns
        # its ID. Keep only events seen during a native start dispatch.
        self._download_dispatches = 0
        self._early_download_events: dict[int, str] = {}
        self.journal = OperationJournal(cache_path.with_name("update-operations.jsonl"))
        self._stopped = False
        self._generation = 1
        self._execution_epoch = 0  # Monotonic identity for queued native intent.
        self._pending_ui: set[UIRequest] = set()
        self._discovery = SessionDiscovery(discovery_directory)
        self.intelligence = ModIntelligence(self, cache_path.with_name("mod-intelligence.json"))

    def _publish_discovery(self) -> None:
        # Serialize with stop/profile replacement so an older profile cannot
        # republish a descriptor after the service has closed.
        with self._guard:
            if self._server is not None and not self._stopped:
                self._discovery.publish(f"http://127.0.0.1:{self._server.server_port}/",
                                        str(self._snapshot.get("profileId") or ""),
                                        str(self._snapshot.get("profileName") or ""))

    def profile_id(self) -> str:
        """O(1) profile identity for polling; never clone the whole mod list."""
        with self._guard:
            return str(self._snapshot.get("profileId") or "")

    def revision(self) -> dict:
        with self._guard:
            return {"ok": True, "generation": self._generation,
                    "profileId": self._snapshot.get("profileId"),
                    "selectedModId": self.selected_mod_id}

    def health(self) -> dict:
        with self._guard:
            return {"ok": True, "app": "Modhunter", "protocol": 1, "live": True,
                    "version": VERSION, "profileId": self._snapshot.get("profileId"),
                    "generation": self._generation}

    def snapshot_response(self) -> dict:
        # The catalog and its revision must describe the same state even when
        # MO2 switches profiles while an HTTP response is being built.
        with self._guard:
            return {"ok": True, "catalog": self.snapshot(), "generation": self._generation}

    def _snapshot_payload_key(self) -> tuple:
        # Called while holding _guard. Time-derived health summaries already
        # use this same 15-second freshness window; no command uses this cache.
        return (self._generation, self._execution_epoch, self.cache.revision(),
                self.chronicle.source_revision(str(self._snapshot.get("profileId") or "")),
                tuple(self.intelligence._state.items()), bool(self.intelligence._configured),
                bool(self.intelligence.runner.available), int(time.time() // 15))

    def snapshot_response_bytes(self) -> bytes:
        """Share one complete, immutable response per observed catalog state.

        Only concurrent snapshot builders wait on _snapshot_build_lock. JSON
        serialization uses an owned snapshot outside the native execution lock.
        A superseded build may finish its original response but can never enter
        the current cache. Keep at most one response, and no oversized cache.
        """
        with self._snapshot_build_lock:
            with self._guard:
                key = self._snapshot_payload_key()
                if self._snapshot_payload_cache and self._snapshot_payload_cache[0] == key:
                    return self._snapshot_payload_cache[1]
                response = self.snapshot_response()
            payload = json.dumps(response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            with self._guard:
                if self._snapshot_payload_key() == key and not self._stopped:
                    self._snapshot_payload_cache = (key, payload) if len(payload) <= MAX_CACHED_SNAPSHOT else None
            return payload

    @staticmethod
    def _label_index(snapshot: dict) -> dict:
        labels = {}
        for item in snapshot.get("items", []):
            labels.setdefault(str(item.get("name") or ""), []).append(str(item.get("id") or ""))
        return labels

    def native_snapshot(self) -> dict:
        """Owned Qt input without provider decoration or per-row health work."""
        with self._guard:
            return deepcopy(self._snapshot)

    def native_selection_snapshot(self, item_ids: list[str]) -> dict:
        with self._guard:
            snapshot = {key: self._snapshot.get(key) for key in
                        ("profileId", "profileName", "game", "gameRuntime", "loader")}
            snapshot["items"] = [deepcopy(self._items_by_id[key]) for key in item_ids if key in self._items_by_id]
            return snapshot

    def has_nexus_mod(self, mod_id: int) -> bool:
        with self._guard:
            return mod_id in self._nexus_ids

    def set_selected_by_label(self, label: str) -> None:
        with self._guard:
            ids = self._labels_to_ids.get(label, [])
            self.set_selected(ids[0] if len(ids) == 1 else "")

    def snapshot(self, *, source_details=False) -> dict:
        with self._guard:
            if source_details:
                snapshot = deepcopy(self._snapshot)
            else:
                snapshot = {key: deepcopy(value) for key, value in self._snapshot.items() if key != "items"}
                snapshot["items"] = [deepcopy({**item, "update": compact_source_update(item["update"])})
                    if isinstance(item.get("update"), dict) else deepcopy(item) for item in self._snapshot.get("items", [])]
            snapshot["executionEpoch"] = self._execution_epoch
        # The existing MO2 Chronicle persists explicit user source links by
        # profile. Rehydrate them for scans without overwriting MO2 metadata.
        links = self.chronicle.source_links(snapshot.get("profileId", ""))
        return self.intelligence.decorate(self._apply_source_links(snapshot, links))

    def selection_snapshot(self, item_ids: list[str]) -> dict:
        """A private exact-row view for queue preflights, independent of list size."""
        with self._guard:
            snapshot = {key: self._snapshot.get(key) for key in
                        ("profileId", "profileName", "game", "gameRuntime", "loader")}
            snapshot["executionEpoch"] = self._execution_epoch
            snapshot["items"] = [deepcopy(self._items_by_id[key]) for key in item_ids
                                 if key in self._items_by_id]
        links = self.chronicle.source_links(snapshot.get("profileId", ""), item_ids)
        return self.intelligence.decorate(self._apply_source_links(snapshot, links), detailed=True)

    def source_details(self, item_id: str, profile_id: str) -> dict:
        with self._guard:
            if profile_id != self._snapshot.get("profileId"):
                return {"ok": False, "error": "MO2 profile changed; refresh source details"}
            if item_id not in self._items_by_id:
                return {"ok": False, "error": "Mod is not in the active MO2 profile"}
            snapshot = self.selection_snapshot([item_id])
            item = snapshot["items"][0]
            return {"ok": True, "profileId": profile_id, "itemId": item_id,
                    "update": deepcopy(item.get("update") or {}), "source": deepcopy(item.get("source") or {})}

    @staticmethod
    def _apply_source_links(snapshot: dict, links: dict) -> dict:
        for item in snapshot.get("items", []):
            candidate = canonical_source_url(links.get(str(item.get("id")), ""))
            update = item.get("update") or {}
            if not candidate and "sourceUrl" not in update:
                continue
            previous = canonical_source_url((item.get("source") or {}).get("url", ""))
            if candidate and candidate != previous:
                item["source"] = source_record(candidate, "MO2 Chronicle source link")
            expected = candidate or previous
            # A scanned override is evidence for that exact URL; retaining it
            # must not rewrite native MO2 metadata or survive clearing the link.
            if ((candidate and candidate != previous and update.get("sourceUrl") != candidate)
                    or "sourceUrl" in update and update["sourceUrl"] != expected):
                item.pop("update", None)
        return snapshot

    def save_progress(self, payload: dict) -> dict:
        """Save exact-profile notes without preparing the entire catalog.

        Source edits are execution-context changes. Commit their in-memory
        state and invalidate old intent atomically; filesystem persistence is
        outside the native execution guard.
        """
        with self._guard:
            profile = str(self._snapshot.get("profileId") or "")
            if self._stopped or payload.get("profileId") != profile:
                return {"ok": False, "profileChanged": True,
                        "error": "Profile changed while saving; retry after refresh"}
            expected_epoch = payload.get("executionEpoch", self._execution_epoch)
            if type(expected_epoch) is not int or expected_epoch != self._execution_epoch:
                return {"ok": False, "profileChanged": True,
                        "error": "MO2 context changed before saving; refresh before replacing progress"}
            before = {key: canonical_source_url(url) for key, url in self.chronicle.source_links(profile).items()}
            result = self.chronicle.set_progress(profile, payload.get("rows"), payload.get("prefs"),
                payload.get("verifiedRuntime", ""), self._items_by_id.keys(), persist=False)
            if not result.get("ok"):
                return result
            after = {key: canonical_source_url(url) for key, url in self.chronicle.source_links(profile).items()}
            changed = {key for key in before.keys() | after.keys() if before.get(key, "") != after.get(key, "")}
            if changed:
                self._execution_epoch += 1
                self._file_cache.clear()
                self._file_epoch += 1
                self._scan_epoch += 1
                self._scan_cancel.set()
                self._scan = {"state": "stale", "reason": "Mod source links changed; rescan their release information"}
                self._quick_job = {"state": "idle", "profileId": profile, "queued": [], "skipped": [], "failed": []}
                for item_id in changed:
                    if item_id in self._items_by_id:
                        self._items_by_id[item_id].pop("update", None)
                self._generation += 1
            result.update(sourceLinksChanged=bool(changed), generation=self._generation,
                          executionEpoch=self._execution_epoch)
        try:
            self.chronicle.persist()
        except OSError as exc:
            return {**result, "ok": False, "error": "Could not persist MO2 progress: " + str(exc)}
        return result

    def set_snapshot(self, snapshot: dict) -> None:
        with self._guard:
            old = self._snapshot
            same_profile = old.get("profileId") == snapshot.get("profileId")
            same_game = old.get("game") == snapshot.get("game")
            same_runtime = (old.get("gameRuntime") == snapshot.get("gameRuntime")
                            and old.get("loader") == snapshot.get("loader"))
            old_items = {str(i["id"]): i for i in old.get("items", [])}
            new_items = {str(i["id"]): i for i in snapshot.get("items", [])}
            def source_identity(item):
                source = item.get("source") or {}
                return source.get("url", ""), source.get("verified") is True, source.get("kind", "")
            versions_changed = old_items.keys() != new_items.keys() or any(
                old_items[key].get("installedVersion") != new_items[key].get("installedVersion")
                or old_items[key].get("nexusId") != new_items[key].get("nexusId")
                or source_identity(old_items[key]) != source_identity(new_items[key]) for key in old_items.keys() & new_items.keys())
            if same_profile and same_game and same_runtime:
                for i in snapshot.get("items", []):
                    before = old_items.get(str(i["id"]))
                    if (before and before.get("installedVersion") == i.get("installedVersion")
                            and before.get("nexusId") == i.get("nexusId")
                            and source_identity(before) == source_identity(i) and before.get("update")):
                        i["update"] = before["update"]
            awards = self.chronicle.observe(old, snapshot) if same_profile else []
            scan_context_changed = not (same_profile and same_game and same_runtime) or versions_changed
            if scan_context_changed:
                self._execution_epoch += 1
                self._file_cache.clear()  # File identity belongs to a specific game/profile.
                self._file_epoch += 1
                self._scan_epoch += 1
                self._scan_cancel.set()
                # A previous bulk queue must neither hold the new profile's
                # progress state nor resume after an A -> B -> A switch.
                self._quick_job = {"state": "idle", "profileId": snapshot.get("profileId"),
                                   "queued": [], "skipped": [], "failed": []}
            if not same_profile or not same_game:
                self._scan = {"state": "idle", "reason": "MO2 profile changed"}
                self.selected_mod_id = ""
            elif awards or versions_changed or not same_runtime:
                # A successful install/version change invalidates the old scan verdict.
                self._scan = {"state": "stale", "reason": "Installed mods or runtime changed: rescan to verify latest state"}
            self.chronicle.set_profile_name(snapshot.get("profileId", ""), snapshot.get("profileName", "Default"))
            self._snapshot = snapshot
            self._items_by_id = new_items
            self._labels_to_ids = self._label_index(snapshot)
            self._nexus_ids = {int(i.get("nexusId") or 0) for i in snapshot.get("items", [])}
            self._generation += 1
            self.intelligence.invalidate(reason="MO2 inventory refreshed; plugin copies and conversion hashes need a fresh check")
        self._publish_discovery()
        self.intelligence.auto_audit()

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
            m = self._items_by_id.get(mod_id)
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
        from .lore import describe_enemy_theme
        snapshot = self.snapshot()
        seen = set()
        targets = []
        for item in snapshot.get("items", []):
            update = item.get("update") or {}
            before = str(item.get("installedVersion") or "")
            target = str(update.get("latestVersion") or "")
            if not self._siege_candidate(item):
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

    @staticmethod
    def _siege_candidate(item: dict) -> bool:
        from .scanner import version_verdict
        update = item.get("update") or {}
        try:
            nexus_id = int(item.get("nexusId") or 0)
        except (TypeError, ValueError, OverflowError):
            return False
        return (str(item.get("id", "")).startswith("live-") and nexus_id > 0
                and update.get("verdict") == "candidate"
                and version_verdict(item.get("installedVersion"), update.get("latestVersion")) == "candidate")

    def siege_boss_defeated(self, profile_id: str, mod_id: str, run_id: str) -> dict:
        # Inspect native snapshot under the same lock as profile replacement.
        with self._guard:
            if profile_id != self._snapshot.get("profileId"):
                return {"ok": False, "error": "Profile changed before boss victory"}
            rows = self.selection_snapshot([mod_id])["items"]
            if not rows or not self._siege_candidate(rows[0]):
                return {"ok": False, "error": "Target is not a currently outdated MO2 mod"}
            mod = self._items_by_id[mod_id]
            return self.chronicle.claim_mod_boss(profile_id, mod, run_id)

    def set_key(self, key: str) -> None:
        # Key is deliberately not stored in browser/localStorage/on-disk.
        with self._guard:
            self.api_key = str(key).strip()

    def call_ui(self, cmd: str, payload: dict, timeout: float = 20,
                *, cancel_event: threading.Event | None = None) -> dict:
        req = UIRequest(cmd, payload, timeout=timeout)
        cancellable_read = cmd in ("nexus-metadata", "nexus-files") and cancel_event is not None
        if cancellable_read and cancel_event.is_set():
            req.cancel()
            return req.result
        self._submit_ui(req)
        if cancellable_read:
            while not req.event.is_set():
                if cancel_event.is_set():
                    req.cancel()  # Only an owned read may abandon a running Qt callback.
                    break
                remaining = req.deadline - time.monotonic()
                if remaining <= 0:
                    break
                req.event.wait(min(0.1, remaining))
            completed = req.event.is_set()
        else:
            completed = req.event.wait(timeout)
        if not completed:
            if req.cancel_if_pending():
                return req.result
            if cmd in ("nexus-metadata", "nexus-files"):
                req.cancel()  # Its late callback cannot resurrect an old read.
            else:
                # Native execution has already begun. Retain the operation's
                # lease until its real result arrives, avoiding duplicate work
                # and losing the download ID after a slow native handoff.
                req.event.wait()
        return req.result

    def _submit_ui(self, request: UIRequest) -> None:
        def forget():
            with self._guard:
                self._pending_ui.discard(request)
        with self._guard:
            if self._stopped:
                request.cancel_if_pending()
                return
            self._pending_ui.add(request)
        request.add_done_callback(forget)
        try:
            self.ui_call(request)
        except Exception:
            request.cancel_if_pending()
            forget()
            raise

    def begin_scan(self, mode: str, profile_id: str | None = None) -> dict:
        if mode not in ("quick", "deep", "local", "sources", "all"):
            return {"ok": False, "error": "Invalid scan mode"}
        with self._guard:
            if self._stopped:
                return {"ok": False, "error": "MO2 session is closed"}
            if profile_id is not None and profile_id != self._snapshot.get("profileId"):
                return {"ok": False, "error": "MO2 profile changed before scanning; refresh and try again"}
            if self._scan.get("state") == "running":
                return {"ok": False, "error": "An update scan is already running"}
            self._scan_epoch += 1
            job_epoch = self._scan_epoch
            self._scan_cancel.set()
            self._scan_cancel = threading.Event()
            scan_cancel = self._scan_cancel
            self._scan = {"state": "running", "mode": mode, "checked": 0, "total": 0, "errors": [], "jobId": job_epoch}
            self._file_cache.clear()  # Never review a cached Nexus file list after a fresh audit.
            self._file_epoch += 1
            snapshot = self.snapshot()
            key = self.api_key
            native_nexus = self.native_nexus_available
            base_sequence = self.chronicle.update_count(snapshot["profileId"])
            scan_identity = tuple(snapshot.get(key) for key in ("profileId", "game", "gameRuntime", "loader"))
        def is_current():
            return (not self._stopped and self._scan_epoch == job_epoch
                    and tuple(self._snapshot.get(key) for key in ("profileId", "game", "gameRuntime", "loader")) == scan_identity)
        def progress(data):
            with self._guard:
                if is_current() and self._scan.get("state") == "running":
                    self._scan.update(data)
        def worker():
            try:
                with self._guard:
                    if not is_current():
                        return
                def scan_nexus(which):
                    if which == "local" or (which == "quick" and native_nexus and not key):
                        return local_mo2_candidates(snapshot), "local"
                    if key:
                        client = NexusClient(key)
                    elif native_nexus:
                        import re
                        game = snapshot.get("game", "skyrimspecialedition")
                        def mo2_nexus_get(path):
                            with self._guard:
                                if not is_current():
                                    raise NexusError("MO2 scan was superseded")
                            m = re.fullmatch(r"/v1/games/[a-z0-9_-]+/mods/([0-9]+)\.json", path)
                            if m is None:
                                raise NexusError("Unsupported native Nexus request")
                            native = self.call_ui("nexus-metadata",
                                {"game": game, "modId": int(m.group(1))}, timeout=70,
                                cancel_event=scan_cancel)
                            if not native.get("ok"):
                                raise NexusError(str(native.get("error") or "MO2 Nexus lookup failed"))
                            return native["meta"]
                        client = NexusClient("mo2-native", request_json=mo2_nexus_get)
                    else:
                        raise MissingAuth("Connect to Nexus in MO2 Settings > Nexus or use a fallback API key.")
                    return UpdateScanner(self.cache, client).scan(
                        snapshot, which, progress, cancel_event=scan_cancel), which

                def scan_sources():
                    with self._guard:
                        if self._source_scanner is None:
                            self._source_scanner = SourceScanner(cache_path=self._source_cache_path)
                        scanner = self._source_scanner
                    return scanner.scan(snapshot, progress, cancel_event=scan_cancel)
                effective_mode = mode
                if mode == "sources":
                    result = scan_sources()
                elif mode == "all":
                    nexus, coverage = scan_nexus("deep" if key or native_nexus else "local")
                    with self._guard:
                        if not is_current():
                            return
                    other = scan_sources()
                    result = {**nexus,
                        "itemRecords": {**nexus.get("itemRecords", {}), **other.get("itemRecords", {})},
                        "errors": list(nexus.get("errors", [])) + list(other.get("errors", [])),
                        "queried": int(nexus.get("queried", 0)) + int(other.get("queried", 0)),
                        "sourcesChecked": other.get("sourceProjects", 0),
                        "manualSources": other.get("manualSources", 0),
                        "sourceEngine": other.get("sourceEngine"), "adapterStats": other.get("adapterStats", {}),
                        "nexusCoverage": coverage, "mode": "all"}
                else:
                    result, effective_mode = scan_nexus(mode)
                with self._guard:
                    if not is_current():
                        return  # A superseded worker may never alter another scan's state.
                    from_source = {str(i.get("id")): i for i in snapshot.get("items", [])}
                    for item in self._snapshot.get("items", []):
                        item_key = str(item.get("id", ""))
                        scanned_item = from_source.get(item_key)
                        update = result.get("itemRecords", {}).get(item_key) or result["records"].get(str(item.get("nexusId") or 0))
                        if update:
                            item["update"] = dict(update)
                            source_url = canonical_source_url(((scanned_item or {}).get("source") or {}).get("url", ""))
                            if source_url != canonical_source_url((item.get("source") or {}).get("url", "")):
                                item["update"]["sourceUrl"] = source_url
                    self._scan = {"state": "partial" if result.get("errors") else "completed", "mode": effective_mode, **result, "checked": result.get("queried", 0), "total": result.get("queried", 0), "jobId": job_epoch}
                    # A full audit is required, following the latest verified install.
                    # Local / cached and quick activity checks cannot crown a profile.
                    self.chronicle.maybe_victory(snapshot["profileId"], self._snapshot, self._scan,
                        scanned_after_update=(base_sequence == self.chronicle.update_count(snapshot["profileId"])))
                    self._generation += 1
            except (MissingAuth, NexusError, ValueError, OSError) as ex:
                with self._guard:
                    if is_current():
                        self._scan = {"state": "error", "mode": mode, "error": str(ex), "checked": 0, "total": 0, "jobId": job_epoch}
            except Exception as ex:
                with self._guard:
                    if is_current():
                        self._scan = {"state": "error", "mode": mode, "error": "Unexpected scan failure: " + type(ex).__name__, "checked": 0, "total": 0, "jobId": job_epoch}
        threading.Thread(target=worker, name="ArsenalNexusScan", daemon=True).start()
        return {"ok": True, "state": "running"}

    def scan_state(self) -> dict:
        with self._guard:
            # Polling carries progress and aggregate evidence only. Full per-mod
            # release metadata lives behind the exact-row source-details API.
            return deepcopy({key: value for key, value in self._scan.items()
                             if key not in {"records", "itemRecords"}})

    def fetch_files(self, mod_id: int) -> dict:
        with self._guard:
            if mod_id <= 0 or mod_id not in self._nexus_ids:
                return {"ok": False, "error": "This Nexus ID is not associated with the active MO2 profile"}
            domain = self._snapshot.get("game", "skyrimspecialedition")
            profile = self._snapshot.get("profileId")
            epoch = self._file_epoch
            cached = self._file_cache.get((domain, mod_id))
            if cached:
                return deepcopy(cached)
            key = self.api_key
            native_nexus = self.native_nexus_available and self.native_nexus_files_available is not False
            if not key and not native_nexus:
                if self.native_nexus_available and self.native_nexus_files_error:
                    return {"ok": False, "error": "MO2 Nexus file list unavailable: " + self.native_nexus_files_error
                            + " Install the matching Modhunter MO2 package or supply an optional personal Nexus API key.", "mo2Login": True}
                return {"ok": False, "error": "Connect to Nexus in MO2 Settings > Nexus or supply a fallback key.", "mo2Login": True}
            identity = (profile, domain, mod_id, epoch)
            flight = self._file_inflight.get(identity)
            owner = flight is None
            if owner:
                flight = {"event": threading.Event(), "result": None}
                self._file_inflight[identity] = flight
        if owner:
            try:
                result = self._fetch_files_remote(mod_id, domain, key, native_nexus)
            except Exception as exc:
                result = {"ok": False, "error": "Nexus file lookup failed: " + type(exc).__name__}
            with self._guard:
                current = (self._snapshot.get("profileId"), self._snapshot.get("game", "skyrimspecialedition"),
                           mod_id, self._file_epoch)
                if current != identity or mod_id not in self._nexus_ids:
                    result = {"ok": False, "error": "MO2 profile or scan changed while loading files; refresh and review again"}
                elif result.get("ok"):
                    self._file_cache[(domain, mod_id)] = result
                flight["result"] = result
                self._file_inflight.pop(identity, None)
                flight["event"].set()
        elif not flight["event"].wait(75):
            return {"ok": False, "error": "Nexus file lookup is still pending; retry shortly"}
        with self._guard:
            if (self._snapshot.get("profileId") != profile or self._file_epoch != epoch
                    or mod_id not in self._nexus_ids):
                return {"ok": False, "error": "MO2 profile or scan changed while loading files; refresh and review again"}
            return deepcopy(flight["result"])

    def _fetch_files_remote(self, mod_id: int, domain: str, key: str, native_nexus: bool) -> dict:
        """One provider request shared by concurrent reviews of the same project."""
        try:
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
                    if fid > 0:
                        items.append({"fileId": fid, "name": str(file.get("name") or file.get("fileName") or file.get("file_name") or f"File {fid}")[:190], "version": str(file.get("version") or ""), "category": int(file.get("category", file.get("category_id", 0)) or 0), "sizeInKB": max(0, int(file.get("sizeInKB", file.get("size_kb", 0)) or 0)), "uploaded": str(file.get("uploaded_time") or ""), "fileName": str(file.get("fileName") or file.get("file_name") or "")[:190], "description": unescape(re.sub(r"<[^>]*>", " ", str(file.get("description") or "")))[:500].strip()})
                except (TypeError, ValueError, OverflowError):
                    continue
            items.sort(key=lambda f: (f["category"] != 1, -f["fileId"]))
            return {"ok": True, "files": items, "modId": mod_id}
        except (NexusError, MissingAuth, ValueError) as ex:
            return {"ok": False, "error": str(ex)}

    def review_files(self, mod_id: int, item_id: str) -> dict:
        """Decorate native Nexus files for one exact MO2 row, not shared projects."""
        snapshot = self.selection_snapshot([item_id])
        item = next((row for row in snapshot.get("items", [])
                     if str(row.get("id") or "") == item_id
                     and int(row.get("nexusId") or 0) == mod_id), None)
        if item is None:
            return {"ok": False, "error": "Selected file review does not belong to this MO2 profile"}
        response = self.fetch_files(mod_id)
        if not response.get("ok"):
            return response
        if self.selection_snapshot([item_id]) != snapshot:
            return {"ok": False, "error": "Selected mod changed while loading files; refresh and review again"}
        advisory = advise_nexus_files(item, response["files"])
        return {**response, **advisory, "itemId": item_id}

    def _start_native_download(self, command: str, native_payload: dict, ticket: dict) -> dict:
        """Register native IDs atomically with any completion signal received early."""
        with self._guard:
            native_payload = {**native_payload, "profileId": ticket.get("profileId"),
                              "itemId": ticket.get("itemId", ""),
                              "gameRuntime": ticket.get("gameRuntime"),
                              "installedVersion": ticket.get("installedVersion", "")}
            self._download_dispatches += 1
        result: dict = {"ok": False, "error": "MO2 did not return a download"}
        try:
            result = self.call_ui(command, native_payload)
            return result
        finally:
            with self._guard:
                self._download_dispatches -= 1
                if result.get("ok"):
                    try:
                        download_id = int(result.get("downloadId") or 0)
                    except (TypeError, ValueError, OverflowError):
                        download_id = 0
                    if download_id > 0:
                        ticket["status"] = self._early_download_events.pop(download_id, "downloading")
                        self._downloads[download_id] = ticket
                        self.journal.record(ticket, download_id)
                if not self._download_dispatches:
                    self._early_download_events.clear()

    def request_download(self, mod_id: int, file_id: int, confirmation: str,
                         item_id: str = "", installed_version: str = "",
                         *, reviewed_context: dict | None = None,
                         expected_execution_epoch: int | None = None) -> dict:
        if confirmation != "DOWNLOAD":
            return {"ok": False, "error": "Confirmation required"}
        with self._guard:
            profile = self.profile_id()
            execution_epoch = self._execution_epoch
            if expected_execution_epoch is not None and expected_execution_epoch != execution_epoch:
                return {"ok": False, "error": "MO2 context changed; refresh before downloading"}
        files = self.fetch_files(mod_id)
        if not files.get("ok") or file_id not in {int(f["fileId"]) for f in files["files"]}:
            return {"ok": False, "error": "The selected file must be verified in the Nexus Files list first"}
        with self._guard:
            if self._snapshot.get("profileId") != profile or self._execution_epoch != execution_epoch:
                return {"ok": False, "error": "MO2 profile changed while checking the download"}
            if reviewed_context is not None and not self.download_context_matches(reviewed_context):
                return {"ok": False, "error": "Reviewed release or compatibility changed; review the file again"}
            ticket = {"provider": "nexus", "modId": mod_id, "fileId": file_id,
                "operationId": secrets.token_hex(12), "itemId": item_id,
                "installedVersion": installed_version, "status": "downloading",
                "profileId": self._snapshot.get("profileId"),
                "profileName": self._snapshot.get("profileName", ""),
                "game": self._snapshot.get("game", ""),
                "gameRuntime": self._snapshot.get("gameRuntime", "unknown")}
        native = {"modId": mod_id, "fileId": file_id, "executionEpoch": execution_epoch}
        if reviewed_context is not None:
            native["reviewedContext"] = reviewed_context
        return self._start_native_download("download", native, ticket)

    @staticmethod
    def _download_context(snapshot: dict, item: dict) -> dict:
        """Small server-owned review identity carried through the Qt handoff."""
        source = item.get("source") or {}
        update = item.get("update") or {}
        return {"profileId": snapshot.get("profileId"), "game": snapshot.get("game"),
                "executionEpoch": snapshot.get("executionEpoch"),
                "gameRuntime": snapshot.get("gameRuntime"), "loader": snapshot.get("loader"),
                "itemId": str(item.get("id") or ""),
                "installedVersion": str(item.get("installedVersion") or ""),
                "nexusId": int(item.get("nexusId") or 0),
                "sourceUrl": str(source.get("url") or ""), "sourceVerified": source.get("verified") is True,
                "verdict": update.get("verdict"), "targetVersion": str(update.get("latestVersion") or ""),
                "risks": risk_reasons(item)}

    def download_context_matches(self, reviewed: dict) -> bool:
        """Revalidate immediately before a native download, even after Qt delay."""
        with self._guard:
            live = self.selection_snapshot([str(reviewed.get("itemId") or "")])
            rows = live.get("items", [])
            return bool(rows and not self._stopped and self._download_context(live, rows[0]) == reviewed)

    @staticmethod
    def _ticket_blocks_update(ticket: dict, item: dict | None) -> bool:
        if ticket.get("status") in ("failed", "removed"):
            return False
        # A completed installation remains history. After MO2 observes a new
        # installed version it must not permanently block the next release.
        if ticket.get("status") == "installed" and item is not None:
            return str(ticket.get("installedVersion") or "") == str(item.get("installedVersion") or "")
        return True

    def quick_update(self, payload: dict) -> dict:
        """One click: select a verified unique archive; no extra form or approval.

        A singular click can request MO2's native interactive installer when its
        download finishes. Bulk uses a background worker and downloads only;
        it never launches a storm of FOMOD windows or blocks the browser.
        """
        with self._guard:
            profile = self.profile_id()
            execution_epoch = self._execution_epoch
        if (not profile or payload.get("profileId") != profile
                or payload.get("executionEpoch", execution_epoch) != execution_epoch):
            return {"ok": False, "error": "MO2 profile changed; refresh and try again"}
        if payload.get("all") is True:
            snapshot = self.snapshot()
            if snapshot.get("profileId") != profile or snapshot.get("executionEpoch") != execution_epoch:
                return {"ok": False, "error": "MO2 profile changed; refresh and try again"}
            ids = [str(item["id"]) for item in snapshot.get("items", [])
                   if (item.get("update") or {}).get("verdict") == "candidate"]
            if not ids:
                return {"ok": True, "state": "completed", "queued": [], "skipped": [], "failed": [],
                        "message": "No verified update candidates yet. Run Check Sources first."}
            with self._guard:
                if snapshot.get("executionEpoch") != self._execution_epoch:
                    return {"ok": False, "error": "MO2 context changed; refresh and try again"}
                if self._quick_job.get("state") == "running":
                    return {"ok": True, **deepcopy(self._quick_job)}
                self._quick_job = {"state": "running", "profileId": profile, "total": len(ids),
                                   "completed": 0, "queued": [], "skipped": [], "failed": []}
                job = self._quick_job
            threading.Thread(target=self._quick_worker, args=(profile, ids, job, execution_epoch), daemon=True,
                             name="ModhunterQuickQueue").start()
            return {"ok": True, "state": "running", "profileId": profile, "total": len(ids),
                    "message": "Finding verified file matches and queueing downloads in MO2"}
        ids = payload.get("itemIds")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            return {"ok": False, "error": "Choose one installed mod or use all=true"}
        if not self._queue_lock.acquire(blocking=False):
            return {"ok": False, "error": "A download queue is already being prepared; retry shortly"}
        try:
            result = self._quick_one(profile, ids[0], auto_install=payload.get("autoInstall") is True,
                                     execution_epoch=execution_epoch)
        finally:
            self._queue_lock.release()
        if result.get("queued") and payload.get("autoInstall") is True:
            # Handle an exceptionally fast native download completion signal.
            self._maybe_autoinstall()
        return {"ok": True, "state": "completed", "profileId": profile,
                "queued": result.get("queued", []), "skipped": result.get("skipped", []),
                "failed": result.get("failed", []), "message": result.get("message", "")}

    def quick_state(self) -> dict:
        with self._guard:
            job = deepcopy(self._quick_job)
            profile = self._snapshot.get("profileId")
        if job.get("profileId") != profile:
            return {"ok": True, "state": "idle", "profileId": profile, "queued": [], "skipped": [], "failed": []}
        return {"ok": True, **job}

    def _quick_worker(self, profile: str, ids: list[str], job: dict, execution_epoch: int) -> None:
        try:
            for item_id in ids:
                with self._guard:
                    if self._stopped or self._quick_job is not job or self.profile_id() != profile:
                        job["skipped"].append({"itemId": item_id, "reason": "MO2 profile changed or session closed"})
                        break
                with self._queue_lock:
                    with self._guard:
                        if self._stopped or self._quick_job is not job or self.profile_id() != profile:
                            break
                    result = self._quick_one(profile, item_id, auto_install=False,
                                             execution_epoch=execution_epoch)
                with self._guard:
                    for key in ("queued", "skipped", "failed"):
                        job[key].extend(result.get(key, []))
                    job["completed"] += 1
        except Exception as exc:
            with self._guard:
                job["failed"].append({"error": "Update queue failed: " + type(exc).__name__})
        finally:
            with self._guard:
                job["state"] = "completed" if not self._stopped else "stopped"

    def _quick_one(self, profile: str, item_id: str, auto_install: bool,
                   *, execution_epoch: int | None = None) -> dict:
        live = self.selection_snapshot([item_id])
        if (live.get("profileId") != profile
                or execution_epoch is not None and live.get("executionEpoch") != execution_epoch):
            return {"skipped": [{"itemId": item_id, "reason": "The active MO2 profile changed"}]}
        item = next((m for m in live.get("items", []) if str(m.get("id")) == item_id), None)
        if not item or (item.get("update") or {}).get("verdict") != "candidate":
            return {"skipped": [{"itemId": item_id, "reason": "No verified newer release; choose a file manually"}]}
        with self._guard:
            if any(str(t.get("itemId")) == item_id and t.get("profileId") == profile
                   and self._ticket_blocks_update(t, item) for t in self._downloads.values()):
                return {"skipped": [{"itemId": item_id, "reason": "Already in MO2 Downloads"}]}
        update = item.get("update") or {}
        entry: dict | None = None
        reason = "Select the exact file to continue"
        try:
            nexus_id = int(item.get("nexusId") or 0)
        except (TypeError, ValueError, OverflowError):
            nexus_id = 0
        if update.get("source") == "nexus-api" and nexus_id > 0:
            found = self.review_files(nexus_id, item_id)
            if not found.get("ok"):
                return {"skipped": [{"itemId": item_id, "reason": str(found.get("error") or "Nexus file list unavailable")}]}
            picked, reason = choose_nexus_file(item, found.get("files", []))
            if picked:
                entry = {"itemId": item_id, "modId": nexus_id,
                         "fileId": int(picked["fileId"]), "installedVersion": str(item.get("installedVersion") or ""),
                         "targetVersion": str(update.get("latestVersion") or ""),
                         "reviewed": True, "riskAcknowledged": True}
        elif update.get("source") in {f"{provider}-release-api" for provider in PUBLIC_API_PROVIDERS}:
            picked, reason = choose_release_asset(item)
            if picked:
                entry = {"kind": "source", "itemId": item_id, "assetProof": picked["assetProof"],
                         **({"assetKey": picked["assetKey"]} if picked.get("assetKey") else {"assetId": int(picked["assetId"])}),
                         "installedVersion": str(item.get("installedVersion") or ""),
                         "targetVersion": str(update.get("latestVersion") or ""),
                         "reviewed": True, "riskAcknowledged": True}
        if entry is None:
            return {"skipped": [{"itemId": item_id, "reason": reason}]}
        result = self._queue_reviewed_downloads_once({
            "profileId": profile, "executionEpoch": live["executionEpoch"],
            "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": [entry]})
        if result.get("queued"):
            with self._guard:
                for queued in result["queued"]:
                    ticket = self._downloads.get(int(queued["downloadId"]))
                    if ticket:
                        ticket["selectionMode"] = "verified-match"
                        ticket["autoInstallRequested"] = auto_install
                        self.journal.record(ticket, int(queued["downloadId"]))
        if not result.get("ok"):
            failed = result.get("failed") or [{"itemId": item_id, "error": result.get("error", "Queue rejected")}]
            return {"failed": failed, "message": "Select an exact file if this recommendation is unavailable"}
        return result

    def _maybe_autoinstall(self) -> None:
        """Drain explicitly armed tickets sequentially, without recursive retries."""
        if self._stopped or self._install_lock.locked():
            return
        while not self._stopped and not self._install_lock.locked():
            with self._guard:
                profile = self._snapshot.get("profileId")
                selected = next(((did, ticket) for did, ticket in self._downloads.items()
                                 if ticket.get("profileId") == profile
                                 and ticket.get("status") == "complete"
                                 and ticket.get("autoInstallRequested") is True), None)
                if selected is None:
                    return
                did, ticket = selected
                ticket["autoInstallRequested"] = False
            result = self.install_reviewed_download(did)
            if result.get("ok"):
                return  # The native worker drains the next ticket when MO2 finishes.
            if "already open" in str(result.get("error") or ""):
                with self._guard:
                    if self._downloads.get(did) is ticket:
                        ticket["autoInstallRequested"] = True
                return
            with self._guard:
                if self._downloads.get(did) is ticket:
                    ticket["lastError"] = str(result.get("error") or "Review this archive in MO2")[:240]
                    self.journal.record(ticket, did)

    def install_ready_queue(self) -> dict:
        """One click arms all completed, valid downloads for sequential MO2 dialogs.

        Each native installer remains interactive. Nothing is installed while the
        user is away, and a changed profile/runtime/version fails a live preflight.
        """
        profile = self.profile_id()
        armed, blocked = [], []
        with self._guard:
            for did, ticket in self._downloads.items():
                if ticket.get("profileId") != profile or ticket.get("status") != "complete":
                    continue
                brief = build_install_brief(self.selection_snapshot([str(ticket.get("itemId") or "")]), deepcopy(ticket), did)
                if brief.get("canInstall"):
                    ticket["autoInstallRequested"] = True
                    armed.append(did)
                else:
                    blocked.append({"downloadId": did, "reason": "; ".join(brief.get("errors") or ["Recheck this update"])})
        self._maybe_autoinstall()
        return {"ok": True, "armed": len(armed), "blocked": blocked,
                "state": "installing" if self._install_lock.locked() else "idle",
                "message": (f"Opening {len(armed)} ready MO2 installer(s), one at a time."
                            if armed else "No ready downloads passed installation checks.")}

    def retry_failed_download(self, download_id: int) -> dict:
        """Requeue the same previously reviewed archive with no approval screens."""
        if isinstance(download_id, bool) or not isinstance(download_id, int) or download_id <= 0:
            return {"ok": False, "error": "Invalid download ID"}
        with self._guard:
            old = deepcopy(self._downloads.get(download_id))
        snapshot = self.selection_snapshot([str((old or {}).get("itemId") or "")])
        if not old or old.get("profileId") != snapshot.get("profileId") or old.get("status") != "failed":
            return {"ok": False, "error": "Only failed downloads from the active MO2 profile can be retried"}
        item_id = str(old.get("itemId") or "")
        mod = next((m for m in snapshot.get("items", []) if str(m.get("id")) == item_id), None)
        if (not mod or (mod.get("update") or {}).get("verdict") != "candidate"
                or str(mod.get("installedVersion") or "") != str(old.get("installedVersion") or "")
                or str((mod.get("update") or {}).get("latestVersion") or "") != str(old.get("targetVersion") or "")
                or risk_reasons(mod) != old.get("riskReasons")):
            return {"ok": False, "error": "Mod, release, or compatibility changed. Choose the correct file again."}
        base = {"itemId": item_id, "installedVersion": old["installedVersion"],
                "targetVersion": old["targetVersion"], "reviewed": True, "riskAcknowledged": True,
                "autoInstall": old.get("autoInstallRequested") is True or
                               old.get("selectionMode") == "verified-match"}
        if old.get("provider") == "nexus":
            if int(mod.get("nexusId") or 0) != int(old.get("modId") or 0) or not old.get("fileId"):
                return {"ok": False, "error": "Source identity changed. Choose the file again."}
            entry = {**base, "modId": int(old["modId"]), "fileId": int(old["fileId"])}
        elif old.get("provider") in PUBLIC_API_PROVIDERS:
            if (mod.get("update") or {}).get("source") != f"{old['provider']}-release-api" or not (old.get("assetKey") or old.get("assetId")):
                return {"ok": False, "error": "Source release changed. Choose the file again."}
            entry = {**base, "kind": "source", "assetProof": old.get("assetProof"),
                     **({"assetKey": old["assetKey"]} if old.get("assetKey") else {"assetId": int(old["assetId"])})}
        else:
            return {"ok": False, "error": "Source unavailable. Choose another file."}
        result = self.queue_reviewed_downloads({"profileId": snapshot["profileId"],
                                                "executionEpoch": snapshot["executionEpoch"],
                                                "confirm": "QUEUE_REVIEWED_DOWNLOADS",
                                                "entries": [entry]})
        if result.get("queued"):
            with self._guard:
                recorded = self._downloads.get(download_id)
                if recorded and recorded.get("status") == "failed":
                    recorded["retriedBy"] = int(result["queued"][0]["downloadId"])
        return result

    def queue_reviewed_downloads(self, payload: dict) -> dict:
        """Serialize reviewed batches so concurrent clicks cannot enqueue duplicates."""
        with self._guard:
            epoch = self._execution_epoch
            if payload.get("executionEpoch", epoch) != epoch:
                return {"ok": False, "error": "MO2 context changed; refresh and review the files again",
                        "queued": [], "failed": []}
            payload = {**payload, "executionEpoch": epoch}
        with self._queue_lock:
            result = self._queue_reviewed_downloads_once(payload)
        # An instantly completed archive can already be ready by the time the
        # native queue API returns. Do not discard the user's install intent.
        self._maybe_autoinstall()
        return result

    def _queue_reviewed_downloads_once(self, payload: dict) -> dict:
        """Preflight a complete Nexus + public-provider release queue before ANY downloads.

        One reviewed queue can contain both sources. It never overwrites mods,
        never auto-installs, and reports every attempted native download.
        """
        raw = payload.get("entries")
        # Validate a batch against exactly its selected rows; a one-click queue
        # must not repeatedly copy tens of thousands of unrelated mods.
        selected_ids = [str(x.get("itemId")) for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
        snapshot = self.selection_snapshot(selected_ids)
        reviewed_items = {str(item.get("id")): item for item in snapshot.get("items", [])}
        if (payload.get("profileId") != snapshot.get("profileId") or
                payload.get("executionEpoch", snapshot["executionEpoch"]) != snapshot["executionEpoch"] or
                payload.get("confirm") != "QUEUE_REVIEWED_DOWNLOADS" or
                not isinstance(raw, list) or not raw):
            return {"ok": False, "error": "Active profile, reviewed entries and confirmation required", "queued": [], "failed": []}
        nexus_entries = [x for x in raw if isinstance(x, dict) and x.get("kind") != "source"]
        source_entries = [x for x in raw if isinstance(x, dict) and x.get("kind") == "source"]
        user_requested_native_install = {str(x.get("itemId")) for x in raw
                                         if isinstance(x, dict) and x.get("autoInstall") is True}
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
        unique_sources, skipped = {}, []
        for item in sources:
            archive = (item["provider"], item["url"])
            if archive in unique_sources:
                skipped.append({"itemId": item["itemId"], "reason": "The same exact provider archive is already selected in this batch"})
            else:
                unique_sources[archive] = item
        sources = list(unique_sources.values())
        # A queued, completed, or installed archive belongs to this profile
        # until it is removed or a different installed version is scanned.
        with self._guard:
            already_queued = {str(ticket.get("itemId")) for ticket in self._downloads.values()
                              if ticket.get("profileId") == snapshot.get("profileId")
                              and ticket.get("itemId")
                              and self._ticket_blocks_update(ticket, self._items_by_id.get(str(ticket.get("itemId"))))}
            queued_archives = {(ticket.get("provider"), ticket.get("archiveUrl")) for ticket in self._downloads.values()
                               if ticket.get("profileId") == snapshot.get("profileId") and ticket.get("archiveUrl")
                               and self._ticket_blocks_update(ticket, self._items_by_id.get(str(ticket.get("itemId"))))}
        for item in approved + sources:
            if item["itemId"] in already_queued:
                return {"ok": False, "queued": [], "failed": [],
                        "error": "An update for this MO2 mod is already queued. Finish its download or installation first."}
        if any((item["provider"], item["url"]) in queued_archives for item in sources):
            return {"ok": False, "queued": [], "failed": [],
                    "error": "This exact provider archive is already in MO2 Downloads for the active profile."}
        # Preflight ALL Nexus file identities. Public assets were validated
        # against server-owned release results above, never client URLs.
        exact_files: dict[tuple[int, int], dict] = {}
        for item in approved:
            available = self.fetch_files(item["modId"])
            if not available.get("ok"):
                return {"ok": False, "queued": [], "failed": [{"itemId": item["itemId"],
                        "error": available.get("error", "Nexus file list unavailable")}]}
            matched = next((f for f in available.get("files", [])
                            if int(f.get("fileId") or 0) == item["fileId"]), None)
            if not matched:
                return {"ok": False, "queued": [], "failed": [{"itemId": item["itemId"],
                        "error": "Exact Nexus file is no longer in the live Files list"}]}
            exact_files[(item["modId"], item["fileId"])] = matched
        queued, failed = [], []
        for item in approved + sources:
            live = self.selection_snapshot([item["itemId"]])
            current = next((x for x in live.get("items", []) if x.get("id") == item["itemId"]), None)
            reviewed_context = self._download_context(snapshot, reviewed_items[item["itemId"]])
            same = bool(live.get("profileId") == snapshot.get("profileId") and current is not None
                        and str(current.get("installedVersion") or "") == item["installedVersion"]
                        and (current.get("update") or {}).get("verdict") == "candidate"
                        and self._download_context(live, current) == reviewed_context)
            if not same:
                failed.append({"itemId": item["itemId"], "error": "Profile, reviewed release, or compatibility changed"})
                break
            if item.get("kind") == "source":
                try:
                    renewed = validate_release_asset(live, {
                        "kind": "source", "itemId": item["itemId"],
                        **({"assetKey": item["assetKey"]} if item.get("assetKey") else {"assetId": item["assetId"]}),
                        "assetProof": item["assetProof"], "installedVersion": item["installedVersion"],
                        "targetVersion": item["targetVersion"], "reviewed": True, "riskAcknowledged": True})
                except PlanError as exc:
                    failed.append({"itemId": item["itemId"], "error": str(exc)})
                    break
                ticket = {
                    "provider": renewed["provider"], "name": renewed["name"],
                    "assetKey": renewed["assetKey"], "assetId": renewed["assetId"], "assetProof": renewed["assetProof"],
                    "archiveUrl": renewed["url"], "sourceUrl": renewed["sourceUrl"],
                    "projectId": renewed["projectId"], "releaseId": renewed["releaseId"],
                    "operationId": secrets.token_hex(12),
                    "itemId": item["itemId"], "installedVersion": item["installedVersion"],
                    "status": "downloading", "profileId": live.get("profileId"),
                    "gameRuntime": live.get("gameRuntime", "unknown")}
                result = self._start_native_download("download-url", {**renewed, "reviewed": True,
                    "riskAcknowledged": True, "reviewedContext": reviewed_context,
                    "executionEpoch": reviewed_context["executionEpoch"]}, ticket)
            else:
                if int(current.get("nexusId") or 0) != item["modId"]:
                    failed.append({"itemId": item["itemId"], "error": "Nexus project changed"})
                    break
                result = self.request_download(item["modId"], item["fileId"], "DOWNLOAD",
                                               item["itemId"], item["installedVersion"],
                                               reviewed_context=reviewed_context)
            if result.get("ok"):
                with self._guard:
                    selected_row = current or {}
                    live_ticket = self._downloads.get(int(result["downloadId"]))
                    if live_ticket:
                        metadata = exact_files.get((item.get("modId"), item.get("fileId")), {})
                        live_ticket.update({
                            "itemId": item["itemId"], "installedVersion": item["installedVersion"],
                            "targetVersion": str((selected_row.get("update") or {}).get("latestVersion") or ""),
                            "profileName": str(live.get("profileName") or ""),
                            "game": str(live.get("game") or ""),
                            "gameRuntime": str(live.get("gameRuntime") or "unknown"),
                            "riskReasons": risk_reasons(selected_row), "reviewed": True,
                            "autoInstallRequested": item["itemId"] in user_requested_native_install,
                            "fileName": metadata.get("fileName") or metadata.get("name") or item.get("name") or "",
                            "fileVersion": str(metadata.get("version") or ""),
                        })
                        if item.get("kind") == "source":
                            live_ticket["assetId"] = item.get("assetId")
                            live_ticket["assetKey"] = item.get("assetKey")
                            live_ticket["assetProof"] = item.get("assetProof")
                            live_ticket["fileName"] = item.get("name") or live_ticket.get("name") or ""
                with self._guard:
                    receipt = self._downloads.get(int(result["downloadId"]))
                    if receipt:
                        receipt["modName"] = str(selected_row.get("name") or receipt.get("name") or "")[:220]
                        self.journal.record(receipt, int(result["downloadId"]))
                queued.append({"itemId": item["itemId"], "provider": item.get("provider", "nexus"),
                               "downloadId": result.get("downloadId")})
            else:
                failed.append({"itemId": item["itemId"], "error": str(result.get("error") or "Download rejected")})
        return {"ok": True, "partial": bool(failed), "queued": queued, "failed": failed, "skipped": skipped,
                "message": f"Queued {len(queued)} reviewed archive(s). Install each separately in MO2."}

    def set_download_status(self, download_id: int, state: str) -> None:
        if state not in ("complete", "downloading", "paused", "failed", "removed"):
            return
        with self._guard:
            if download_id in self._downloads:
                # An old callback cannot reactivate an installing or installed archive.
                if self._downloads[download_id]["status"] not in ("installing", "installed"):
                    self._downloads[download_id]["status"] = state
                    self.journal.record(self._downloads[download_id], download_id)
            elif self._download_dispatches and isinstance(download_id, int) and download_id > 0:
                # Early native callback raced ahead of startDownload's return ID.
                self._early_download_events[download_id] = state
        if state == "complete":
            self._maybe_autoinstall()

    def downloads(self) -> dict:
        with self._guard:
            return deepcopy({k:v for k,v in self._downloads.items() if v.get("profileId")==self._snapshot.get("profileId")})

    def activity(self) -> dict:
        """Read-only operational history; previous-session receipts never install."""
        with self._guard:
            profile_id = str(self._snapshot.get("profileId") or "")
        return self.journal.profile(profile_id)

    def preview_install(self, download_id: int) -> dict:
        """The browser sees only an immutable, profile-scoped advisory briefing."""
        with self._guard:
            ticket = deepcopy(self._downloads.get(download_id))
        return build_install_brief(self.selection_snapshot([str((ticket or {}).get("itemId") or "")]), ticket, download_id)

    def install_reviewed_download(self, download_id: int) -> dict:
        """Start one interactive MO2 installer without blocking the browser.

        An unfinished native dialog is a normal in-progress state, not an HTTP
        timeout. Keep the global install lease until the Qt callback actually
        finishes, then publish the verified receipt to the download ticket.
        """
        if isinstance(download_id, bool) or download_id <= 0:
            return {"ok": False, "error": "Invalid MO2 download ID"}
        if not self._install_lock.acquire(blocking=False):
            return {"ok": False, "error": "Another MO2 installer is already open"}
        started = False
        try:
            with self._guard:
                ticket = self._downloads.get(download_id)
                if not ticket or ticket.get("profileId") != self._snapshot.get("profileId"):
                    return {"ok": False, "error": "Download does not belong to this active MO2 profile"}
                preflight = build_install_brief(self.selection_snapshot([str(ticket.get("itemId") or "")]), ticket, download_id)
                if not preflight["canInstall"]:
                    return {"ok": False, "error": "; ".join(preflight["errors"])}
                ticket["status"] = "installing"
                execution_epoch = self._execution_epoch
                ticket.pop("lastError", None)
                self.journal.record(ticket, download_id)
            threading.Thread(target=self._complete_native_install,
                             args=(download_id, ticket, execution_epoch), daemon=True,
                             name=f"ArsenalInstall-{download_id}").start()
            started = True
            return {"ok": True, "state": "installing", "downloadId": download_id,
                    "message": "MO2 installer requested. Finish its normal prompts; Modhunter will show the result."}
        except (RuntimeError, OSError) as exc:
            with self._guard:
                if self._downloads.get(download_id) is ticket:
                    ticket["status"] = "complete"
            return {"ok": False, "error": "Could not open MO2 installer: " + str(exc)[:160]}
        finally:
            if not started:
                self._install_lock.release()

    def _complete_native_install(self, download_id: int, ticket: dict, execution_epoch: int) -> None:
        """Wait on a daemon worker, never the HTTP thread or MO2 GUI thread."""
        try:
            request = UIRequest("install", {"downloadId": download_id,
                                "executionEpoch": execution_epoch,
                                "profileId": ticket.get("profileId"),
                                "gameRuntime": ticket.get("gameRuntime"),
                                "itemId": ticket.get("itemId", ""),
                                "installedVersion": ticket.get("installedVersion", "")})
            self._submit_ui(request)  # Qt signal delivers the request to MO2's GUI.
            request.event.wait()  # No deadline while the real FOMOD dialog is open.
            result = request.result
        except Exception as exc:
            result = {"ok": False, "error": "MO2 installer dispatch failed: " + type(exc).__name__}
        try:
            with self._guard:
                if self._downloads.get(download_id) is ticket:
                    if result.get("ok"):
                        ticket["status"] = "installed"
                        ticket["verifiedUpdate"] = result.get("verifiedUpdate") is True
                        ticket["installedName"] = str(result.get("installedName") or "")
                        ticket["backupPath"] = str((result.get("backup") or {}).get("backupPath") or "")
                    else:
                        # Cancellation keeps the reviewed archive retryable.
                        ticket["status"] = "complete"
                        ticket["lastError"] = str(result.get("error") or "MO2 installer canceled")[:240]
                        # A canceled FOMOD can still have created the profile
                        # text backup. Keep the evidence, never its path in history.
                        ticket["backupPath"] = str((result.get("backup") or {}).get("backupPath") or ticket.get("backupPath") or "")
                    self.journal.record(ticket, download_id)
        finally:
            self._install_lock.release()
            self._maybe_autoinstall()

    def start(self) -> str:
        if self._server:
            self._publish_discovery()
            return f"http://127.0.0.1:{self._server.server_port}/"
        self._stopped = False
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        srv.daemon_threads = True
        self._server = srv
        self._thread = threading.Thread(target=srv.serve_forever, daemon=True, name="ArsenalLoopbackHTTP")
        self._thread.start()
        self._publish_discovery()
        self.intelligence.auto_audit()
        return f"http://127.0.0.1:{srv.server_port}/"

    def stop(self) -> None:
        with self._guard:
            self._stopped = True
            self._snapshot_payload_cache = None
            self._execution_epoch += 1
            self._scan_epoch += 1
            self._scan_cancel.set()
            self.intelligence.invalidate(reason="MO2 session closed")
            if self._scan.get("state") == "running":
                self._scan = {"state": "idle", "reason": "MO2 session closed"}
            pending = list(self._pending_ui)
        for request in pending:
            if request.cmd in ("nexus-metadata", "nexus-files"):
                request.cancel()
            else:
                request.cancel_if_pending()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._discovery.close()

    def handler(self):
        service = self
        class Handler(BaseHTTPRequestHandler):
            server_version = "Modhunter/0.9"
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
                payload = obj if isinstance(obj, bytes) else json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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
                if uri.path == "/api/health":
                    # Native Rust clients probe a tiny identity document, not
                    # the multi-megabyte HTML. No auth tokens/paths are exposed.
                    return self._reply(service.health())
                if uri.path == "/":
                    boot = json.dumps({"active": True, "token": service.token}, ensure_ascii=False).replace("<", "\\u003c")
                    html = service.html.replace("<!--__MO2_BOOT__-->", f"<script>window.__MO2_BRIDGE_BOOT__={boot}</script>")
                    output = html.encode("utf-8")
                    self._headers("text/html; charset=utf-8", len(output))
                    return self.wfile.write(output)
                if not self._authorize():
                    return
                if uri.path == "/api/snapshot":
                    return self._reply(service.snapshot_response_bytes())
                if uri.path == "/api/revision":
                    return self._reply(service.revision())
                if uri.path == "/api/plugin-audit":
                    return self._reply(service.intelligence.state())
                if uri.path == "/api/mod-health":
                    query = urllib.parse.parse_qs(uri.query)
                    return self._reply(service.intelligence.health_state(
                        query.get("itemId", [""])[0], query.get("profileId", [""])[0]))
                if uri.path == "/api/siege/targets":
                    return self._reply(service.siege_targets())
                if uri.path == "/api/siege/bounties":
                    return self._reply(service.chronicle.boss_bounties(service.profile_id()))
                if uri.path == "/api/siege/state":
                    return self._reply(service.chronicle.siege_state(service.profile_id()))
                if uri.path == "/api/realm":
                    return self._reply(service.chronicle.realm_wallet(service.profile_id()))
                if uri.path == "/api/realm/world":
                    return self._reply(service.chronicle.realm_world(service.profile_id()))
                if uri.path == "/api/game":
                    compact = urllib.parse.parse_qs(uri.query).get("compact") == ["1"]
                    return self._reply(service.chronicle.overview(service.profile_id(), include_progress=not compact))
                if uri.path == "/api/progress":
                    profile = service.profile_id()
                    return self._reply({"ok": True, "profileId": profile, "progress": service.chronicle.progress(profile)})
                if uri.path == "/api/scan":
                    with service._guard:
                        result = {"ok": True, "profileId": service.profile_id(), "scan": service.scan_state()}
                    return self._reply(result)
                if uri.path == "/api/source-details":
                    query = urllib.parse.parse_qs(uri.query)
                    return self._reply(service.source_details(query.get("itemId", [""])[0], query.get("profileId", [""])[0]))
                if uri.path == "/api/update-plan":
                    detailed = urllib.parse.parse_qs(uri.query).get("details") == ["full"]
                    return self._reply(plan_updates(service.snapshot(source_details=detailed)))
                if uri.path == "/api/downloads":
                    with service._guard:
                        result = {"ok": True, "profileId": service.profile_id(), "downloads": service.downloads()}
                    return self._reply(result)
                if uri.path == "/api/quick-state":
                    return self._reply(service.quick_state())
                if uri.path == "/api/activity":
                    return self._reply(service.activity())
                if uri.path == "/api/install-preview":
                    query = urllib.parse.parse_qs(uri.query)
                    try:
                        did = int(query.get("downloadId", ["0"])[0])
                    except (ValueError, TypeError):
                        did = 0
                    return self._reply(service.preview_install(did))
                if uri.path == "/api/files":
                    query = urllib.parse.parse_qs(uri.query)
                    try:
                        mid = int(query.get("modId", ["0"])[0])
                    except (ValueError, TypeError):
                        mid = 0
                    item_id = query.get("itemId", [""])[0]
                    return self._reply(service.review_files(mid, item_id) if item_id else service.fetch_files(mid))
                if uri.path == "/api/capabilities":
                    return self._reply({"ok": True, "app": "MO2 Update Arsenal", "live": True, "nexusKeySession": bool(service.api_key), "nativeNexusAvailable": bool(service.native_nexus_available), "nativeNexusFilesAvailable": bool(service.native_nexus_available and service.native_nexus_files_available is not False), "nativeNexusFilesError": service.native_nexus_files_error, "keyStorage": "RAM only", "features": ["rust-plugin-audit", "eslifier-provenance", "per-mod-health", "nexus-public-bugs", "master-order-checks", "snapshot", "local-check", "nexus-quick", "nexus-deep", "nexus-files", "native-mo2-nexus-login", "github-release-checks", "gitlab-release-checks", "rust-site-adapters", "modrinth-release-checks", "thunderstore-release-checks", "source-details", "provider-capabilities", "manual-source-review", "github-direct-downloads", "reviewed-provider-downloads", "mixed-source-batch-updates", "reviewed-update-workshop", "one-click-matched-update", "background-bulk-queue", "mo2-download", "mo2-install", "install-preview", "operation-journal", "read-only-recovery-history", "backup", "focus-mod", "chronicle", "verified-achievements", "ranked-full-clear", "persistent-profile-progress", "realm-wallet", "realm-purchase", "realm-save", "siege-targets", "siege-state", "siege-bounties", "siege-mod-focus"]})
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
                if uri.path == "/api/plugin-audit":
                    return self._reply(service.intelligence.start_audit(
                        str(data.get("profileId") or ""), deep=data.get("deep", True) is True))
                if uri.path == "/api/mod-health":
                    return self._reply(service.intelligence.start_health(
                        str(data.get("itemId") or ""), str(data.get("profileId") or ""),
                        force=data.get("force") is True))
                if uri.path == "/api/eslifier-data":
                    return self._reply(service.call_ui("eslifier-data", {
                        "profileId": str(data.get("profileId") or "")}, timeout=180))
                if uri.path == "/api/siege/boss/defeated":
                    return self._reply(service.siege_boss_defeated(str(data.get("profileId") or ""),
                        str(data.get("modId") or ""), str(data.get("runId") or "")))
                if uri.path == "/api/siege/state":
                    profile = service.profile_id()
                    if data.get("profileId") != profile:
                        return self._reply({"ok": False, "error": "Profile changed while saving Siege"}, 409)
                    try:
                        revision = int(data.get("revision", -1))
                    except (TypeError, ValueError):
                        return self._reply({"ok": False, "error": "Invalid siege revision"}, 400)
                    return self._reply(service.chronicle.save_siege_state(profile, data.get("state"), revision))
                if uri.path == "/api/realm/purchase":
                    profile = service.profile_id()
                    if data.get("profileId") != profile:
                        return self._reply({"ok": False, "error": "Profile changed while spending Realm currency"}, 409)
                    return self._reply(service.chronicle.purchase_realm(profile, str(data.get("sku", "")), str(data.get("txn", ""))))
                if uri.path == "/api/realm/world":
                    profile = service.profile_id()
                    if data.get("profileId") != profile:
                        return self._reply({"ok": False, "error": "Profile changed while saving the world"}, 409)
                    try:
                        revision = int(data.get("revision", -1))
                    except (TypeError, ValueError):
                        return self._reply({"ok": False, "error": "Invalid world revision"}, 400)
                    return self._reply(service.chronicle.save_realm_world(profile, data.get("world"), revision))
                if uri.path == "/api/focus":
                    return self._reply(service.focus_mod(str(data.get("id") or ""), str(data.get("profileId") or "")))
                if uri.path == "/api/progress":
                    result = service.save_progress(data)
                    return self._reply(result, 409 if result.get("profileChanged") else 200)
                if uri.path == "/api/victory/ack":
                    profile = service.profile_id()
                    if data.get("profileId") != profile:
                        return self._reply({"ok": False, "error": "Profile changed while acknowledging victory"}, 409)
                    return self._reply(service.chronicle.ack_victory(profile, str(data.get("victoryId") or "")))
                if uri.path == "/api/refresh":
                    return self._reply(service.call_ui("refresh", {}))
                if uri.path == "/api/runtime":
                    return self._reply(service.call_ui("runtime", {}, timeout=180))
                if uri.path == "/api/scan":
                    return self._reply(service.begin_scan(data.get("mode", "quick"), data.get("profileId")))
                if uri.path == "/api/update-queue":
                    return self._reply(service.queue_reviewed_downloads(data))
                if uri.path == "/api/install-ready":
                    return self._reply(service.install_ready_queue())
                if uri.path == "/api/retry-download":
                    try:
                        download_id = int(data.get("downloadId") or 0)
                    except (ValueError, TypeError, OverflowError):
                        download_id = 0
                    return self._reply(service.retry_failed_download(download_id))
                if uri.path == "/api/quick-update":
                    return self._reply(service.quick_update(data))
                if uri.path == "/api/download":
                    try:
                        result = service.request_download(int(data.get("modId") or 0), int(data.get("fileId") or 0),
                            data.get("confirm"), expected_execution_epoch=data.get("executionEpoch"))
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
