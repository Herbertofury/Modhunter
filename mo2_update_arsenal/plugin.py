"""MO2 2.5.x Python Tool plugin. Privileged mobase calls run on MO2's UI thread."""
from __future__ import annotations

import json
import logging
import time
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import mobase
from PyQt6 import QtCore, QtGui, QtWidgets

from .bridge import ArsenalService, UIRequest
from .agent_control import set_enabled
from .install_policy import protected_installs, protected_refusal
from .model import LocalMetadataCache, call_str, take_snapshot, focus_identity
from .refresh_work import RefreshMetadataCache, RefreshWork
from .snapshot_projection import begin_snapshot_capture, capture_snapshot, project_snapshot
from .focus import navigate, find_mo2_view, name_column
from .scanner import version_verdict
from .native_nexus import NativeNexusGateway
from .plugin_intelligence import attach_native_plugin_state, build_audit_request
from .version import VERSION
from .download_ids import download_id as parse_download_id
from .native_downloads import DownloadDirectoryIndex, archive_receipt, download_model, ready_receipt, verified_path
from .content_recovery import NativeContent

LOG = logging.getLogger(__name__)


class Dispatcher(QtCore.QObject):
    submitted = QtCore.pyqtSignal(object)

    def __init__(self, handler):
        super().__init__()
        self.submitted.connect(handler, QtCore.Qt.ConnectionType.QueuedConnection)


class ModUpdateArsenal(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self.org = None
        self.server = None
        self.dispatcher = None
        self._pending_refresh = None
        self._background_refresh = None
        self._refresh_completion = None
        self._background_dirty = False
        self._deferred_content_leases = set()
        self._runtime = "unknown"
        self._download_check_pending = False
        self._download_check_offset = 0
        self._download_directory_index = None
        self._selection_connected = False
        self._focus_navigating = False
        self._native_nexus = None
        self._native_nexus_error = ''
        self._metadata_cache = LocalMetadataCache()
        self._ui_initialized = False
        self._closing = False
        self._startup_attempts = 0
        self._startup_inflight = False
        self._startup_invalidated = False
        self._startup_timer = None
        self._startup_open = False
        self._subscription_errors = []
        self._callbacks = []
        self._snapshot_error = ""
        self._snapshot_ms = 0.0
        self._content_native = NativeContent(self)
        self._content_launch_guard = False

    def name(self) -> str:
        return "MO2 Update Arsenal"

    def isActive(self) -> bool:
        return True

    def author(self) -> str:
        return "Herberto / MO2 Update Arsenal"

    def description(self) -> str:
        return "Modhunter: automatic update checks, plugin maintenance evidence, reviewed downloads and your live MO2 profile."

    def version(self):
        return mobase.VersionInfo(VERSION)

    def settings(self):
        return [mobase.PluginSetting("agent_mod_control_scope",
            "Agent enable/disable permissions as JSON mapping each profile ID to exact MO2 internal mod names.",
            "{}")]

    def displayName(self) -> str:
        return "Update Arsenal - Quest Board"

    def tooltip(self) -> str:
        return "Launch the local Arsenal dashboard for your active profile."

    def icon(self):
        return QtGui.QIcon()

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self.org = organizer
        self.dispatcher = Dispatcher(self._dispatch)
        # An independent queued Qt signal carries completed pure snapshot data
        # back to the same owning thread. No organizer calls cross the thread.
        self._refresh_completion = Dispatcher(self._on_background_refresh_completed)
        background_cache = RefreshMetadataCache()
        def project(captured, force, cancel):
            inputs, started = captured
            return (project_snapshot(inputs, metadata_cache=background_cache,
                                     force_metadata=force, cancel_event=cancel), started)
        self._background_refresh = RefreshWork(
            context=self._background_context, capture=self._capture_background_refresh,
            project=project, publish=self._publish_background_refresh,
            deliver=self._refresh_completion.submitted.emit,
            begin_capture=self._begin_background_capture,
            schedule_capture=lambda callback: QtCore.QTimer.singleShot(0, callback))
        try:
            self._native_nexus = NativeNexusGateway(organizer)
        except Exception as exc:
            self._native_nexus_error = str(exc)
        # Explicit user control for the runtime. Unknown is always safer than silently guessing.
        try:
            self._runtime = str(organizer.persistent(self.name(), "target-runtime", "unknown"))
        except Exception:
            self._runtime = "unknown"
        # Keep independent subscriptions independent. A missing optional callback
        # must not silently disable all download completion/failure notifications.
        self._subscribe(organizer, "onProfileChanged", self._on_profile_changed)
        self._content_launch_guard = self._subscribe(organizer, "onAboutToRun", self._on_about_to_run)
        self._subscribe(organizer, "onFinishedRun", self._on_finished_run)
        for factory, hooks in (
            (organizer.modList, (("onModInstalled", self._on_mod_installed),
                                ("onModRemoved", self._on_mod_removed),
                                ("onModMoved", self._on_mod_moved),
                                ("onModStateChanged", self._on_mod_states_changed))),
            (organizer.pluginList, (("onRefreshed", self._on_plugins_refreshed),
                                   ("onPluginMoved", self._on_mod_moved),
                                   ("onPluginStateChanged", self._on_plugin_states_changed))),
            (organizer.downloadManager, (("onDownloadComplete", self._on_download_complete),
                                        ("onDownloadFailed", self._on_download_failed),
                                        ("onDownloadPaused", self._on_download_paused),
                                        ("onDownloadRemoved", self._on_download_removed))),
        ):
            try:
                owner = factory()
            except Exception as exc:
                self._subscription_errors.append(getattr(factory, "__name__", "native interface") + ": " + type(exc).__name__)
                continue
            for name, callback in hooks:
                self._subscribe(owner, name, callback)
        self._subscribe(organizer, "onUserInterfaceInitialized", self._on_ui_initialized)
        app = QtCore.QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)
        return True

    def _subscribe(self, owner, name, callback):
        try:
            if getattr(owner, name)(callback) is False:
                raise RuntimeError("callback registration declined")
            self._callbacks.append(callback)
            return True
        except Exception as exc:
            self._subscription_errors.append(name + ": " + type(exc).__name__)
            LOG.warning(f"Modhunter callback {name} unavailable ({type(exc).__name__})")
            return False

    def _on_about_to_run(self, binary, working_directory, arguments):
        if self.server is not None and not self.server.content.about_to_run(binary):
            QtWidgets.QMessageBox.information(None, "Modhunter content operation",
                "Finish the active mod installation or recovery before launching the game or an MO2 tool.")
            return False
        return True

    def _on_finished_run(self, binary, exit_code):
        if self.server is not None:
            self.server.content.finished_run(binary)

    # Exact arities select MO2's current map-based overload, unlike *args which
    # matches the deprecated per-item overload first and storms large profiles.
    def _on_mod_states_changed(self, changes):
        if self.server is not None:
            # The native map needs only its count and, for a single change, its key.
            names = changes if type(changes) is dict else (
                list(changes) if isinstance(changes, dict) else ())
            single = len(names) == 1
            self.server.content.invalidate(reason="target" if single else "external",
                                           mod_name=str(next(iter(names))) if single else "")
        self._schedule_refresh()

    def _on_plugin_states_changed(self, changes):
        self._schedule_refresh()

    def _on_profile_changed(self, previous, current):
        if self.server is not None:
            self.server.invalidate_focus_profile()
            self.server.content.invalidate(reason="profile")
        self._schedule_refresh()

    def _on_mod_installed(self, mod):
        if self.server is not None:
            self.server.content.invalidate(reason="target", mod_name=mod if isinstance(mod, str) else call_str(mod, "name", ""))
        self._schedule_refresh()

    def _on_mod_removed(self, name):
        if self.server is not None:
            self.server.content.invalidate(reason="target", mod_name=str(name))
        self._schedule_refresh()

    def _on_mod_moved(self, name, old_priority, new_priority):
        if self.server is not None:
            self.server.content.invalidate(reason="target", mod_name=str(name))
        self._schedule_refresh()

    def _on_plugins_refreshed(self):
        self._schedule_refresh()

    def _on_download_complete(self, did):
        self._download_state(did, "complete")

    def _on_download_failed(self, did):
        self._download_state(did, "failed")

    def _on_download_paused(self, did):
        self._download_state(did, "paused")

    def _on_download_removed(self, did):
        self._download_state(did, "removed")

    def _on_ui_initialized(self, window):
        self._ui_initialized = True
        # Do not read an incomplete profile in init(), and never open a browser
        # automatically. Discovery is available after MO2's UI becomes ready.
        if self._background_refresh is not None:
            self._schedule_startup()
        else:
            QtCore.QTimer.singleShot(0, self._start_automatically)

    def _schedule_startup(self, delay=0):
        if (self._closing or self.server is not None or self._startup_inflight
                or self._startup_timer is not None or self._startup_attempts >= 5):
            return
        token = self._startup_timer = object()

        def start_if_pending():
            if self._startup_timer is token:
                self._startup_timer = None
                self._start_automatically()

        QtCore.QTimer.singleShot(delay, start_if_pending)

    def _start_automatically(self):
        if (self._closing or self.server is not None
                or not (self._ui_initialized or self._startup_open)
                or self._startup_inflight or self._startup_attempts >= 5):
            return
        self._startup_attempts += 1
        if self._background_refresh is not None:
            self._startup_inflight = True
            self._startup_invalidated = False
            self._background_refresh.enqueue(done=self._finish_background_startup)
            return
        try:
            self._ensure_service()
        except Exception as exc:
            self._snapshot_error = type(exc).__name__
            LOG.warning(f"Modhunter bridge startup attempt {self._startup_attempts} failed ({type(exc).__name__})")
            if self._startup_attempts < 5:
                QtCore.QTimer.singleShot(1000, self._start_automatically)

    def _finish_background_startup(self, result):
        self._startup_inflight = False
        if self._closing:
            return
        if result.get("ok"):
            open_dashboard, self._startup_open = self._startup_open, False
            if open_dashboard:
                try:
                    self._open_dashboard(result["address"])
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(None, "Modhunter",
                        f"Could not open Modhunter: {type(exc).__name__}: {exc}")
            return
        self._snapshot_error = str(result.get("error") or "Could not start Modhunter")
        LOG.warning(f"Modhunter bridge startup attempt {self._startup_attempts} failed ({self._snapshot_error})")
        if self._startup_attempts < 5:
            self._schedule_startup(0 if self._startup_invalidated else 1000)
        elif self._startup_open:
            self._startup_open = False
            QtWidgets.QMessageBox.critical(None, "Modhunter",
                "Could not open Modhunter: " + self._snapshot_error)

    def _startup_profile_ready(self):
        return bool(self.org.profileName() and self.org.profilePath())

    def _ensure_service(self):
        if self._closing:
            raise RuntimeError("MO2 is closing")
        if self.server is not None:
            return self.server.start()
        if not self._startup_profile_ready():
            raise RuntimeError("MO2 has not finished selecting its profile")
        started = time.perf_counter()
        snap = take_snapshot(self.org, self._runtime, metadata_cache=self._metadata_cache)
        return self._create_service(snap, started)

    def _create_service(self, snap, started, *, valid=None):
        attach_native_plugin_state(self.org, snap)
        if valid is not None and not valid():
            raise RuntimeError("MO2 profile changed during startup")
        doc = (Path(__file__).with_name("web") / "index.html").read_text(encoding="utf-8")
        try:
            folder = Path(self.org.pluginDataPath()) / "MO2UpdateArsenal"
        except Exception:
            folder = Path(__file__).parent / "data"
        if self._native_nexus is None:
            try:
                self._native_nexus = NativeNexusGateway(self.org)
                self._native_nexus_error = ""
            except Exception as exc:
                self._native_nexus_error = type(exc).__name__
        if valid is not None and not valid():
            raise RuntimeError("MO2 profile changed during startup")
        service = ArsenalService(doc, folder / "nexus-cache.json", self.dispatcher.submitted.emit, snap)
        service.native_nexus_available = self._native_nexus is not None
        service.native_nexus_files_available = bool(self._native_nexus and self._native_nexus.files_available)
        service.native_nexus_files_error = self._native_nexus.files_error if self._native_nexus else self._native_nexus_error
        service.content.native_enabled = True
        service.content.launch_guard_available = bool(self._content_launch_guard)
        try:
            if valid is not None and not valid():
                raise RuntimeError("MO2 profile changed during startup")
            address = service.start()
            if valid is not None and not valid():
                raise RuntimeError("MO2 profile changed during startup")
        except Exception:
            service.stop()
            raise
        self.server = service
        self._snapshot_error = ""
        self._snapshot_ms = (time.perf_counter() - started) * 1000
        self._bind_native_selection()
        self._publish_diagnostics()
        # MO2's Python proxy forwards LogRecord.msg without applying args.
        LOG.info(f"Modhunter {VERSION} ready with {len(snap['items'])} mods, Qt {QtCore.qVersion()}, desktop discovery enabled")
        return address

    def _publish_diagnostics(self):
        if self.server is not None:
            self.server.native_diagnostics = {
                "pluginVersion": VERSION, "qtRuntime": QtCore.qVersion(),
                "automaticStartup": self._ui_initialized,
                "callbackWarnings": list(self._subscription_errors),
                "lastSnapshotError": self._snapshot_error,
                "snapshotMs": round(self._snapshot_ms, 2),
                "nativeNexus": bool(self._native_nexus),
                "nativeFiles": bool(self._native_nexus and self._native_nexus.files_available),
                "fileTransport": self._native_nexus.file_transport if self._native_nexus else "unavailable",
            }

    def _shutdown(self):
        self._closing = True
        self._startup_timer = None
        self._startup_open = False
        if self._background_refresh is not None:
            self._background_refresh.close()
        if self.server is not None:
            self.server.stop()
            self.server = None

    def display(self) -> None:
        if not self.org:
            QtWidgets.QMessageBox.warning(None, "Modhunter", "MO2 has not initialized this plugin yet.")
            return
        if self.server is None and self._background_refresh is not None and not self._closing:
            self._startup_open = True
            if self._startup_attempts >= 5 and not self._startup_inflight:
                self._startup_attempts = 0
            self._schedule_startup()
            return
        try:
            existed = self.server is not None
            address = self._ensure_service()
            if existed and self._background_refresh is not None:
                def after_refresh(result):
                    if self._closing:
                        return
                    if result.get("ok"):
                        self._open_dashboard(address)
                    else:
                        QtWidgets.QMessageBox.critical(None, "Modhunter",
                            result.get("error", "Profile refresh failed"))
                self._queue_background_refresh(force=True, done=after_refresh)
                return
            if existed:
                refreshed = self._refresh_snapshot(force_metadata=True)
                if not refreshed.get("ok"):
                    raise RuntimeError(refreshed.get("error", "Profile refresh failed"))
            self._open_dashboard(address)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "Modhunter", f"Could not open Modhunter: {type(exc).__name__}: {exc}")

    def _open_dashboard(self, address):
        if QtGui.QDesktopServices.openUrl(QtCore.QUrl(address)) is False:
            QtWidgets.QMessageBox.information(None, "Modhunter is ready",
                "The default browser did not open. Launch Modhunter.exe and choose Find MO2.")

    def _background_context(self):
        return (str(id(self.server)), call_str(self.org, "basePath"),
                call_str(self.org, "profileName"), call_str(self.org, "profilePath"), self._runtime)

    def _capture_background_refresh(self):
        if self._closing or not self.server:
            raise RuntimeError("MO2 profile service unavailable")
        started = time.perf_counter()
        return capture_snapshot(self.org, self._runtime), started

    def _begin_background_capture(self, valid):
        if self._closing or (self.server is None and not self._startup_inflight):
            raise RuntimeError("MO2 profile service unavailable")
        if self.server is None and not self._startup_profile_ready():
            raise RuntimeError("MO2 has not finished selecting its profile")
        started = time.perf_counter()
        return begin_snapshot_capture(self.org, self._runtime, is_current=valid), started

    def _queue_background_refresh(self, *, force=False, done=None):
        # Each requested capture subsumes only the callback already waiting
        # at this instant. New native events get a fresh revision and timer.
        self._pending_refresh = None
        def result(outcome):
            if not self._closing and not outcome.get("ok"):
                self._snapshot_error = str(outcome.get("error") or "Profile refresh failed")
                self._publish_diagnostics()
            if done is not None:
                done(outcome)
        self._background_refresh.enqueue(force=force, done=result)

    def _on_background_refresh_completed(self, completed):
        if self._background_refresh is not None:
            self._background_refresh.complete(completed)

    def _publish_background_refresh(self, result, valid):
        snapshot, started = result
        if self.server is None:
            if not self._startup_inflight or not valid():
                return {"ok": False, "error": "MO2 profile changed; refresh again"}
            return {"ok": True, "address": self._create_service(snapshot, started, valid=valid)}
        attach_native_plugin_state(self.org, snapshot)
        if not valid():
            return {"ok": False, "error": "MO2 profile changed; refresh again"}
        self.server.set_snapshot(snapshot)
        if valid():
            self._background_dirty = False
        self._snapshot_error = ""
        self._snapshot_ms = (time.perf_counter() - started) * 1000
        self._publish_diagnostics()
        return {"ok": True, "count": len(snapshot["items"]), "profile": snapshot["profileName"], "catalog": snapshot}

    def _download_state(self, did: int, state: str):
        if self.server and type(did) is int and parse_download_id(did) is not None:
            self.server.set_download_status(did, state, native_signal=True)
            if state == "complete":
                # The callback itself is an authoritative READY observation.
                # Match its actual path immediately, independently of ticket IDs.
                pending = self.server.pending_native_archives()
                if pending:
                    try:
                        proof = archive_receipt(self.org.downloadsPath(), self.org.downloadManager().downloadPath(did))
                        game = self.org.managedGame()
                        matches = [(handle, t) for handle, t in pending if t["profileName"] == self.org.profileName()
                                   and t["game"].casefold() == str(game.gameNexusName()).casefold()
                                   and proof["game"] == str(game.gameShortName()).casefold()
                                   and (t["modId"], t["fileId"]) == (proof["modId"], proof["fileId"])]
                        if len(matches) == 1:
                            handle, ticket = matches[0]
                            self.server.confirm_native_archive(handle, ticket["operationId"], proof)
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                        pass
                self._schedule_download_check()

    def _schedule_download_check(self):
        if not self._download_check_pending and not self._closing:
            self._download_check_pending = True
            QtCore.QTimer.singleShot(3000, self._check_native_downloads)

    def _check_native_downloads(self):
        self._download_check_pending = False
        if self._closing or self.server is None:
            return
        pending = self.server.pending_native_archives()
        if not pending:
            return
        try:
            model = download_model(QtWidgets)
            if model is not None:
                if self._download_directory_index is None:
                    self._download_directory_index = DownloadDirectoryIndex()
                start = self._download_check_offset % len(pending)
                for i in range(min(4, len(pending))):
                    handle, ticket = pending[(start + i) % len(pending)]
                    if self.org.profileName() == ticket["profileName"]:
                        proof = ready_receipt(self.org, model, QtCore, handle, ticket, self._download_directory_index)
                        if proof:
                            self.server.confirm_native_archive(handle, ticket["operationId"], proof)
                self._download_check_offset = start + 4
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
        self._schedule_download_check()

    def _schedule_refresh(self):
        if self.server is not None and self._background_refresh is not None and not self._closing:
            self._background_dirty = True
            self._background_refresh.invalidate()
        if self._pending_refresh is not None or self._closing:
            return
        if self.server is None:
            if self._background_refresh is not None:
                if self._startup_inflight:
                    self._startup_invalidated = True
                    self._background_refresh.invalidate()
                elif self._ui_initialized and self._startup_timer is None:
                    if self._startup_attempts >= 5:
                        self._startup_attempts = 0
                    self._schedule_startup()
            elif self._ui_initialized:
                self._startup_attempts = 0
                QtCore.QTimer.singleShot(0, self._start_automatically)
            return
        token = self._pending_refresh = object()

        def refresh_if_pending():
            if not self._closing and self._pending_refresh is token:
                self._pending_refresh = None
                if self._background_refresh is not None:
                    self._queue_background_refresh()
                else:
                    self._refresh_snapshot()

        QtCore.QTimer.singleShot(150, refresh_if_pending)

    def _refresh_snapshot(self, *, force_metadata: bool = False) -> dict:
        # An explicit capture subsumes earlier requests, not events raised
        # while native state is being read. Those receive their own token.
        pending, self._pending_refresh = self._pending_refresh, None
        if not self.server:
            return {"ok": False, "error": "Launch Arsenal first"}
        started = time.perf_counter()
        if self._background_refresh is not None:
            self._background_refresh.invalidate()
        try:
            snapshot = take_snapshot(self.org, self._runtime, metadata_cache=self._metadata_cache,
                                     force_metadata=force_metadata)
            attach_native_plugin_state(self.org, snapshot)
            self.server.set_snapshot(snapshot)
            if self._pending_refresh is None:
                self._background_dirty = False
            self._snapshot_error = ""
            self._snapshot_ms = (time.perf_counter() - started) * 1000
            self._publish_diagnostics()
            return {"ok": True, "count": len(snapshot["items"]), "profile": snapshot["profileName"], "catalog": snapshot}
        except Exception as exc:
            # A failed explicit capture did not satisfy its pending event.
            # A failed scheduled capture has no token and does not loop.
            if pending is not None:
                self._schedule_refresh()
            self._snapshot_error = type(exc).__name__
            self._publish_diagnostics()
            LOG.warning(f"Modhunter profile refresh failed ({type(exc).__name__})")
            return {"ok": False, "error": f"MO2 profile refresh failed: {exc}"}

    def _download(self, mod_id: int, file_id: int):
        if not self.server:
            return {"ok": False, "error": "Arsenal server is not running"}
        if not self.server.has_nexus_mod(mod_id):
            return {"ok": False, "error": "Nexus mod is not in active profile"}
        try:
            result = self.org.downloadManager().startDownloadNexusFile(mod_id, file_id)
            if parse_download_id(result) is None:
                return {"ok": False, "error": "MO2 declined this download. On free Nexus accounts use the Files page and Mod Manager Download."}
            return {"ok": True, "downloadId": int(result), "message": "Sent selected Nexus file to MO2 download manager"}
        except Exception as ex:
            return {"ok": False, "error": f"MO2 download manager error: {ex}. Free accounts may need to initiate the download from Nexus."}

    def _download_url(self, payload: dict):
        """Resolve the exact reviewed public archive again on MO2's Qt thread."""
        from .update_plan import validate_release_asset, PlanError
        if not self.server:
            return {"ok": False, "error": "Modhunter service is not running"}
        try:
            selected = validate_release_asset(self.server.selection_snapshot([str(payload.get("itemId") or "")]), payload)
            if selected["url"] != payload.get("url") or selected["assetProof"] != payload.get("assetProof"):
                raise PlanError("Release archive changed before native execution")
        except PlanError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            download_id = parse_download_id(self.org.downloadManager().startDownloadURLs([selected["url"]]))
            if download_id is None:
                return {"ok": False, "error": "MO2 declined the source archive download"}
            return {"ok": True, "downloadId": download_id,
                    "message": "Verified public release asset queued in MO2 Downloads"}
        except (AttributeError, RuntimeError, ValueError, TypeError) as exc:
            return {"ok": False, "error": "MO2 URL download manager unavailable: " + str(exc)}

    def _profile_backup(self):
        raw_profile_path = self.org.profilePath()
        if not raw_profile_path:
            raise OSError("Active MO2 profile path is unavailable; installation stopped")
        profile_path = Path(raw_profile_path).resolve()
        if not profile_path.is_dir():
            raise OSError("Active MO2 profile directory is unavailable; installation stopped")
        profile_id = self.server.profile_id() or "unknown"
        root = Path(self.org.pluginDataPath()) / "MO2UpdateArsenal" / "backups" / profile_id
        path = root / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
        path.mkdir(parents=True, exist_ok=False)
        copied = []
        for name in ("modlist.txt", "plugins.txt", "loadorder.txt", "archives.txt", "lockedorder.txt"):
            src = profile_path / name
            if src.is_file():
                destination = path / name
                with src.open("rb") as original, destination.open("wb") as output:
                    shutil.copyfileobj(original, output)
                    output.flush()
                    os.fsync(output.fileno())
                shutil.copystat(src, destination)
                copied.append(name)
        if not copied:
            raise OSError("No profile load-order files could be backed up; installation stopped")
        manifest = {"createdAt": datetime.now(timezone.utc).isoformat(), "profileId": profile_id, "profileName": self.org.profileName(), "files": copied, "purpose": "Pre-update text load order safety snapshot"}
        with (path / "manifest.json").open("w", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        return {"ok": True, "backupPath": str(path), "files": copied}

    def _install(self, download_id: int, native_archive_proof=None):
        if protected_installs():
            return protected_refusal()
        status = self.server.downloads().get(download_id, {}) if self.server else {}
        if status.get("status") != "installing":
            return {"ok": False, "error": "The download is not complete"}
        from .install_brief import build_install_brief
        preflight = build_install_brief(self.server.selection_snapshot([str(status.get("itemId") or "")]),
                                        {**status, "status": "complete"}, download_id)
        if not preflight.get("canInstall"):
            return {"ok": False, "error": "; ".join(preflight.get("errors") or ["Archive evidence changed before native installation"])}
        try:
            before = self.server.native_snapshot()
            if str(self.org.profileName()) != str(before.get("profileName") or ""):
                return {"ok": False, "error": "Active MO2 profile changed; reopen the Workshop before installing"}
            source = (verified_path(self.org.downloadsPath(), native_archive_proof) if native_archive_proof
                      else Path(self.org.downloadManager().downloadPath(download_id)).resolve())
            root = Path(self.org.downloadsPath()).resolve()
            if not source.is_file() or not source.is_relative_to(root) or source.suffix.lower() not in (".zip", ".7z", ".rar"):
                return {"ok": False, "error": "Selected download file is missing or outside the MO2 download directory"}
            # Backup profile before launching MO2's standard interactive installer.
            backup = self._profile_backup()
            installed = self.org.installMod(str(source))
            if not installed:
                return {"ok": False, "error": "Installation was canceled or failed; MO2 files were not removed", "backup": backup}
            self._refresh_snapshot(force_metadata=True)
            # MO2 installMod returned a real installed mod; only that exact row
            # may receive verified XP (never every mod sharing a Nexus ID).
            after = self.server.native_snapshot()
            target = next((m for m in after.get("items", []) if m.get("mo2InternalName") == installed.name()), None)
            previous = next((m for m in before.get("items", []) if m.get("mo2InternalName") == installed.name()), None)
            verified = bool(after.get("profileId") == before.get("profileId")
                            and target and previous
                            and (not status.get("itemId") or target.get("id") == status["itemId"])
                            and version_verdict(str(previous.get("installedVersion", "")),
                                                str(target.get("installedVersion", ""))) == "candidate")
            if verified:
                ticket = status
                self.server.chronicle.record_update(after["profileId"], target, previous.get("installedVersion", "?"), target.get("installedVersion", "?"), "mo2-install-complete", str(ticket.get("fileId", "")))
            return {"ok": True, "message": ("Update version confirmed in MO2" if verified else
                "MO2 installer finished; version change not confirmed. Rescan and verify FOMOD, overrides and enabled state"),
                "backup": backup, "installedName": installed.name(), "verifiedUpdate": verified}
        except Exception as ex:
            return {"ok": False, "error": f"MO2 install did not complete: {ex}"}

    def _runtime_dialog(self):
        options = ["Detect from game", "1.5.97", "1.6.640", "1.6.1170", "1.4.15 (Skyrim VR)", "Other / custom"]
        current = self._runtime if self._runtime not in ("unknown", "") else "Detect from game"
        selected, ok = QtWidgets.QInputDialog.getItem(None, "Game Runtime - Update Arsenal", "Choose the executable version used for compatibility warnings:", options, options.index(current) if current in options else 0, False)
        if not ok:
            return {"ok": False, "error": "Runtime change canceled"}
        if selected == "Other / custom":
            selected, ok = QtWidgets.QInputDialog.getText(None, "Custom Game Runtime", "Game executable version:")
            if not ok or not selected.strip():
                return {"ok": False, "error": "Custom runtime not set"}
        self._runtime = "unknown" if selected == "Detect from game" else selected.strip()
        self.org.setPersistent(self.name(), "target-runtime", self._runtime)
        return self._refresh_snapshot()

    def _nexus_connect(self):
        dlg = QtWidgets.QDialog(None)
        dlg.setWindowTitle("Nexus Connection - Update Arsenal")
        layout = QtWidgets.QVBoxLayout(dlg)
        info = QtWidgets.QLabel("MO2's existing Nexus sign-in is the primary connection for mod update scanning and exact file lists.\n\nUse Settings > Nexus > Connect to Nexus. Modhunter does not extract credentials. A personal API key is an optional fallback on older MO2 builds.")
        info.setWordWrap(True)
        layout.addWidget(info)
        field = QtWidgets.QLineEdit()
        field.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        field.setPlaceholderText("Optional personal Nexus API key")
        layout.addWidget(field)
        open_account = QtWidgets.QPushButton("Open official Nexus API/account page")
        open_account.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl("https://www.nexusmods.com/users/myaccount?tab=api")))
        layout.addWidget(open_account)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            key = field.text().strip()
            if key:
                self.server.set_key(key)
                field.clear()
                return {"ok": True, "keyConnected": True, "message": "API key active in memory for this MO2 session"}
            native_files = bool(self._native_nexus and self._native_nexus.files_available)
            message = ("MO2 native Nexus bridge ready" if native_files else
                       "MO2 metadata bridge ready; install the matching native helper or supply a fallback key for file lists" if self._native_nexus else
                       "Open MO2 Settings > Nexus > Connect to Nexus, or use a fallback key")
            return {"ok": True, "keyConnected": bool(self.server.api_key), "nativeNexus": bool(self._native_nexus),
                    "nativeNexusFiles": native_files, "message": message}
        return {"ok": False, "error": "Connection dialog canceled"}

    def _focus_mod(self, payload: dict) -> dict:
        service = self.server
        if service is None:
            return {"ok": False, "error": "Modhunter service is not running"}
        target = service.native_focus_target(payload, since_intent=True)
        if not target["ok"]:
            return target
        if self.org.profileName() != target["profileName"]:
            return {"ok": False, "code": "focus-refresh", "error": "MO2 is switching profiles."}

        def still_current():
            if (self.server is not service or self.org.profileName() != target["profileName"]
                    or not service.native_focus_target(payload)["ok"]):
                return False
            if target["focusIdentity"]:
                mod = self.org.modList().getMod(target["name"])
                observed = focus_identity(call_str(mod, "absolutePath"), target["game"], target["name"], None)
                return observed.get("focusIdentity") == target["focusIdentity"]
            return True

        prior_focus = self._focus_navigating
        self._focus_navigating = True
        try:
            result = navigate(self.org, target["name"], QtCore, QtWidgets, still_current=still_current)
        finally:
            self._focus_navigating = prior_focus
        if result.get("ok") and not still_current():
            return {"ok": False, "error": "A newer MO2 selection superseded this navigation"}
        # This acknowledgement is also proof for the HTTP worker. A later
        # organizer selection or catalog can supersede it before delivery.
        return service.acknowledge_native_focus(payload, result)

    def _bind_native_selection(self):
        if self._selection_connected:
            return
        _, view = find_mo2_view(QtWidgets)
        if view is None or view.selectionModel() is None:
            return
        try:
            view.selectionModel().currentChanged.connect(self._on_native_selection)
            view.model().modelReset.connect(self._on_native_model_reset)
            self._selection_connected = True
        except (AttributeError, RuntimeError):
            pass

    def _on_native_model_reset(self):
        # QItemSelectionModel resets without sending currentChanged. Observe
        # the resulting native index just as we would after a keyboard action.
        _, view = find_mo2_view(QtWidgets)
        if view is not None:
            self._on_native_selection(view.currentIndex(), None)

    def _on_native_selection(self, current, _previous):
        # The owner's verified reveal acknowledges its exact native target
        # explicitly. Its synchronous focus/filter signals are not newer
        # user actions and cannot invalidate a queued subsequent reveal.
        if self._focus_navigating or not self.server:
            return
        if not current.isValid():
            self.server.set_selected("")
            return
        _, view = find_mo2_view(QtWidgets)
        if view is None:
            return
        name_col = name_column(view.model(), QtCore)
        label = str(current.siblingAtColumn(name_col).data(QtCore.Qt.ItemDataRole.DisplayRole) or "")
        # Exact, indexed matching avoids a full health-decorated snapshot on
        # every arrow-key selection. Duplicate labels deliberately select none.
        self.server.set_selected_by_label(label)

    def _defer_content_lease(self, request, background):
        # Leave Qt ownership unclaimed. Cancellation/expiry can still win
        # while the worker retains its existing install/restore queue lock.
        if request.state != "queued" or request in self._deferred_content_leases:
            return
        if request.deadline is not None and time.monotonic() >= request.deadline:
            request.cancel_if_pending()
            return
        self._deferred_content_leases.add(request)
        def resume(outcome):
            self._deferred_content_leases.discard(request)
            if request.state != "queued":
                return
            if request.deadline is not None and time.monotonic() >= request.deadline:
                request.cancel_if_pending()
                return
            if self._closing:
                request.finish({"ok": False, "error": "MO2 is closing"})
                return
            # A newer debounce can supersede this outcome without yet having
            # enqueued its capture. Join it rather than starting extra work.
            if self._pending_refresh is not None:
                self._defer_content_lease(request, background)
                return
            if not outcome.get("ok") or self._background_dirty or background.busy:
                request.finish({"ok": False, "error": outcome.get("error")
                                or "MO2 profile did not settle; review the operation again"})
                return
            self._dispatch(request)  # NativeContent checks full reviewed identity and lease.

        refused = background.after_settle(resume)
        if refused is not None:
            self._deferred_content_leases.discard(request)
            request.finish({"ok": False, "error": refused})
        elif self._pending_refresh is not None or not background.busy:
            # Promote a pending debounce into owned work now. Another explicit
            # refresh may subsume its timer, but cannot strand this listener.
            self._queue_background_refresh()

    def _dispatch(self, request: UIRequest):
        if request.cmd == "content-lease":
            if self._closing:
                request.finish({"ok": False, "error": "MO2 is closing"})
                return
            background = getattr(self, "_background_refresh", None)
            if background is not None and (self._pending_refresh is not None or self._background_dirty or background.busy):
                # Only an actual reviewed lease is eligible to wait. Invalid
                # internal requests do not take a bounded settlement slot.
                if not isinstance(request.payload.get("context"), dict) or not request.payload.get("jobId"):
                    request.finish({"ok": False, "error": "MO2 profile is refreshing; a reviewed content lease is required"})
                else:
                    self._defer_content_lease(request, background)
                return
        if not request.begin():
            return  # Expired/cancelled Qt events can never execute later.
        try:
            background = getattr(self, "_background_refresh", None)
            if (getattr(self, "_background_dirty", False) or
                (background is not None and background.busy)) and (
                # Do not interrupt a native recovery transaction that already
                # owns its lease: it independently rechecks organizer identity
                # and its synchronous post-install refresh must still complete.
                request.cmd in ("download", "download-url", "install", "backup", "content-lease", "agent-set-enabled")
            ):
                request.finish({"ok": False, "error": "MO2 profile is refreshing; review the operation again"})
                return
            if request.cmd == "agent-set-enabled":
                if self.server is None or QtWidgets.QApplication.activeModalWidget() is not None:
                    raise ValueError("MO2 is unavailable or waiting for a dialog")
                request.finish(set_enabled(self, request.payload, int(mobase.ModState.ACTIVE)))
                return
            if request.cmd.startswith("content-"):
                if self.server is None:
                    raise RuntimeError("Native content service is not active")
                self._content_native.handle(request, QtCore.QTimer)
                return
            if request.cmd in ("plugin-audit-input", "eslifier-data"):
                if not self.server or request.payload.get("profileId") != self.server.profile_id():
                    request.finish({"ok": False, "error": "MO2 profile changed before plugin analysis"})
                    return
                snapshot = self.server.native_snapshot()
                if str(self.org.profileName()) != str(snapshot.get("profileName") or ""):
                    request.finish({"ok": False, "error": "Active MO2 profile changed; refresh and retry"})
                    return
                if request.cmd == "plugin-audit-input":
                    request.finish({"ok": True, "request": build_audit_request(self.org, snapshot,
                        deep=request.payload.get("deep") is True,
                        configured=self.server.intelligence.configured_paths())})
                else:
                    folder = QtWidgets.QFileDialog.getExistingDirectory(None, "Select ESLifier_Data or ESLifier_FO4_Data")
                    if (str(self.org.profileName()) != str(snapshot.get("profileName") or "")
                            or self.server.profile_id() != snapshot.get("profileId")):
                        request.finish({"ok": False, "error": "Active MO2 profile changed while choosing ESLifier records; refresh and retry"})
                        return
                    request.finish(self.server.intelligence.configure_path(folder, snapshot["profileId"])
                        if folder else {"ok": False, "cancelled": True, "error": "Folder selection canceled"})
                return
            if request.cmd in ("download", "download-url", "install"):
                if not self.server or request.payload.get("profileId") != self.server.profile_id():
                    request.finish({"ok": False, "error": "MO2 profile changed before native execution; refresh and review again"})
                    return
                reviewed = request.payload.get("reviewedContext")
                if (request.cmd in ("download", "download-url") and reviewed is not None
                        and not self.server.download_context_matches(reviewed)):
                    request.finish({"ok": False, "error": "Reviewed release or compatibility changed before native execution; review the file again"})
                    return
                context = self.server.selection_snapshot([])
                if (request.payload.get("executionEpoch") is not None
                        and request.payload["executionEpoch"] != context.get("executionEpoch")):
                    request.finish({"ok": False, "error": "MO2 context changed before native execution; review the operation again"})
                    return
                if str(self.org.profileName()) != str(context.get("profileName") or ""):
                    # Profile-change refresh is debounced. Recheck the actual
                    # organizer instead of trusting that still-old snapshot.
                    request.finish({"ok": False, "error": "Active MO2 profile changed before native execution; refresh and review again"})
                    return
                if (request.payload.get("gameRuntime") is not None
                        and request.payload["gameRuntime"] != context.get("gameRuntime", "unknown")):
                    request.finish({"ok": False, "error": "Game runtime changed before native execution; review the file again"})
                    return
                item_id = request.payload.get("itemId")
                if item_id:
                    current = self.server.selection_snapshot([str(item_id)]).get("items", [])
                    if (not current or str(current[0].get("installedVersion") or "")
                            != str(request.payload.get("installedVersion") or "")):
                        request.finish({"ok": False, "error": "Installed mod changed before native execution; refresh and review again"})
                        return
                    if (request.cmd == "download" and int(current[0].get("nexusId") or 0)
                            != int(request.payload.get("modId") or 0)):
                        request.finish({"ok": False, "error": "Nexus project changed before native execution; review the file again"})
                        return
                    native_mod = self.org.modList().getMod(current[0].get("mo2InternalName"))
                    if native_mod is None:
                        request.finish({"ok": False, "error": "Selected mod was removed before native execution"})
                        return
                    native_version = str(native_mod.version())
                    if native_version in ("Invalid", "None", "0", "0.0", ""):
                        native_version = "?"
                    if native_version != str(request.payload.get("installedVersion") or ""):
                        request.finish({"ok": False, "error": "Native installed version changed before execution; refresh and review again"})
                        return
            if request.cmd in ("nexus-metadata", "nexus-files"):
                if self._native_nexus is None:
                    request.finish({"ok": False, "error": "MO2 native Nexus unavailable: " + self._native_nexus_error})
                else:
                    self._native_nexus.request(request)
                return
            if request.cmd == "refresh":
                if background is not None and self.server is not None:
                    self._queue_background_refresh(force=True, done=request.finish)
                    return
                result = self._refresh_snapshot(force_metadata=True)
            elif request.cmd in ("download", "download-url"):
                # Linearize the final evidence check with the short native
                # start call. A provider worker must not replace the reviewed
                # release between checking it and handing its archive to MO2.
                # Never hold this lease across the interactive FOMOD installer.
                with self.server._guard:
                    context = self.server.selection_snapshot([])
                    if (self.server._stopped or request.payload.get("profileId") != context.get("profileId")
                            or request.payload.get("executionEpoch", context.get("executionEpoch")) != context.get("executionEpoch")):
                        result = {"ok": False, "error": "MO2 context changed before native execution; review the operation again"}
                    elif (request.payload.get("reviewedContext") is not None
                            and not self.server.download_context_matches(request.payload["reviewedContext"])):
                        result = {"ok": False, "error": "Reviewed release or compatibility changed before native execution; review the file again"}
                    elif request.cmd == "download":
                        result = self._download(int(request.payload["modId"]), int(request.payload["fileId"]))
                        if result.get("ok"):
                            self._schedule_download_check()
                    else:
                        result = self._download_url(request.payload)
            elif request.cmd == "install":
                result = self._install(int(request.payload["downloadId"]), request.payload.get("nativeArchiveProof"))
            elif request.cmd == "focus":
                result = self._focus_mod(request.payload)
            elif request.cmd == "backup":
                result = self._profile_backup() if request.payload.get("confirm") else {"ok": False, "error": "Confirmation required"}
            elif request.cmd == "runtime":
                result = self._runtime_dialog()
            elif request.cmd == "connect":
                result = self._nexus_connect()
            else:
                result = {"ok": False, "error": "Unsupported MO2 command"}
            request.finish(result)
        except Exception as exc:
            request.finish({"ok": False, "error": f"MO2 command failed: {type(exc).__name__}: {exc}"})

    def __del__(self):
        if self.server:
            try:
                self.server.stop()
            except Exception:
                pass
