"""MO2 2.5.x Python Tool plugin. Privileged mobase calls run on MO2's UI thread."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import mobase
from PyQt6 import QtCore, QtGui, QtWidgets

from .bridge import ArsenalService, UIRequest
from .model import take_snapshot
from .focus import navigate, find_mo2_view, name_column
from .scanner import version_verdict
from .native_nexus import NativeNexusGateway


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
        self._dirty = False
        self._runtime = "unknown"
        self._download_complete_callback = None
        self._selection_connected = False
        self._native_nexus = None
        self._native_nexus_error = ''

    def name(self) -> str:
        return "MO2 Update Arsenal"

    def isActive(self) -> bool:
        return True

    def author(self) -> str:
        return "Herberto / MO2 Update Arsenal"

    def description(self) -> str:
        return "Animated MO2 update command center with Modhunter Atlas campaigns, survivor squads, mod-named bosses and verified Forge Sigil bounties."

    def version(self):
        return mobase.VersionInfo("0.8.0")

    def settings(self):
        return []

    def displayName(self) -> str:
        return "Update Arsenal - Quest Board"

    def tooltip(self) -> str:
        return "Launch the local Arsenal dashboard for your active profile."

    def icon(self):
        return QtGui.QIcon()

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self.org = organizer
        self.dispatcher = Dispatcher(self._dispatch)
        try:
            self._native_nexus = NativeNexusGateway(organizer)
        except Exception as exc:
            self._native_nexus_error = str(exc)
        # Explicit user control for the runtime. Unknown is always safer than silently guessing.
        try:
            self._runtime = str(organizer.persistent(self.name(), "target-runtime", "unknown"))
        except Exception:
            self._runtime = "unknown"
        try:
            organizer.onProfileChanged(lambda *_: self._schedule_refresh())
            ml = organizer.modList()
            ml.onModInstalled(lambda *_: self._schedule_refresh())
            ml.onModRemoved(lambda *_: self._schedule_refresh())
            ml.onModMoved(lambda *_: self._schedule_refresh())
            ml.onModStateChanged(lambda *_: self._schedule_refresh())
            dm = organizer.downloadManager()
            dm.onDownloadComplete(lambda did: self._download_state(did, "complete"))
            dm.onDownloadFailed(lambda did: self._download_state(did, "failed"))
            dm.onDownloadPaused(lambda did: self._download_state(did, "paused"))
            dm.onDownloadRemoved(lambda did: self._download_state(did, "removed"))
        except Exception:
            # Plugin remains usable without change subscriptions; Refresh is always available.
            pass
        return True

    def display(self) -> None:
        if not self.org:
            QtWidgets.QMessageBox.warning(None, "Update Arsenal", "MO2 has not initialized this plugin yet.")
            return
        try:
            if not self.server:
                snap = take_snapshot(self.org, self._runtime)
                page = Path(__file__).with_name("web") / "index.html"
                doc = page.read_text(encoding="utf-8")
                try:
                    folder = Path(self.org.pluginDataPath()) / "MO2UpdateArsenal"
                except Exception:
                    folder = Path(__file__).parent / "data"
                self.server = ArsenalService(doc, folder / "nexus-cache.json", self.dispatcher.submitted.emit, snap)
                self.server.native_nexus_available = self._native_nexus is not None
            else:
                self._refresh_snapshot()
            address = self.server.start()
            self._bind_native_selection()
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(address))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "Update Arsenal", f"Could not open Arsenal: {type(exc).__name__}: {exc}")

    def _download_state(self, did: int, state: str):
        if self.server:
            self.server.set_download_status(int(did), state)

    def _schedule_refresh(self):
        if self._dirty:
            return
        self._dirty = True
        QtCore.QTimer.singleShot(150, self._refresh_snapshot)

    def _refresh_snapshot(self) -> dict:
        self._dirty = False
        if not self.server:
            return {"ok": False, "error": "Launch Arsenal first"}
        try:
            snapshot = take_snapshot(self.org, self._runtime)
            self.server.set_snapshot(snapshot)
            return {"ok": True, "count": len(snapshot["items"]), "profile": snapshot["profileName"], "catalog": snapshot}
        except Exception as exc:
            return {"ok": False, "error": f"MO2 profile refresh failed: {exc}"}

    def _download(self, mod_id: int, file_id: int):
        if not self.server:
            return {"ok": False, "error": "Arsenal server is not running"}
        snapshot = self.server.snapshot()
        if mod_id not in {int(m.get("nexusId") or 0) for m in snapshot.get("items", [])}:
            return {"ok": False, "error": "Nexus mod is not in active profile"}
        try:
            result = self.org.downloadManager().startDownloadNexusFile(mod_id, file_id)
            if int(result) <= 0:
                return {"ok": False, "error": "MO2 declined this download. On free Nexus accounts use the Files page and Mod Manager Download."}
            return {"ok": True, "downloadId": int(result), "message": "Sent selected Nexus file to MO2 download manager"}
        except Exception as ex:
            return {"ok": False, "error": f"MO2 download manager error: {ex}. Free accounts may need to initiate the download from Nexus."}

    def _download_url(self, payload: dict):
        """Route verified public GitHub release archives through MO2 Downloads."""
        url = str(payload.get("url") or "")
        if not url.startswith("https://github.com/") or "/releases/download/" not in url:
            return {"ok": False, "error": "Untrusted release archive URL rejected"}
        try:
            download_id = int(self.org.downloadManager().startDownloadURLs([url]))
            if download_id <= 0:
                return {"ok": False, "error": "MO2 declined the source archive download"}
            return {"ok": True, "downloadId": download_id,
                    "message": "Verified public release asset queued in MO2 Downloads"}
        except (AttributeError, RuntimeError, ValueError, TypeError) as exc:
            return {"ok": False, "error": "MO2 URL download manager unavailable: " + str(exc)}

    def _profile_backup(self):
        profile_path = Path(self.org.profilePath()).resolve()
        profile_id = self.server.snapshot().get("profileId", "unknown")
        root = Path(self.org.pluginDataPath()) / "MO2UpdateArsenal" / "backups" / profile_id
        path = root / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
        path.mkdir(parents=True, exist_ok=False)
        copied = []
        for name in ("modlist.txt", "plugins.txt", "loadorder.txt", "archives.txt", "lockedorder.txt"):
            src = profile_path / name
            if src.is_file():
                shutil.copy2(src, path / name)
                copied.append(name)
        manifest = {"createdAt": datetime.now(timezone.utc).isoformat(), "profileId": profile_id, "profileName": self.org.profileName(), "files": copied, "purpose": "Pre-update text load order safety snapshot"}
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {"ok": True, "backupPath": str(path), "files": copied}

    def _install(self, download_id: int):
        status = self.server.downloads().get(download_id, {}) if self.server else {}
        if status.get("status") != "installing":
            return {"ok": False, "error": "The download is not complete"}
        try:
            before = self.server.snapshot()
            if str(self.org.profileName()) != str(before.get("profileName") or ""):
                return {"ok": False, "error": "Active MO2 profile changed; reopen the Workshop before installing"}
            source = Path(self.org.downloadManager().downloadPath(download_id)).resolve()
            root = Path(self.org.downloadsPath()).resolve()
            if not source.is_file() or not source.is_relative_to(root) or source.suffix.lower() not in (".zip", ".7z", ".rar"):
                return {"ok": False, "error": "Selected download file is missing or outside the MO2 download directory"}
            # Backup profile before launching MO2's standard interactive installer.
            backup = self._profile_backup()
            installed = self.org.installMod(str(source))
            if not installed:
                return {"ok": False, "error": "Installation was canceled or failed; MO2 files were not removed", "backup": backup}
            self._refresh_snapshot()
            # MO2 installMod returned a real installed mod; only that exact row
            # may receive verified XP (never every mod sharing a Nexus ID).
            after = self.server.snapshot()
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
            return {"ok": True, "keyConnected": bool(self.server.api_key), "nativeNexus": bool(self._native_nexus), "message": "MO2 native Nexus connection ready" if self._native_nexus else "Open MO2 Settings > Nexus > Connect to Nexus, or use a fallback key"}
        return {"ok": False, "error": "Connection dialog canceled"}

    def _focus_mod(self, payload: dict) -> dict:
        snapshot = self.server.snapshot()
        if payload.get("profileId") != snapshot.get("profileId") or self.org.profileName() != snapshot.get("profileName"):
            return {"ok": False, "error": "MO2 profile changed during navigation; refresh Arsenal"}
        mod = next((m for m in snapshot.get("items", []) if m.get("id") == payload.get("modId") and m.get("mo2InternalName") == payload.get("name")), None)
        if not mod:
            return {"ok": False, "error": "Exact mod target no longer matches MO2"}
        return navigate(self.org, mod["mo2InternalName"], QtCore, QtWidgets)

    def _bind_native_selection(self):
        if self._selection_connected:
            return
        _, view = find_mo2_view(QtWidgets)
        if view is None or view.selectionModel() is None:
            return
        try:
            view.selectionModel().currentChanged.connect(self._on_native_selection)
            self._selection_connected = True
        except (AttributeError, RuntimeError):
            pass

    def _on_native_selection(self, current, _previous):
        if not self.server or not current.isValid():
            return
        _, view = find_mo2_view(QtWidgets)
        if view is None:
            return
        name_col = name_column(view.model(), QtCore)
        label = str(current.siblingAtColumn(name_col).data(QtCore.Qt.ItemDataRole.DisplayRole) or "")
        items = self.server.snapshot().get("items", [])
        hits = [m for m in items if m.get("name") == label]
        # Avoid duplicate-display-name ambiguity, as required by MO2R.
        if len(hits) == 1:
            self.server.set_selected(hits[0]["id"])
        else:
            self.server.set_selected("")

    def _dispatch(self, request: UIRequest):
        try:
            if request.cmd in ("nexus-metadata", "nexus-files"):
                if self._native_nexus is None:
                    request.finish({"ok": False, "error": "MO2 native Nexus unavailable: " + self._native_nexus_error})
                else:
                    self._native_nexus.request(request)
                return
            if request.cmd == "refresh":
                result = self._refresh_snapshot()
            elif request.cmd == "download":
                result = self._download(int(request.payload["modId"]), int(request.payload["fileId"]))
            elif request.cmd == "download-url":
                result = self._download_url(request.payload)
            elif request.cmd == "install":
                result = self._install(int(request.payload["downloadId"]))
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
