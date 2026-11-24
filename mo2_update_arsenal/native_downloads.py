"""Join a session's Nexus ticket to MO2's own Downloads state and archive."""
from __future__ import annotations

import stat
import os
import threading
from pathlib import Path

from .content_recovery import real_path


def archive_receipt(downloads, path):
    """Read only MO2's identity keys. A sidecar by itself never proves READY."""
    path = real_path(path)
    if path.parent != real_path(downloads, directory=True) or path.suffix.lower() not in {".zip", ".7z", ".rar"}:
        raise ValueError("Archive is outside MO2 Downloads")
    data = path.stat()
    sidecar = real_path(str(path) + ".meta")
    keys = {"gamename", "modid", "fileid", "name", "version", "repository", "removed", "paused"}
    values = {}
    if not stat.S_ISREG(data.st_mode) or not data.st_ino or data.st_size <= 0:
        raise ValueError("Archive identity is unavailable")
    if sidecar.stat().st_size > 1024 * 1024:
        raise ValueError("Archive metadata is too large")
    general = False
    with sidecar.open("rb") as f:
        for line in f:
            if f.tell() > 1024 * 1024:
                raise ValueError("Archive metadata is too large")
            line = line.removeprefix(b"\xef\xbb\xbf")
            if line.startswith(b"["):
                general = line.strip().lower() == b"[general]"
            elif general and b"=" in line:
                key = line[:line.index(b"=")].decode("ascii", errors="replace").lower()
                if key in keys:
                    if key in values:
                        raise ValueError("Duplicate archive metadata")
                    values[key] = line.split(b"=", 1)[1].strip().decode("utf-8")
    if (not keys.issubset(values) or values["repository"].casefold() != "nexus"
            or values["removed"] != "false" or values["paused"] != "false"
            or not values["gamename"] or not values["name"] or not values["version"]):
        raise ValueError("Incomplete Nexus archive metadata")
    birth = getattr(data, "st_birthtime_ns", data.st_ctime_ns)
    return {"archiveName": path.name, "game": values["gamename"].casefold(),
            "modId": int(values["modid"]), "fileId": int(values["fileid"]),
            "name": values["name"], "version": values["version"], "createdMs": birth // 1_000_000,
            "device": data.st_dev, "index": data.st_ino, "bytes": data.st_size, "mtimeNs": data.st_mtime_ns}


def verified_path(downloads, proof):
    name = proof["archiveName"]
    if not name or Path(name).name != name:
        raise ValueError("Invalid native archive receipt")
    path = Path(downloads) / name
    if archive_receipt(downloads, path) != proof:
        raise ValueError("The completed native download has changed")
    return path


class DownloadDirectoryIndex:
    """Read archive names and birth times off the GUI thread if handles moved."""

    def __init__(self):
        self.key = None
        self.result = None
        self.running = False
        self.lock = threading.Lock()

    def names(self, root, times):
        directory = real_path(root, directory=True)
        info = directory.stat()
        key = (directory, info.st_dev, info.st_ino, info.st_mtime_ns)
        with self.lock:
            if key == self.key and self.result is not None:
                return [directory / name for stamp in times for name in self.result.get(stamp, ())]
            if not self.running:
                self.running = True
                threading.Thread(target=self._read, args=(directory, key), daemon=True,
                                 name="ModhunterDownloadIndex").start()
        return None

    def _read(self, directory, key):
        by_time = {}
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if Path(entry.name).suffix.casefold() not in {".zip", ".7z", ".rar"}:
                        continue
                    data = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(data.st_mode):
                        stamp = getattr(data, "st_birthtime_ns", data.st_ctime_ns) // 1_000_000
                        by_time.setdefault(stamp, []).append(entry.name)
        except OSError:
            by_time = None
        finally:
            with self.lock:
                self.key, self.result, self.running = key, by_time, False


def ready_receipt(organizer, model, qt, ticket_id, ticket, directory_index=None):
    # These columns belong to MO2's DownloadList, before sort/filter proxies.
    role = qt.Qt.ItemDataRole.DisplayRole
    rows = model.match(model.index(0, 6), role, str(ticket["modId"]), -1, qt.Qt.MatchFlag.MatchExactly)
    downloaded = qt.QCoreApplication.translate("DownloadList", "Downloaded")
    candidates = []
    for index in rows:
        row = index.row()
        if model.data(model.index(row, 1), role) != downloaded:
            continue
        created = model.data(model.index(row, 3), role)
        if created is None or not created.isValid():
            continue
        candidates.append((row, created.toMSecsSinceEpoch(), str(model.data(model.index(row, 5), role)),
                           str(model.data(model.index(row, 0), role))))
    if not candidates:
        return None
    game = organizer.managedGame()
    if str(game.gameNexusName()).casefold() != ticket["game"].casefold():
        return None
    results = []
    # Some MO2 releases return row-based handles, others use independent IDs.
    # Each candidate number is only a read-only lookup hint into downloadPath.
    # No position, title, numeric handle or on-disk file grants completion.
    paths = []
    for handle in dict.fromkeys([ticket_id, *(r[0] for r in candidates)]):
        try:
            path = organizer.downloadManager().downloadPath(handle)
            if path:
                paths.append(Path(path))
        except (ValueError, TypeError, RuntimeError, OSError):
            continue
    if directory_index is not None:
        # Sorted view rows and stable handles need not coincide. The native
        # birth time lets us find the entry without assigning its row a handle.
        names = directory_index.names(organizer.downloadsPath(), {r[1] for r in candidates})
        if names is None:
            return None
        paths.extend(names)
    for path in dict.fromkeys(paths):
        try:
            proof = archive_receipt(organizer.downloadsPath(), path)
        except (ValueError, TypeError, OSError):
            continue
        if (proof["game"] != str(game.gameShortName()).casefold() or proof["modId"] != ticket["modId"]
                or proof["fileId"] != ticket["fileId"]):
            continue
        matches = [r for r in candidates if r[1] == proof["createdMs"] and r[2] == proof["version"]
                   and (r[3] in (proof["archiveName"], proof["name"]) or r[3].startswith(proof["name"] + " ("))]
        if len(matches) == 1 and proof not in results:
            results.append(proof)
    return results[0] if len(results) == 1 else None


def download_model(widgets):
    for window in widgets.QApplication.topLevelWidgets():
        if window.objectName() != "MainWindow":
            continue
        view = window.findChild(widgets.QTreeView, "downloadView")
        model = view.model() if view is not None else None
        while model is not None and hasattr(model, "sourceModel"):
            model = model.sourceModel()
        if model is not None and model.metaObject().className() == "DownloadList":
            return model
    return None
