"""MO2-authenticated Nexus requests on MO2's GUI thread.

The public repository bridge owns requests and account handling. On MO2 2.5.2
its filesAvailable pointer-list signal is neither SIP-convertible nor safe to
consume. A narrow typed Qt helper captures the native sender during one of our
own metadata callbacks, then observes that sender's ordinary QVariant JSON
signal. We never inspect account values or decode C++ file pointers.
"""
from __future__ import annotations
import secrets
import threading
from typing import Any


def normalize_native_files(files: Any, *, raw_api: bool = False) -> list[dict]:
    """Convert known Nexus JSON or legacy converted file records into choices."""
    result = []
    for row in files[:20000] if isinstance(files, (list, tuple)) else []:
        if isinstance(row, dict):
            fid = row.get("file_id", 0) if raw_api else row.get("fileId", row.get("file_id", 0))
            read = lambda key, default=None: row.get(key, default)
        elif not raw_api:
            fid = getattr(row, "fileID", 0)
            read = lambda key, default=None: getattr(row, key, default)
        else:
            continue
        try:
            if isinstance(fid, bool):
                continue
            fid = int(fid)
            if fid <= 0:
                continue
            # Nexus's current API declares bytes and KB separately. Its old
            # `size` field, copied into MO2's fileSize, is already in KB.
            if raw_api and read("size_in_bytes") is not None:
                size_kb = int(read("size_in_bytes") or 0) // 1024
            else:
                size_kb = int(read("sizeInKB", read("size_kb", read("fileSize", read("size", 0)))) or 0)
            result.append({
                "fileId": fid,
                "name": str(read("name") or read("fileName") or read("file_name") or f"File {fid}")[:190],
                "version": str(read("version") or "")[:75],
                "category": int(read("category", read("categoryID", read("category_id", 0))) or 0),
                "sizeInKB": max(0, size_kb),
                "fileName": str(read("fileName", read("file_name", read("uri", ""))) or "")[:190],
                "description": str(read("description") or "")[:600],
                "uploaded_time": str(read("uploaded_time") or "")[:80],
            })
        except (ValueError, TypeError, OverflowError):
            continue
    result.sort(key=lambda x: (x["category"] != 1, -x["fileId"]))
    return result


class NativeNexusGateway:
    """Construct and dispatch on MO2's GUI thread; cancellation is Python-only."""

    def __init__(self, organizer, *, sender_resolver=None):
        self.bridge = organizer.createNexusBridge()
        if self.bridge is None:
            raise RuntimeError("MO2 did not supply a Nexus repository bridge")
        self.pending: dict[str, Any] = {}
        self._bootstrap: dict[str, str] = {}
        self._bootstrap_parent: dict[str, str] = {}
        self._pending_lock = threading.Lock()
        self._thread_id = threading.get_ident()
        self._raw_source = None
        self._qt_sender = None
        self._files_error = ""
        self._legacy_files = False
        self.file_transport = "unavailable"
        self.qt_binding_info = {}
        delegated = getattr(self.bridge, "_object", None)
        self._qt_bridge = delegated() if callable(delegated) else delegated
        if self._qt_bridge is None and callable(getattr(self.bridge, "metaObject", None)):
            self._qt_bridge = self.bridge
        self.bridge.descriptionAvailable.connect(self._description)
        self.bridge.requestFailed.connect(self._failed)
        if self._qt_bridge is None:
            # Explicit non-Qt converted callback adapters (including portable
            # fixtures) retain their existing record path. A real Qt bridge
            # never subscribes to the unsupported QList<ModRepositoryFileInfo*>.
            self.bridge.filesAvailable.connect(self._files)
            self._legacy_files = True
            self.file_transport = "converted-callback"
        else:
            try:
                if sender_resolver is None:
                    from .qt_bridge import NativeQtSender
                    sender_resolver = NativeQtSender()
                self._qt_sender = sender_resolver
                self.qt_binding_info = dict(sender_resolver.info)
                self.file_transport = "qt-raw-bootstrap"
            except (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                # Metadata still works; a missing/mismatched helper must not
                # prevent plugin startup or falsely advertise a working list.
                self._files_error = str(exc)[:350]

    @property
    def files_available(self) -> bool:
        return self._legacy_files or (self._qt_sender is not None and not self._files_error)

    @property
    def files_error(self) -> str:
        return self._files_error

    def _on_thread(self) -> bool:
        return threading.get_ident() == self._thread_id

    def request(self, request) -> None:
        """Dispatch without blocking Qt. A later owned Qt callback completes it."""
        if request.state == "queued" and not request.begin():
            return
        if request.state != "running":
            return
        if not self._on_thread():
            request.finish({"ok": False, "error": "MO2 Nexus requests must run on MO2's GUI thread"})
            return
        kind, payload = request.cmd, request.payload
        if kind not in ("nexus-metadata", "nexus-files"):
            request.finish({"ok": False, "error": "Unsupported MO2 Nexus operation"})
            return
        try:
            game, mod_id = str(payload.get("game") or ""), int(payload.get("modId") or 0)
        except (TypeError, ValueError, OverflowError):
            request.finish({"ok": False, "error": "Invalid MO2 Nexus game or mod ID"})
            return
        if not game or mod_id <= 0 or isinstance(payload.get("modId"), bool):
            request.finish({"ok": False, "error": "Missing MO2 Nexus game or mod ID"})
            return
        if kind == "nexus-files" and not self.files_available:
            request.finish({"ok": False, "error": self._file_unavailable_message()})
            return
        ticket = "modhunter:" + secrets.token_hex(12)
        with self._pending_lock:
            self.pending[ticket] = request
        # The waiting worker may cancel. Only dictionary bookkeeping occurs in
        # this callback, never QObject methods, signal disconnects or requests.
        request.add_done_callback(lambda: self._forget(ticket))
        if request.state != "running":
            return
        try:
            if kind == "nexus-metadata":
                self._dispatch_owned(request, ticket, self.bridge.requestDescription, game, mod_id, ticket)
            elif self._legacy_files or self._raw_source is not None:
                self._dispatch_owned(request, ticket, self.bridge.requestFiles, game, mod_id, ticket)
            else:
                # Metadata may not have been requested yet. The native bridge
                # owns this one bootstrap request too; no account or URL is read.
                bootstrap = "modhunter:bootstrap:" + secrets.token_hex(12)
                with self._pending_lock:
                    if ticket not in self.pending:
                        return
                    self._bootstrap[bootstrap] = ticket
                    self._bootstrap_parent[ticket] = bootstrap
                if request.state == "running":
                    self._dispatch_owned(request, ticket, self.bridge.requestDescription, game, mod_id, bootstrap)
        except Exception as exc:
            self._forget(ticket)
            request.finish({"ok": False, "error": "MO2 Nexus request unavailable: " + str(exc)[:200]})

    def _dispatch_owned(self, request, ticket, method, game, mod_id, user_data):
        # MO2 versions expose either a void request or an acceptance boolean.
        # An explicit rejection cannot leave an owned request waiting forever.
        accepted = method(game, mod_id, user_data)
        if accepted is False:
            self._forget(ticket)
            request.finish({"ok": False, "error": "MO2 declined the Nexus request. Verify MO2 Settings > Nexus > Connect to Nexus, then retry."})

    def _file_unavailable_message(self):
        return ("MO2 Nexus file list unavailable: " + (self._files_error or "The native file signal is unavailable")
                + " Use the matching Modhunter MO2 package, or set an optional personal Nexus API key.")

    def _forget(self, ticket):
        with self._pending_lock:
            token = str(ticket)
            bootstrap = self._bootstrap_parent.pop(token, None)
            if bootstrap is not None:
                self._bootstrap.pop(bootstrap, None)
            return self.pending.pop(token, None)

    def _lookup(self, game, mod_id, ticket, kind=None):
        """Only an exact owned ticket can affect any request or capture a sender."""
        if not self._on_thread() or not isinstance(ticket, str):
            return None, None, False
        with self._pending_lock:
            parent = self._bootstrap.get(ticket)
            token = parent or ticket
            request = self.pending.get(token)
        if request is None or request.state != "running":
            return None, None, False
        try:
            same = (str(game) == str(request.payload.get("game"))
                    and not isinstance(mod_id, bool)
                    and int(mod_id) == int(request.payload.get("modId"))
                    and (kind is None or request.cmd == kind))
        except (TypeError, ValueError, OverflowError):
            same = False
        if not same:
            self._forget(token)
            request.finish({"ok": False, "error": "MO2 Nexus response identity did not match the requested game, mod, and operation"})
            return None, None, False
        return request, token, parent is not None

    def _take(self, game, mod_id, ticket, kind=None):
        request, token, bootstrap = self._lookup(game, mod_id, ticket, kind)
        if request is None:
            return None
        if bootstrap:
            self._forget(token)
            request.finish({"ok": False, "error": "MO2 Nexus response operation did not match its metadata bootstrap"})
            return None
        return self._forget(token)

    def _capture_source(self):
        """Called only inside an owned metadata/failure callback's signal stack."""
        if self._legacy_files:
            return True
        if self._raw_source is not None:
            return True
        if self._qt_sender is None:
            return False
        try:
            source = self._qt_sender.sender(self._qt_bridge)
            if source is None:
                raise RuntimeError("MO2's native Nexus callback did not expose its Qt sender")
            meta = source.metaObject()
            signature = b"nxmFilesAvailable(QString,int,QVariant,QVariant,int)"
            if (meta.className() != "NexusInterface" or meta.indexOfSignal(signature) < 0
                    or source.thread() != self._qt_bridge.thread()):
                raise RuntimeError("MO2's Nexus callback sender does not provide the expected raw files signal")
            source.nxmFilesAvailable.connect(self._raw_files)
            try:
                source.destroyed.connect(self._source_destroyed)
            except (AttributeError, RuntimeError, TypeError):
                source.nxmFilesAvailable.disconnect(self._raw_files)
                raise
            self._raw_source = source  # Borrowed QObject; native MO2 retains ownership.
            self._files_error = ""
            self.file_transport = "qt-raw-variant"
            return True
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            self._files_error = str(exc)[:350]
            self.file_transport = "unavailable"
            return False

    def _source_destroyed(self, *_):
        self._raw_source = None
        self.file_transport = "qt-raw-bootstrap" if self._qt_sender is not None else "unavailable"
        # Requests associated with the disappearing source cannot complete.
        with self._pending_lock:
            requests = list(self.pending.values())
            self.pending.clear()
            self._bootstrap.clear()
            self._bootstrap_parent.clear()
        for request in requests:
            request.finish({"ok": False, "error": "MO2's Nexus connection closed; retry after reconnecting"})

    @staticmethod
    def _metadata(game, mod_id, data):
        if not isinstance(data, dict):
            data = dict(data)
        known_status = str(data.get("status") or "").lower() in {
            "published", "hidden", "removed", "wastebinned", "under_moderation",
            "not_published", "publish_with_game", "not_found"}
        if not data or not (data.get("version") or known_status or isinstance(data.get("available"), bool)):
            raise ValueError("MO2 returned no usable Nexus version metadata")
        if ((data.get("mod_id") is not None and int(data["mod_id"]) != int(mod_id))
                or (data.get("domain_name") is not None and str(data["domain_name"]) != str(game))):
            raise ValueError("MO2 Nexus metadata identity did not match the requested game and mod")
        return {str(k): v for k, v in data.items() if k in
                {"version", "name", "status", "available", "mod_id", "domain_name",
                 "updated_timestamp", "updated_time", "latest_file_update"}}

    def _description(self, game, mod_id, ticket, data):
        request, token, bootstrap = self._lookup(game, mod_id, ticket)
        if request is None:
            return
        if request.cmd != ("nexus-files" if bootstrap else "nexus-metadata"):
            self._forget(token)
            request.finish({"ok": False, "error": "MO2 Nexus response identity did not match the requested operation"})
            return
        try:
            meta = self._metadata(game, mod_id, data)
            connected = self._capture_source()
            if bootstrap:
                with self._pending_lock:
                    self._bootstrap.pop(ticket, None)
                    self._bootstrap_parent.pop(token, None)
                if not connected:
                    self._forget(token)
                    request.finish({"ok": False, "error": self._file_unavailable_message()})
                elif request.state == "running":
                    self._dispatch_owned(request, token, self.bridge.requestFiles, str(game), int(mod_id), token)
            else:
                self._forget(token)
                request.finish({"ok": True, "meta": meta, "transport": "mo2-native-nexus"})
        except (TypeError, ValueError, OverflowError, RuntimeError) as exc:
            self._forget(token)
            request.finish({"ok": False, "error": "Could not decode MO2 Nexus metadata: " + str(exc)[:160]})

    def _files(self, game, mod_id, ticket, data):
        if not self._legacy_files:
            return
        request = self._take(game, mod_id, ticket, "nexus-files")
        if request is not None:
            self._finish_files(request, mod_id, data, raw_api=False)

    def _raw_files(self, game, mod_id, ticket, data, request_id):
        request = self._take(game, mod_id, ticket, "nexus-files")
        if request is None:
            return
        if (isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 0
                or not isinstance(data, dict) or not isinstance(data.get("files"), list)):
            request.finish({"ok": False, "error": "MO2 returned no usable raw Nexus file list"})
            return
        self._finish_files(request, mod_id, data["files"], raw_api=True)

    @staticmethod
    def _finish_files(request, mod_id, data, *, raw_api):
        try:
            files = normalize_native_files(data, raw_api=raw_api)
            request.finish({"ok": True, "modId": int(mod_id), "files": files, "transport": "mo2-native-nexus"})
        except (TypeError, ValueError, RuntimeError) as exc:
            request.finish({"ok": False, "error": "Could not decode MO2 Nexus files: " + str(exc)[:160]})

    def _failed(self, *args):
        # Native requestFailed(game, modId, fileId, userData, code, message).
        if len(args) != 6:
            return
        request, token, _ = self._lookup(args[0], args[1], args[3])
        if request is None:
            return
        # Failure callbacks run in the same native signal stack and can bind
        # the raw signal, but authentication failures never trigger a retry.
        self._capture_source()
        self._forget(token)
        if args[2] != 0:
            request.finish({"ok": False, "error": "MO2 Nexus failure response identity did not match the requested operation"})
            return
        request.finish({
            "ok": False,
            "statusCode": args[4] if type(args[4]) is int else None,
            "error": "MO2 Nexus connection failed: " + str(args[-1])[:200]
            + ". Verify MO2 Settings > Nexus > Connect to Nexus.",
        })
