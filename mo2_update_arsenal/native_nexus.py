"""MO2-authenticated Nexus gateway, using the user's existing Nexus connection.

IOrganizer.createNexusBridge() is the supported MO2 API. All of its Qt
requests and signal callbacks must run on the MO2 GUI thread. This adapter
does not inspect MO2 credentials or invent an OAuth integration.
"""
from __future__ import annotations
import secrets
import threading
from typing import Any


def normalize_native_files(files: Any) -> list[dict]:
    """Convert MO2 ModRepositoryFileInfo records into JSON-safe file choices."""
    result = []
    for row in files if isinstance(files, (list, tuple)) else []:
        if isinstance(row, dict):
            fid = row.get("fileId", row.get("file_id", 0))
            read = lambda key, default=None: row.get(key, default)
        else:
            fid = getattr(row, "fileID", 0)
            read = lambda key, default=None: getattr(row, key, default)
        try:
            fid = int(fid)
            if fid <= 0:
                continue
            size = int(read("fileSize", read("size", 0)) or 0)
            result.append({
                "fileId": fid,
                "name": str(read("name") or read("fileName") or f"File {fid}")[:190],
                "version": str(read("version") or "")[:75],
                "category": int(read("categoryID", read("category_id", 0)) or 0),
                "sizeInKB": max(0, size // 1024),
                "fileName": str(read("fileName", read("file_name", "")) or "")[:190],
                "description": str(read("description") or "")[:600],
            })
        except (ValueError, TypeError, OverflowError):
            continue
    result.sort(key=lambda x: (x["category"] != 1, -x["fileId"]))
    return result


class NativeNexusGateway:
    """Qt callback adapter. Construct and use exclusively on MO2's GUI thread."""

    def __init__(self, organizer):
        self.bridge = organizer.createNexusBridge()
        if self.bridge is None:
            raise RuntimeError("MO2 did not supply a Nexus repository bridge")
        self.pending: dict[str, Any] = {}
        self._pending_lock = threading.Lock()
        self.bridge.descriptionAvailable.connect(self._description)
        self.bridge.filesAvailable.connect(self._files)
        self.bridge.requestFailed.connect(self._failed)

    def request(self, request) -> None:
        """Dispatch without blocking Qt. UIRequest is completed by a later Qt signal."""
        if request.state == "queued" and not request.begin():
            return
        if request.state != "running":
            return
        kind = request.cmd
        payload = request.payload
        if kind not in ("nexus-metadata", "nexus-files"):
            request.finish({"ok": False, "error": "Unsupported MO2 Nexus operation"})
            return
        try:
            game, mod_id = str(payload.get("game") or ""), int(payload.get("modId") or 0)
        except (TypeError, ValueError, OverflowError):
            request.finish({"ok": False, "error": "Invalid MO2 Nexus game or mod ID"})
            return
        if not game or mod_id <= 0:
            request.finish({"ok": False, "error": "Missing MO2 Nexus game or mod ID"})
            return
        ticket = "modhunter:" + secrets.token_hex(12)
        with self._pending_lock:
            self.pending[ticket] = request
        # Timeout cancellation runs on the waiting worker; only Python
        # bookkeeping is touched here, never QObject or repository APIs.
        request.add_done_callback(lambda: self._forget(ticket))
        if request.state != "running":
            return
        try:
            if kind == "nexus-metadata":
                self.bridge.requestDescription(game, mod_id, ticket)
            else:
                self.bridge.requestFiles(game, mod_id, ticket)
        except Exception as exc:
            self._forget(ticket)
            request.finish({"ok": False, "error": "MO2 Nexus request unavailable: " + str(exc)[:200]})

    def _forget(self, ticket):
        with self._pending_lock:
            return self.pending.pop(str(ticket), None)

    def _take(self, game, mod_id, ticket, kind=None):
        request = self._forget(ticket)
        if request is None or request.state != "running":
            return None
        try:
            same = (str(game) == str(request.payload.get("game"))
                    and int(mod_id) == int(request.payload.get("modId"))
                    and (kind is None or request.cmd == kind))
        except (TypeError, ValueError, OverflowError):
            same = False
        if not same:
            request.finish({"ok": False, "error": "MO2 Nexus response identity did not match the requested game, mod, and operation"})
            return None
        return request

    def _description(self, game, mod_id, ticket, data):
        request = self._take(game, mod_id, ticket, "nexus-metadata")
        if request is None:
            return
        try:
            if not isinstance(data, dict):
                data = dict(data)
            # The MO2 repository bridge returns the authoritative Nexus mod JSON.
            # Never interpret an empty/error payload as proof of current versions.
            if not data or not data.get("version"):
                request.finish({"ok": False, "error": "MO2 returned no usable Nexus version metadata"})
                return
            meta = {str(k): v for k, v in data.items() if k in
                    {"version", "name", "status", "updated_timestamp", "latest_file_update"}}
            request.finish({"ok": True, "meta": meta, "transport": "mo2-native-nexus"})
        except (TypeError, ValueError, RuntimeError) as exc:
            request.finish({"ok": False, "error": "Could not decode MO2 Nexus metadata: " + str(exc)[:160]})

    def _files(self, game, mod_id, ticket, data):
        request = self._take(game, mod_id, ticket, "nexus-files")
        if request is None:
            return
        try:
            files = normalize_native_files(data)
            request.finish({"ok": True, "modId": int(mod_id), "files": files, "transport": "mo2-native-nexus"})
        except (TypeError, ValueError, RuntimeError) as exc:
            request.finish({"ok": False, "error": "Could not decode MO2 Nexus files: " + str(exc)[:160]})

    def _failed(self, *args):
        # Native requestFailed(game, modId, fileId, userData, code, message).
        if len(args) < 4:
            return
        request = self._take(args[0], args[1], args[3])
        if request is not None:
            request.finish({
                "ok": False,
                "error": "MO2 Nexus connection failed: " + str(args[-1])[:200]
                + ". Verify MO2 Settings > Nexus > Connect to Nexus.",
            })
