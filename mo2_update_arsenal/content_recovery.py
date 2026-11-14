"""Owned, asynchronous mod-content transactions around MO2's native installer.

The HTTP/browser adapter supplies identities, never target paths. Native capture
runs on Qt; hashing/copying runs in the matching Rust helper on worker threads.
Prepared backups and ambiguous installer results are intentionally durable.
"""
from __future__ import annotations

from copy import deepcopy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import zipfile

from .model import call_str
from .plugin_intelligence import RustAuditRunner

MAX_ARCHIVE = 16 * 1024 * 1024 * 1024
MAX_LEDGER = 8 * 1024 * 1024
MAX_JOBS = 2000
REVIEW_ID = re.compile(r"review-[0-9]{1,20}-[0-9]{1,20}\Z")
DOWNLOAD_ID = re.compile(r"download-[A-Za-z0-9_-]{1,90}\Z")
BACKUP_ID = re.compile(r"backup-[A-Za-z0-9_-]{1,100}\Z")
TERMINAL = frozenset({"complete", "failed", "cancelled", "unknown", "interrupted"})


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def valid_target_name(value):
    if (not isinstance(value, str) or not value or len(value) > 180
            or value != value.strip() or value.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
            or value in {".", ".."} or value.lower().endswith("_separator")
            or value.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}):
        raise ValueError("Choose a plain, nonempty MO2 mod name without Windows path characters")
    return value


def real_path(value, *, directory=False, absent=False):
    """Reject links/reparse points in every existing component before resolve."""
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Content paths must be absolute native paths without parent traversal")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if absent and current == path:
                continue
            raise ValueError("Native content path no longer exists") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Links and Windows reparse points are not valid content targets")
    resolved = path.resolve(strict=not absent)
    if os.name == "nt":
        text = str(resolved)
        if text.startswith("\\\\?\\UNC\\"):
            resolved = Path("\\\\" + text[8:])
        elif text.startswith("\\\\?\\"):
            resolved = Path(text[4:])
    if directory and not resolved.is_dir():
        raise ValueError("Native content directory no longer exists")
    return resolved


def file_identity(path, cancel=None):
    path = real_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_ARCHIVE:
        raise ValueError("Archive is not a supported bounded regular file")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Content operation cancelled before native mutation")
            count += len(chunk)
            if count > MAX_ARCHIVE:
                raise ValueError("Archive exceeds the supported size")
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or count != before.st_size:
        raise ValueError("Archive changed while its complete bytes were verified")
    return {"path": str(path), "bytes": count, "sha256": digest.hexdigest()}


def read_json(path, bound):
    path = real_path(path)
    if not path.is_file() or path.stat().st_size > bound:
        raise ValueError("Owned transaction record is missing or exceeds its supported size")
    return json.loads(path.read_bytes())


def owned_recovery_root(mods_root, profiles_root):
    """Keep atomic quarantine/rollback on the mods volume, outside MO2's trees."""
    mods = real_path(mods_root, directory=True)
    profiles = real_path(profiles_root, directory=True)
    root = mods.parent / "Modhunter-Recovery"
    if root == mods or root.is_relative_to(mods) or root == profiles or root.is_relative_to(profiles):
        raise ValueError("The owned recovery directory must be outside native mods and profile directories")

    def owned(directory, name, marker):
        try:
            directory.mkdir()
        except FileExistsError:
            real_path(directory, directory=True)
            if not (directory / name).is_file() or read_json(directory / name, 16384) != marker:
                raise ValueError("Existing recovery directory has a different owner; no files were changed")
        else:
            with (directory / name).open("xb") as stream:
                stream.write(canonical_json(marker))
                stream.flush()
                os.fsync(stream.fileno())
        return real_path(directory, directory=True)

    root = owned(root, "modhunter-recovery-owner.json", {"schema": 1, "owner": "Modhunter-MO2-Recovery"})
    key = os.path.normcase(str(mods))
    identifier = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return owned(root / ("instance-" + identifier), "modhunter-instance-owner.json",
                 {"schema": 1, "owner": "Modhunter-MO2-Recovery-Instance", "modsRoot": key})


def reviewed_incoming_root(stage, marker):
    prefix = marker.get("prefix")
    if (not isinstance(prefix, str) or "\\" in prefix or ":" in prefix or prefix.startswith("/")
            or (prefix and (not prefix.endswith("/") or any(p in {"", ".", ".."} for p in prefix[:-1].split("/"))))):
        raise ValueError("Archive review normalization prefix is invalid")
    return real_path(stage / "raw" / prefix.rstrip("/"), directory=True)


def verify_acquisition(data, download_id, context, cancel):
    """Independently verify the host's exact receipt against owned disk state."""
    if not isinstance(data, dict) or not DOWNLOAD_ID.fullmatch(download_id):
        raise ValueError("An exact owned page-download identity is required")
    root = real_path(data.get("acquisitionRoot", ""), directory=True)
    if root.name != "watchtower-acquisitions":
        raise ValueError("Archive is outside Modhunter's acquisition store")
    document = read_json(root / "receipts.json", 4 * 1024 * 1024)
    rows = document.get("jobs") if isinstance(document, dict) and document.get("schema") == 1 else None
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Unsupported acquisition receipt document")
    matches = [row for row in rows if isinstance(row, dict) and row.get("id") == download_id]
    if len(matches) != 1 or matches[0] != data.get("receipt"):
        raise ValueError("Page-download receipt was changed or does not match its owned record")
    receipt = matches[0]
    extension = receipt.get("extension")
    if (receipt.get("state") != "ready" or extension not in {"zip", "7z", "rar"}
            or type(receipt.get("bytes")) is not int or not 0 <= receipt["bytes"] <= MAX_ARCHIVE
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256", "")))):
        raise ValueError("Page download is not completely verified and ready")
    path = real_path(data.get("archivePath", ""))
    expected = real_path(root / download_id / ("archive." + extension))
    if path != expected or path.parent.parent != root:
        raise ValueError("Archive does not belong to the exact download receipt")
    binding = receipt.get("binding") or {}
    association = binding.get("association")
    if context.get("newTarget"):
        if association is not None or binding.get("contentIntent") != "newContent":
            raise ValueError("An existing-mod download cannot be silently retargeted as new content")
    elif (binding.get("contentIntent") != "update" or not isinstance(association, dict) or any(association.get(k) != context.get(k) for k in ("itemId", "profileId"))
          or association.get("game", context.get("game")) != context.get("game")):
        raise ValueError("Downloaded archive belongs to a different exact mod, profile or game")
    identity = file_identity(path, cancel)
    if identity["bytes"] != receipt["bytes"] or identity["sha256"] != receipt["sha256"]:
        raise ValueError("Original archive bytes changed after download verification")
    return identity


def verify_selected_archive(review, original, cancel):
    """Validate selected ZIP provenance without trusting a renderer-built ZIP."""
    if not isinstance(review, dict):
        raise ValueError("Exact archive review is required")
    observed = review.get("archiveIdentity") or {}
    if any(observed.get(k) != original.get(k) for k in ("sha256", "bytes")):
        raise ValueError("Archive review belongs to different original bytes")
    selected = review.get("selectedPaths")
    prepared = review.get("preparedArchive")
    if prepared is None:
        if selected:
            raise ValueError("Selected files require the owned prepared archive")
        return original
    if review.get("requiresInstaller"):
        raise ValueError("FOMOD archives require their complete original installer")
    if not isinstance(prepared, dict):
        raise ValueError("Selected archive has no owned prepared receipt")
    stage = real_path(review.get("stage", ""), directory=True)
    if "entries" not in prepared:
        digest = prepared.get("selectionDigest") or review.get("selectionDigest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Selected archive has no exact receipt identity")
        stored = read_json(stage / ("selected-" + digest + ".json"), 64 * 1024 * 1024)
        if not isinstance(stored, dict) or any(stored.get(k) != v for k, v in prepared.items()):
            raise ValueError("Compact prepared archive receipt differs from its owned complete receipt")
        prepared = stored
    if selected is None:
        entries = prepared.get("entries")
        if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
            raise ValueError("Selected archive has no complete owned entry manifest")
        selected = [row.get("path") for row in entries]
    if not isinstance(selected, list) or not selected or len(selected) > 200000:
        raise ValueError("Selected archive has no complete bounded file selection")
    if any(not isinstance(n, str) for n in selected) or len(set(selected)) != len(selected):
        raise ValueError("Selected archive contains duplicate or invalid file identities")
    marker = read_json(stage / ".modhunter-review-owned.json", 64 * 1024 * 1024)
    if not isinstance(marker, dict) or marker.get("owner") != "Modhunter-archive-review" or marker.get("schema") != 1:
        raise ValueError("Archive review staging is not owned by Modhunter")
    # The Rust marker is a complete original-archive receipt, not an approval
    # based only on an attacker-writable filename or extracted directory.
    if marker.get("archiveSha256") != original["sha256"] or marker.get("archiveBytes") != original["bytes"] or str(real_path(marker.get("archive", ""))) != original["path"]:
        raise ValueError("Selected archive staging belongs to different original bytes")
    manifest_digest = hashlib.sha256((stage / ".modhunter-review-owned.json").read_bytes()).hexdigest()
    if manifest_digest != review.get("manifestDigest"):
        raise ValueError("Selected archive's complete original manifest changed")
    incoming = real_path(review.get("incomingRoot", ""), directory=True)
    if incoming != reviewed_incoming_root(stage, marker):
        raise ValueError("Selected archive incoming tree is outside its owned review")
    output = real_path(prepared.get("path", ""))
    if output.parent != stage or output.suffix.lower() != ".zip":
        raise ValueError("Selected archive is outside its owned review staging")
    if prepared.get("originalSha256") != original["sha256"]:
        raise ValueError("Selected archive original digest changed")
    entries = prepared.get("entries")
    if not isinstance(entries, list) or len(entries) != len(selected):
        raise ValueError("Selected archive manifest does not cover every selected file")
    expected = {}
    for row in entries:
        if not isinstance(row, dict) or row.get("path") in expected:
            raise ValueError("Selected archive manifest has duplicate entries")
        name = row.get("path")
        if (not isinstance(name, str) or not name or "\\" in name or name.startswith("/")
                or any(part in {"", ".", ".."} or ":" in part for part in name.split("/"))
                or name.casefold() == "meta.ini"):
            raise ValueError("Selected archive contains an unsafe file identity")
        expected[name] = row
    if set(expected) != set(selected) or len({n.casefold() for n in expected}) != len(expected):
        raise ValueError("Selected archive selection and complete manifest differ")
    prefix = marker.get("prefix", "")
    original_files = marker.get("files")
    if not isinstance(prefix, str) or not isinstance(original_files, dict):
        raise ValueError("Original archive's complete file manifest is invalid")
    selected_manifest = {}
    for name, row in expected.items():
        original_row = original_files.get(prefix + name)
        if not isinstance(original_row, dict) or any(original_row.get(k) != row.get(k) for k in ("bytes", "sha256")):
            raise ValueError("Selected archive file does not occur in the original archive manifest")
        selected_manifest[name] = {"bytes": row["bytes"], "sha256": row["sha256"]}
    selection_digest = hashlib.sha256(canonical_json({"manifestDigest": manifest_digest,
        "originalSha256": original["sha256"], "selected": selected_manifest})).hexdigest()
    if prepared.get("selectionDigest") != selection_digest or review.get("selectionDigest", selection_digest) != selection_digest:
        raise ValueError("Selected archive's exact selection digest changed")
    if output != stage / ("selected-" + selection_digest + ".zip"):
        raise ValueError("Selected archive does not match its owned selection identity")
    stored = read_json(stage / ("selected-" + selection_digest + ".json"), 64 * 1024 * 1024)
    if stored != prepared:
        raise ValueError("Selected archive's owned receipt changed")
    # The original ZIP remains the independent authority. Rechecking selected
    # member bytes prevents a forged marker/prepared ZIP pair from changing
    # which original files the user reviewed.
    if zipfile.is_zipfile(original["path"]):
        with zipfile.ZipFile(original["path"]) as archive:
            indexed = {info.filename: info for info in archive.infolist()}
            if len(indexed) != len(archive.infolist()):
                raise ValueError("Original archive contains duplicate file identities")
            for name, row in selected_manifest.items():
                info = indexed.get(prefix + name)
                if info is None or info.file_size != row["bytes"] or info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("Reviewed selection does not match the original archive entries")
                digest = hashlib.sha256()
                with archive.open(info) as stream:
                    while chunk := stream.read(1024 * 1024):
                        if cancel.is_set():
                            raise RuntimeError("Original archive selection verification cancelled")
                        digest.update(chunk)
                if digest.hexdigest() != row["sha256"]:
                    raise ValueError("Selected file bytes are not present in the original archive")
    actual = file_identity(output, cancel)
    if any(actual.get(k) != prepared.get(k) for k in ("sha256", "bytes")):
        raise ValueError("Prepared archive bytes changed after file review")
    with zipfile.ZipFile(output) as archive:
        infos = archive.infolist()
        files = [info for info in infos if not info.is_dir()]
        parents = {"/".join(name.split("/")[:index]) for name in expected for index in range(1, len(name.split("/")))}
        if (len(files) != len(expected) or {info.filename for info in files} != set(expected)
                or len({info.filename.casefold() for info in infos}) != len(infos)
                or len(infos) > len(expected) + len(parents)):
            raise ValueError("Prepared archive contains missing, duplicate or unselected files")
        for info in infos:
            if info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Prepared archive contains a link or encrypted entry")
            if info.is_dir():
                if info.filename.rstrip("/") not in parents or info.file_size != 0:
                    raise ValueError("Prepared archive contains an unselected directory")
                continue
            row = expected[info.filename]
            if info.file_size != row.get("bytes") or info.file_size > MAX_ARCHIVE:
                raise ValueError("Selected file size differs from review")
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                while chunk := stream.read(1024 * 1024):
                    if cancel.is_set():
                        raise RuntimeError("Selected archive verification cancelled")
                    digest.update(chunk)
            if digest.hexdigest() != row.get("sha256"):
                raise ValueError("Selected file bytes differ from review")
    return actual


class ContentRunner(RustAuditRunner):
    command = "content-vault"

    def __init__(self, executable=None, *, timeout=1800):
        super().__init__(executable, timeout=timeout)

    def _validate_result(self, result, request):
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise RuntimeError("Content helper did not return a bounded operation result")
        return result

    def run(self, request, *, cancel_event=None):
        try:
            return super().run(request, cancel_event=cancel_event)
        except RuntimeError as exc:
            message = str(exc).replace("Plugin analysis", "Content operation").replace("plugin analysis", "content operation").replace("plugin analyzer", "content recovery helper")
            # A killed atomic transaction retains its journal/staged bytes. Do
            # not repeat the audit runner's read-only claim for this command.
            message = message.replace("No mod files were changed; narrow the profile and retry.", "Inspect the retained transaction before retrying; recovery may be required.")
            raise RuntimeError(message) from exc


class ArchiveRunner(ContentRunner):
    command = "archive-review"


def running_game_processes(context):
    """Inspect actual Windows processes, including launches outside MO2."""
    if os.name != "nt":
        return []  # Portable fixtures; installed MO2 uses the Windows branch.
    from ctypes import wintypes
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise RuntimeError("Windows process inspection failed; close the game and retry")
    names = {str(n).casefold() for n in context.get("gameExecutables", []) if n}
    if not names:
        kernel.CloseHandle(handle)
        raise RuntimeError("Native game executable identity is unavailable")
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        present = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not present and ctypes.get_last_error() not in (0, 18):
            raise RuntimeError("Windows process inventory is unavailable")
        while present:
            if str(entry.szExeFile).casefold() in names:
                found.append({"name": str(entry.szExeFile), "pid": int(entry.th32ProcessID)})
            present = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() not in (0, 18):
            raise RuntimeError("Windows process inventory was incomplete")
    finally:
        kernel.CloseHandle(handle)
    return found


def previous_owner_stopped(job):
    """Positive process-exit proof; a different service nonce alone is insufficient."""
    process_id = job.get("serviceProcessId")
    if type(process_id) is not int or not 0 < process_id < 2 ** 32 or process_id == os.getpid():
        return False
    if os.name != "nt":
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return True
        except (OSError, OverflowError):
            return False
        return False
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, process_id)
    if not handle:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such PID.
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value != 259
    finally:
        kernel.CloseHandle(handle)


def archive_inputs_independent(job):
    """No native mutation began, or its original bytes live in the separate vault."""
    if not job.get("nativeStarted") and not job.get("mutationStarted"):
        return True
    return (job.get("backupVerified") is True and job.get("includesModFiles") is True
            and bool(BACKUP_ID.fullmatch(str(job.get("backupId", "")))))


class ContentRecovery:
    def __init__(self, service, ledger, *, runner=None):
        self.service = service
        self.ledger = Path(ledger)
        self.runner = runner or ContentRunner()
        self._lock = threading.RLock()
        self._contexts = {}
        self._jobs = {}
        self._cancel = {}
        self._requests = {}
        self._lease = None
        self._launched = set()
        self._native_generation = 0
        self._native_session = secrets.token_hex(16)
        self._queue_transitions = []
        self.native_enabled = False
        self.launch_guard_available = False
        self._load()

    def _load(self):
        if not self.ledger.exists():
            return
        try:
            doc = read_json(self.ledger, MAX_LEDGER)
            if doc.get("schema") != 1 or not isinstance(doc.get("jobs"), list) or len(doc["jobs"]) > MAX_JOBS:
                raise ValueError("Unsupported content job journal")
            newly_released = []
            all_previous_workers_stopped = True
            for job in doc["jobs"]:
                if not isinstance(job, dict) or not REVIEW_ID.fullmatch(str(job.get("reviewId", ""))) or job.get("jobId") != job["reviewId"]:
                    raise ValueError("Invalid content job identity")
                if job["jobId"] in self._jobs:
                    raise ValueError("Duplicate content job identity")
                if job.get("state") not in TERMINAL:
                    owner_stopped = previous_owner_stopped(job)
                    all_previous_workers_stopped = all_previous_workers_stopped and owner_stopped
                    job.update(state="interrupted", ok=False, error="MO2 closed before the operation was acknowledged. Inspect the retained backup; this job will not replay.")
                    job["stageReleaseAllowed"] = False
                if (job.get("action") in {"install", "nativeInstall"} and job.get("state") in {"unknown", "interrupted"}
                        and not job.get("stageReleaseAllowed") and previous_owner_stopped(job) and archive_inputs_independent(job)):
                    job["stageReleaseAllowed"] = True
                    job["stageReleaseReason"] = "Original native process exited; archive inputs are independent of retained recovery data"
                    newly_released.append(job)
                self._jobs[job["jobId"]] = job
            if newly_released:
                if all_previous_workers_stopped:
                    self._persist()
                else:
                    for job in newly_released:
                        job["stageReleaseAllowed"] = False
        except Exception as exc:
            # A malformed ledger cannot erase the deduplication boundary.
            self._ledger_error = "Content journal requires inspection before new work: " + str(exc)

    def _persist(self):
        payload = canonical_json({"schema": 1, "jobs": list(self._jobs.values())})
        if len(payload) > MAX_LEDGER:
            raise RuntimeError("Content job history reached its storage bound; preserve and review its journal before continuing")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        pending = self.ledger.with_suffix(".pending")
        with pending.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, self.ledger)

    def _update(self, job, **changes):
        with self._lock:
            job.update(changes, updatedAt=int(time.time() * 1000))
            self._persist()

    def jobs(self):
        with self._lock:
            profile = self.service.profile_id()
            rows = deepcopy([j for j in self._jobs.values() if j.get("profileId") == profile])
            if getattr(self, "_ledger_error", ""):
                for row in rows:
                    row["stageReleaseAllowed"] = False
            return {"ok": True, "jobs": rows,
                    "nativeAvailable": self.native_enabled, "helperAvailable": self.runner.available,
                    "error": getattr(self, "_ledger_error", "")}

    def _ui(self, command, payload, cancel=None, timeout=20):
        from .bridge import UIRequest
        request = UIRequest(command, payload, timeout=timeout)
        key = str(payload.get("jobId") or "")
        with self._lock:
            if key:
                self._requests[key] = request
        self.service._submit_ui(request)
        try:
            while not request.event.wait(0.1):
                if cancel is not None and cancel.is_set():
                    request.cancel_if_pending()
                if request.deadline is not None and time.monotonic() >= request.deadline:
                    request.cancel_if_pending()
            return request.result
        finally:
            with self._lock:
                if self._requests.get(key) is request:
                    self._requests.pop(key, None)

    def capture(self, item_id="", target_name="", *, native=False, backup_id=""):
        try:
            if item_id and target_name:
                raise ValueError("Choose an existing mod or a new target name")
            if target_name:
                valid_target_name(target_name)
            if backup_id:
                if not BACKUP_ID.fullmatch(str(backup_id)) or target_name:
                    raise ValueError("An exact existing backup identity is required for recovery context")
                base = self._ui("content-capture", {"itemId": "", "targetName": ""})
                if not base.get("ok"):
                    return base
                native_context = base["context"]
                receipt = read_json(Path(native_context["vaultRoot"]) / backup_id / "receipt.json", 64 * 1024 * 1024)
                name = valid_target_name(receipt.get("modName"))
                expected_root = real_path(Path(native_context["modsRoot"]) / name, absent=True)
                if (receipt.get("schema") != 1 or receipt.get("id") != backup_id
                        or receipt.get("profileId") != native_context["profileId"] or receipt.get("game") != native_context["game"]
                        or real_path(receipt.get("root", ""), absent=True) != expected_root):
                    raise ValueError("Recovery backup belongs to a different native profile, game or mod root")
                result = self._ui("content-capture", {"recoveryTargetName": name})
            else:
                result = self._ui("content-capture", {"itemId": str(item_id), "targetName": str(target_name)})
            if not result.get("ok"):
                return result
            context = result["context"]
            context_id = "context-" + secrets.token_hex(16)
            with self._lock:
                if (context.get("nativeLease") != self._native_session
                        or context.get("nativeGeneration") != self._native_generation):
                    raise ValueError("Native MO2 state changed during context capture; capture a fresh target")
                if len(self._contexts) >= 128:
                    active = {j.get("contextId") for j in self._jobs.values() if j.get("state") not in TERMINAL}
                    for old in list(self._contexts):
                        if old not in active:
                            self._contexts.pop(old)
                            break
                    else:
                        raise RuntimeError("Too many active content reviews")
                self._contexts[context_id] = deepcopy(context)
            public = {k: context.get(k) for k in ("profileId", "profileName", "game", "gameRuntime", "executionEpoch", "nativeLease", "nativeGeneration", "itemId", "modName", "installedVersion", "newTarget", "missingTarget", "gameRunning")}
            public.update(ok=True, contextId=context_id, target={"itemId": context.get("itemId", ""), "modName": context.get("modName", ""), "version": context.get("installedVersion", "?"), "exists": bool(context.get("modRoot")) and not context.get("newTarget") and not context.get("missingTarget")})
            if native:
                public["nativeContext"] = {k: context.get(k, "") for k in ("modRoot", "vaultRoot", "modsRoot", "profilesRoot")}
            return public
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _context(self, context_id):
        with self._lock:
            value = deepcopy(self._contexts.get(context_id))
        if not value:
            raise ValueError("Content review context expired; capture the active MO2 target again")
        current = self.service.selection_snapshot([])
        if any(value.get(k) != current.get(k) for k in ("profileId", "profileName", "game", "gameRuntime", "executionEpoch")):
            raise ValueError("MO2 profile or execution context changed; review the operation again")
        if value.get("nativeLease") != self._native_session or value.get("nativeGeneration") != self._native_generation:
            raise ValueError("Native MO2 state changed after review; capture a fresh target")
        return value

    @staticmethod
    def _other_targets(snapshot, mod_name):
        # A successful installer may explain only its own target changing.
        # Source edits, another mod's version, or inventory changes require a
        # new review even if no native callback happened to report them.
        return canonical_json({"context": {k: snapshot.get(k) for k in ("profileId", "profileName", "game", "gameRuntime", "loader")},
            "items": sorted((row for row in snapshot.get("items", []) if row.get("mo2InternalName") != mod_name), key=lambda row: str(row.get("id")))})

    def _queued_context(self, job, data):
        try:
            return self._context(job["contextId"])
        except ValueError as original_error:
            with self._lock:
                original = deepcopy(self._contexts.get(job["contextId"]))
                transitions = list(self._queue_transitions)
                generation = self._native_generation
            with self.service._guard:
                intent_epoch = self.service._install_intent_epoch
                epoch = self.service._execution_epoch
            if (not original or original.get("nativeLease") != self._native_session
                    or original.get("queueIntentEpoch") != intent_epoch):
                raise original_error
            cursor = (original.get("executionEpoch"), original.get("nativeGeneration"))
            destination = (epoch, generation)
            proofs = []
            for step in transitions:
                if cursor == destination:
                    break
                if tuple(step["before"]) == cursor and step["intentEpoch"] == intent_epoch:
                    cursor = tuple(step["after"])
                    proofs.append(step["jobId"])
            if cursor != destination or not proofs:
                raise original_error
            review = data.get("review") or (data.get("resolvedArchive") or {}).get("review") or {}
            if not original.get("newTarget") and not (review.get("comparisonDigest") or data.get("comparisonDigest")):
                raise ValueError("This queued installer needs a fresh full content review after the preceding native installation")
            captured = self._ui("content-capture", {"itemId": original.get("itemId", ""),
                "targetName": original["modName"] if original.get("newTarget") else ""})
            if not captured.get("ok"):
                raise ValueError(captured.get("error", "Native queued target could not be recaptured"))
            fresh = captured["context"]
            for key in original:
                if key not in {"executionEpoch", "nativeGeneration", "gameRunning"} and original.get(key) != fresh.get(key):
                    raise ValueError("Queued native target or reviewed source changed after the preceding installation")
            if (fresh.get("executionEpoch"), fresh.get("nativeGeneration")) != destination:
                raise ValueError("Native execution changed while the queued target was being revalidated")
            self._update(job, queueRevalidatedAfter=proofs, queueExecutionEpoch=epoch, queueNativeGeneration=generation)
            # The original contextId/request digest stays immutable. The
            # ordinary install work independently rehashes the owned archive,
            # incoming manifest and exact target comparison before backup.
            return fresh

    def _record_queue_transition(self, job, context, before, result):
        if result.get("ok") is not True or job.get("action") not in {"install", "nativeInstall"}:
            return
        after = self.service.native_snapshot()
        if self._other_targets(before, context["modName"]) != self._other_targets(after, context["modName"]):
            return
        with self.service._guard:
            intent_epoch = self.service._install_intent_epoch
            epoch = self.service._execution_epoch
        with self._lock:
            if context.get("queueIntentEpoch") != intent_epoch:
                return
            origin = (context["executionEpoch"], context["nativeGeneration"])
            destination = (epoch, self._native_generation)
            if origin != destination:
                self._queue_transitions.append({"before": origin, "after": destination,
                    "intentEpoch": intent_epoch, "jobId": job["jobId"]})
                self._queue_transitions = self._queue_transitions[-128:]

    def _new_job(self, data, action, *, context=None):
        review = data.get("reviewId")
        if not isinstance(review, str) or not REVIEW_ID.fullmatch(review):
            raise ValueError("A bounded exact review-<timestamp>-<sequence> identity is required")
        with self._lock:
            if getattr(self, "_ledger_error", ""):
                raise RuntimeError(self._ledger_error)
            old = self._jobs.get(review)
            if old:
                intent_digest = hashlib.sha256(canonical_json(data)).hexdigest()
                if (old.get("downloadId", "") != data.get("downloadId", "") or old.get("action") != action
                        or old.get("profileId") != self.service.profile_id()
                        or old.get("intentDigest") != intent_digest):
                    raise ValueError("Review identity was already used for another operation")
                return old, True
            if len(self._jobs) >= MAX_JOBS or sum(j.get("state") not in TERMINAL for j in self._jobs.values()) >= 8:
                raise RuntimeError("Content operation queue is full; finish or inspect its existing jobs")
            context = context or self._context(str(data.get("contextId") or ""))
            job = {"jobId": review, "reviewId": review, "downloadId": str(data.get("downloadId") or ""),
                   "contextId": str(data.get("contextId") or ""), "action": action, "state": "queued", "ok": True,
                   "nativeLease": self._native_session, "serviceProcessId": os.getpid(), "stageReleaseAllowed": False,
                   "intentDigest": hashlib.sha256(canonical_json(data)).hexdigest(),
                   "profileId": context["profileId"], "itemId": context.get("itemId", ""), "modName": context.get("modName", ""),
                   "createdAt": int(time.time() * 1000), "updatedAt": int(time.time() * 1000)}
            self._jobs[review] = job
            self._cancel[review] = threading.Event()
            try:
                self._persist()
            except Exception:
                self._jobs.pop(review, None)
                self._cancel.pop(review, None)
                raise
            return job, False

    def _accepted(self, job, duplicate=False):
        return {"ok": True, "jobId": job["jobId"], "reviewId": job["reviewId"], "downloadId": job.get("downloadId", ""),
                "state": job["state"], "alreadyAccepted": duplicate}

    def start_install(self, data):
        try:
            with self.service._guard:
                approval_generation = self.service._install_cancel_generation
            if not isinstance(data, dict) or set(data) - {"reviewId", "downloadId", "contextId", "confirm", "targetName", "resolvedArchive", "review", "comparisonDigest"}:
                raise ValueError("Native page installation accepts only owned review and receipt fields")
            if data.get("confirm") != "INSTALL":
                raise ValueError("Confirm the exact reviewed INSTALL operation")
            if not DOWNLOAD_ID.fullmatch(str(data.get("downloadId", ""))):
                raise ValueError("An exact owned page-download identity is required")
            if data.get("targetName"):
                context = self._context(str(data.get("contextId") or ""))
                if not context.get("newTarget") or data["targetName"] != context.get("modName"):
                    raise ValueError("New target name changed after native context review")
            job, duplicate = self._new_job(data, "install")
            if not duplicate:
                try:
                    self.service.queue_content_install(job, deepcopy(data), approval_generation)
                except Exception as exc:
                    cancelled = self._cancel[job["jobId"]].is_set() or approval_generation != self.service._install_cancel_generation
                    self.finish_queued_install(job, data, {"ok": False, "state": "cancelled" if cancelled else "failed", "error": str(exc)})
            return self._accepted(job, duplicate)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def cancel(self, data):
        with self._lock:
            job = self._jobs.get(str(data.get("jobId") or ""))
            if (not job or job.get("reviewId") != data.get("reviewId") or job.get("downloadId", "") != data.get("downloadId", "")
                    or job.get("profileId") != self.service.profile_id()):
                return {"ok": False, "error": "Content job identity changed; refresh before cancelling"}
            event = self._cancel.get(job["jobId"])
            if job["state"] in TERMINAL or event is None:
                return self._accepted(job, True)
            event.set()
            request = self._requests.get(job["jobId"])
            if request:
                request.cancel_if_pending()
            job["cancelRequested"] = True
            self._persist()
        queued = self.service.remove_queued_content(job["jobId"])
        if queued:
            self.finish_queued_install(job, queued["data"], {"ok": False, "state": "cancelled", "error": "Approved queued install cancelled before native mutation"})
        return {"ok": True, "jobId": job["jobId"], "state": job["state"],
                "message": "Pending work will stop. An already-open MO2 installer must finish or be cancelled in its native window."}

    def cancel_install_preparation(self):
        """Disarm preparation only; an actual native transaction retains its lease."""
        with self._lock:
            for ident, event in self._cancel.items():
                job = self._jobs.get(ident, {})
                if job.get("action") not in {"install", "nativeInstall"} or job.get("nativeStarted") or job.get("mutationStarted"):
                    continue
                event.set()
                job["cancelRequested"] = True
                request = self._requests.get(ident)
                if request:
                    request.cancel_if_pending()
            self._persist()

    def finish_queued_install(self, job, data, result):
        """A removed pending item owns no worker, native request, or file lease."""
        with self._lock:
            if job.get("nativeStarted") or job.get("mutationStarted") or self._lease == job["jobId"] or job["jobId"] in self._requests:
                raise RuntimeError("An active native transaction cannot be completed as a queued cancellation")
            self._cancel.pop(job["jobId"], None)
        return self._finish_worker(job, data, result)

    def invalidate(self, *, reason="external", mod_name=""):
        with self._lock:
            active = self._jobs.get(self._lease or "", {})
            owned = (reason == "target" and bool(mod_name) and active.get("nativeStarted") is True
                     and active.get("action") in {"install", "nativeInstall"} and active.get("modName") == mod_name)
            self._native_generation += 1
            # Stop superseded reads/copies promptly, before the debounced MO2
            # snapshot catches up. An already-running native installer or
            # atomic restore must retain ownership until its real outcome.
            for ident, event in self._cancel.items():
                job = self._jobs.get(ident, {})
                if owned and job.get("action") == "install" and job.get("state") == "queued":
                    continue
                if job.get("state") in {"queued", "preparing", "verifying", "scanning"} or (job.get("state") == "installing" and not job.get("nativeStarted")):
                    event.set()
                    job["superseded"] = True
                    request = self._requests.get(ident)
                    if request is not None:
                        request.cancel_if_pending()
        if not owned:
            with self.service._guard:
                self.service._install_intent_epoch += 1

    def shutdown(self):
        with self._lock:
            for event in self._cancel.values():
                event.set()
            for request in self._requests.values():
                request.cancel_if_pending()

    def about_to_run(self, binary):
        with self._lock:
            if self._lease is not None:
                return False
            self._launched.add(str(binary).casefold())
            return True

    def finished_run(self, binary):
        with self._lock:
            self._launched.discard(str(binary).casefold())

    def _worker(self, job, data, operation, *, lock_held=False, context=None):
        cancel = self._cancel[job["jobId"]]
        locked = lock_held
        try:
            if not lock_held:
                locked = self.service._install_lock.acquire(blocking=False)
                if not locked:
                    raise RuntimeError("Another native installer or content transaction is still active")
            context = context or (self._queued_context(job, data) if job.get("action") == "install" else self._context(job["contextId"]))
            before_catalog = self.service.native_snapshot() if job.get("action") in {"install", "nativeInstall"} else None
            self._update(job, state="preparing")
            if cancel.is_set():
                raise RuntimeError("Content operation cancelled before native mutation")
            lease = self._ui("content-lease", {"jobId": job["jobId"], "context": context}, cancel)
            if not lease.get("ok"):
                raise RuntimeError(lease.get("error", "Native mutation lease was rejected"))
            result = operation(job, data, context, cancel)
            if before_catalog is not None:
                self._record_queue_transition(job, context, before_catalog, result)
        except Exception as exc:
            state = "unknown" if job.get("nativeStarted") or job.get("mutationStarted") else "cancelled" if cancel.is_set() else "failed"
            result = {"ok": False, "state": state, "error": str(exc), "backupRetained": bool(job.get("backupId")),
                      "needsReview": job.get("superseded") is True or (state == "failed" and not job.get("nativeStarted") and not job.get("mutationStarted"))}
        finally:
            with self._lock:
                if self._lease == job["jobId"]:
                    self._lease = None
                self._cancel.pop(job["jobId"], None)
            if locked and not lock_held:
                self.service._install_lock.release()
            elif lock_held:
                self.service.release_queued_install_lease(job["jobId"])
        result = self._finish_worker(job, data, result)
        if locked and not lock_held:
            self.service._drain_install_queue()
        return result

    def _finish_worker(self, job, data, result):
        # A terminal acknowledgement must not race the next reviewed command
        # against a lease that this completed worker still owns.
        related = []
        try:
            with self._lock:
                inactive = job["jobId"] not in self._cancel and job["jobId"] not in self._requests and self._lease != job["jobId"]
                if inactive and job.get("action") in {"install", "nativeInstall"} and archive_inputs_independent(job):
                    result["stageReleaseAllowed"] = True
                    result["stageReleaseReason"] = "Native worker stopped; archive inputs are independent of retained recovery data"
                recovered = result.get("ok") is True or (result.get("filesRestored") and (result.get("result") or {}).get("ok") is True)
                if (inactive and recovered and data.get("backupId") and data.get("action") in
                        {"seal", "sealNew", "restore", "restoreNew", "recover", "recoverNew", "drop", "deleteBackup", "deleteNewBackup", "cleanupDisplaced"}):
                    for prior in self._jobs.values():
                        if (prior.get("action") in {"install", "nativeInstall"} and prior.get("state") in {"unknown", "interrupted"}
                                and prior.get("backupId") == data["backupId"] and prior.get("profileId") == job["profileId"]
                                and not prior.get("stageReleaseAllowed") and prior["jobId"] not in self._cancel
                                and prior["jobId"] not in self._requests and self._lease != prior["jobId"]):
                            related.append(prior)
                            prior.update(stageReleaseAllowed=True, stageReleaseRecoveryJobId=job["jobId"],
                                         stageReleaseReason="Exact retained backup was explicitly reconciled")
                self._update(job, **result)
        except Exception as exc:
            with self._lock:
                for prior in related:
                    prior["stageReleaseAllowed"] = False
                    prior.pop("stageReleaseRecoveryJobId", None)
                self._ledger_error = "Content acknowledgement could not be saved; inspect the retained journal before new work: " + str(exc)
                result.update(ok=False, state="unknown" if job.get("nativeStarted") or job.get("mutationStarted") else "failed", error=self._ledger_error, stageReleaseAllowed=False)
                job.update(result)
        return result

    @staticmethod
    def _request(context, action, **extra):
        return {"action": action, "root": context["modRoot"], "vault": context["vaultRoot"],
                "profileId": context["profileId"], "game": context["game"], "modName": context["modName"], **extra}

    def _install_work(self, job, data, context, cancel):
        if not context.get("modRoot"):
            raise ValueError("Choose an exact existing mod or an explicit new mod name")
        if context.get("missingTarget"):
            raise ValueError("This target requires recovery of its retained transaction before installation")
        if data.get("_nativeArchive"):
            original = file_identity(data["_nativeArchive"], cancel)
            installer = original
        else:
            original = verify_acquisition(data.get("resolvedArchive"), job["downloadId"], context, cancel)
            installer = verify_selected_archive(data.get("review") or (data.get("resolvedArchive") or {}).get("review"), original, cancel)
        review = data.get("review") or (data.get("resolvedArchive") or {}).get("review") or {}
        if review.get("stage"):
            verification = ArchiveRunner(self.runner.executable).run({"action": "verify", "archive": original["path"],
                "stage": review["stage"], "expectedSha256": original["sha256"], "expectedBytes": original["bytes"], "selected": []}, cancel_event=cancel)
            if (verification.get("ok") is not True or verification.get("manifestDigest") != review.get("manifestDigest")
                    or verification.get("incomingRoot") != review.get("incomingRoot")
                    or verification.get("requiresInstaller") != review.get("requiresInstaller")):
                raise ValueError("Native archive review changed before content installation")
        comparison = review.get("comparisonDigest") or data.get("comparisonDigest")
        compared = {}
        if comparison:
            if context.get("newTarget") or not re.fullmatch(r"[0-9a-f]{64}", str(comparison)):
                raise ValueError("Content comparison does not belong to an existing reviewed target")
            incoming = real_path(review.get("incomingRoot", ""), directory=True)
            stage = real_path(review.get("stage", ""), directory=True)
            marker = read_json(stage / ".modhunter-review-owned.json", 64 * 1024 * 1024)
            if incoming != reviewed_incoming_root(stage, marker):
                raise ValueError("Reviewed incoming files are outside their owned stage")
            if (marker.get("owner") != "Modhunter-archive-review" or marker.get("archiveSha256") != original["sha256"]
                    or marker.get("archiveBytes") != original["bytes"]
                    or hashlib.sha256((stage / ".modhunter-review-owned.json").read_bytes()).hexdigest() != review.get("manifestDigest")):
                raise ValueError("Content comparison's original archive manifest changed")
            compared = {"other": str(incoming), "expectedDigest": comparison}
            result = self.runner.run(self._request(context, "compare", other=str(incoming)), cancel_event=cancel)
            if result.get("ok") is not True or result.get("digest") != comparison:
                raise ValueError("Installed or incoming files changed after the content review")
        profile_backup = self._ui("content-profile-backup", {"jobId": job["jobId"], "context": context}, cancel)
        if not profile_backup.get("ok"):
            raise RuntimeError(profile_backup.get("error", "Native profile backup failed"))
        action = "prepareNew" if context.get("newTarget") else "snapshot"
        prepared = self.runner.run(self._request(context, action, confirm="INSTALL" if context.get("newTarget") else "BACKUP"), cancel_event=cancel)
        backup = prepared.get("backup") or {}
        if prepared.get("ok") is not True or not BACKUP_ID.fullmatch(str(backup.get("id", ""))):
            raise RuntimeError("Content helper returned no exact backup identity")
        self._update(job, state="verifying", backupId=backup["id"], backup=backup, includesModFiles=True,
                     profileBackup={"ok": True, "files": (profile_backup.get("backup") or {}).get("files", []), "includesModFiles": False})
        # Rehash original/selected archive and target backup after preparation,
        # immediately before the queued native handoff. Qt then revalidates the
        # actual target/profile/game lease before opening any installer dialog.
        if file_identity(installer["path"], cancel) != installer:
            raise ValueError("Installer archive changed after preparation")
        verified_backup = self.runner.run(self._request(context, "verifyPrepared", id=backup["id"], **compared), cancel_event=cancel)
        if verified_backup.get("ok") is not True:
            raise RuntimeError(verified_backup.get("error", "Prepared backup verification was refused; native installation was not opened"))
        if cancel.is_set():
            raise RuntimeError("Install cancelled before opening MO2's installer; backup retained")
        self._update(job, state="installing", backupVerified=True)
        stamp = Path(installer["path"]).stat()
        result = self._ui("content-install", {"jobId": job["jobId"], "context": context,
                    "archive": installer["path"], "archiveIdentity": installer, "archiveStamp": [stamp.st_size, stamp.st_mtime_ns, stamp.st_ino],
                    "backupId": backup["id"], "nativeDownloadId": data.get("_nativeDownloadId")}, cancel, timeout=30)
        if result.get("nativeStarted"):
            self._update(job, nativeStarted=True)
        if not result.get("ok") or not result.get("exactTarget"):
            return {"ok": False, "state": "unknown" if result.get("nativeStarted") else "cancelled" if cancel.is_set() else "failed", "backupRetained": True,
                    "installedName": result.get("installedName", ""), "error": result.get("error", "MO2 did not confirm the exact reviewed target; original bytes remain in the prepared backup")}
        sealed = self.runner.run(self._request(context, "sealNew" if context.get("newTarget") else "seal", id=backup["id"]))
        sealed_backup = sealed.get("backup") or {}
        if (sealed.get("ok") is not True or sealed_backup.get("id") != backup["id"]
                or sealed_backup.get("state") != ("sealedNew" if context.get("newTarget") else "sealed")
                or sealed_backup.get("undoAvailable") is not True):
            return {"ok": False, "state": "unknown", "installedName": result["installedName"], "filesInstalled": True,
                    "refreshRequired": True, "backupRetained": True,
                    "error": sealed.get("error", "Native files were installed, but the exact undo checkpoint could not be sealed. The prepared backup remains retained for recovery review.")}
        self._update(job, state="refreshing", backup=sealed.get("backup", backup), installedName=result["installedName"])
        refreshed = self._ui("content-refresh", {"jobId": job["jobId"], "context": context}, timeout=75)
        if not refreshed.get("ok"):
            return {"ok": False, "state": "failed", "installedName": result["installedName"], "filesInstalled": True,
                    "refreshRequired": True, "backupRetained": True, "error": refreshed.get("error", "MO2 refresh failed after install")}
        from .scanner import version_verdict
        live = self.service.native_selection_snapshot([context.get("itemId", "")])
        target = next((m for m in live.get("items", []) if m.get("mo2InternalName") == context["modName"]), None)
        verified = bool(target and live.get("profileId") == context["profileId"]
                        and version_verdict(context["installedVersion"], str(target.get("installedVersion", ""))) == "candidate")
        if verified:
            self.service.chronicle.record_update(context["profileId"], target, context["installedVersion"],
                target.get("installedVersion", "?"), "mo2-install-complete", str(data.get("_nativeFileId") or job["downloadId"]))
        return {"ok": True, "state": "complete", "installedName": result["installedName"], "verifiedUpdate": verified,
                "backup": sealed.get("backup", backup), "backupRetained": True, "refreshRequired": False,
                "message": "Native MO2 installation completed for the exact target; original mod bytes are retained for undo."}

    def start_recovery(self, data):
        try:
            action = str(data.get("action") or "")
            allowed = {"previewRestore": None, "previewRestoreNew": None, "restore": "RESTORE", "restoreNew": "RESTORE", "recover": "RECOVER", "recoverNew": "RECOVER", "drop": "DROP", "deleteBackup": "DELETE BACKUP", "deleteNewBackup": "DELETE BACKUP", "cleanupDisplaced": "CLEANUP", "seal": "SEAL", "sealNew": "SEAL", "snapshot": "BACKUP", "retentionPreview": None, "retentionApply": "DELETE BACKUPS"}
            if action not in allowed or (allowed[action] and data.get("confirm") != allowed[action]):
                raise ValueError("Confirm the exact requested recovery action")
            if action not in {"snapshot", "retentionPreview", "retentionApply"} and not BACKUP_ID.fullmatch(str(data.get("backupId", ""))):
                raise ValueError("An exact retained backup identity is required")
            if action in {"retentionPreview", "retentionApply"}:
                provided = False
                for key in ("retentionDays", "retentionCount", "retentionBytes"):
                    if key in data and data[key] is not None:
                        if type(data[key]) is not int or not 0 < data[key] < 2 ** 63:
                            raise ValueError("Retention values must be positive bounded integers")
                        provided = True
                if not provided:
                    raise ValueError("Choose at least one explicit backup retention limit")
                if action == "retentionApply" and not re.fullmatch(r"[0-9a-f]{64}", str(data.get("expectedDigest", ""))):
                    raise ValueError("Preview the exact backup retention plan before applying it")
                selected = data.get("selected", [])
                if not isinstance(selected, list) or len(selected) > 2000 or any(not isinstance(v, str) or not BACKUP_ID.fullmatch(v) for v in selected) or len(set(selected)) != len(selected):
                    raise ValueError("Retention selection must contain exact distinct backup identities")
            job, duplicate = self._new_job(data, action)
            if not duplicate:
                threading.Thread(target=self._worker, args=(job, deepcopy(data), self._recovery_work), daemon=True, name="Modhunter-content-recovery").start()
            return self._accepted(job, duplicate)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _recovery_work(self, job, data, context, cancel):
        action = data["action"]
        if action in {"restoreNew", "previewRestoreNew", "recoverNew"}:
            check = self._ui("content-new-ownership", {"jobId": job["jobId"], "context": context}, cancel)
            if not check.get("ok"):
                raise ValueError(check.get("error", "New mod ownership changed; retain its backup"))
        if action in {"restore", "restoreNew"}:
            preview = self.runner.run(self._request(context, "previewRestoreNew" if action == "restoreNew" else "previewRestore", id=data["backupId"],
                **({"other": context["profilesRoot"]} if action == "restoreNew" else {})), cancel_event=cancel)
            if preview.get("ok") is not True or preview.get("canRestore") is not True:
                raise ValueError(preview.get("error", "The exact retained backup did not pass restore preflight"))
        recheck = self._ui("content-check", {"jobId": job["jobId"], "context": context}, cancel)
        if not recheck.get("ok") or cancel.is_set():
            raise RuntimeError(recheck.get("error", "Recovery was cancelled before native mutation"))
        mutates = action in {"restore", "restoreNew", "recover", "recoverNew"}
        if mutates:
            self._update(job, state="restoring", mutationStarted=True, backupId=data["backupId"])
        arguments = {"id": data["backupId"]} if data.get("backupId") else {}
        if data.get("confirm"):
            arguments["confirm"] = data["confirm"]
        if action == "drop":
            action = "deleteBackup"
            arguments["confirm"] = "DELETE BACKUP"
        if action in {"retentionPreview", "retentionApply"}:
            for key in ("retentionDays", "retentionCount", "retentionBytes", "expectedDigest", "selected"):
                if data.get(key) is not None:
                    arguments[key] = data[key]
        if action in {"restoreNew", "previewRestoreNew", "recoverNew"}:
            arguments["other"] = context["profilesRoot"]
        # Do not kill an in-flight rename transaction on user cancellation.
        # The Rust transaction owns crash recovery and its final acknowledgement.
        result = self.runner.run(self._request(context, action, **arguments), cancel_event=None if mutates or action in {"deleteBackup", "deleteNewBackup", "cleanupDisplaced", "retentionApply"} else cancel)
        if result.get("ok") is not True:
            return {"ok": False, "state": "cancelled" if result.get("state") == "cancelled" else "failed",
                    "result": result, "partial": bool(result.get("partial") or result.get("deleted")),
                    "error": result.get("error", "The content operation stopped before completing every requested action")}
        if isinstance(result.get("backup"), dict) and result["backup"].get("id"):
            self._update(job, backupId=result["backup"]["id"], backup=result["backup"], includesModFiles=True)
        if mutates:
            self._update(job, state="refreshing", filesRestored=True)
            refreshed = self._ui("content-refresh", {"jobId": job["jobId"], "context": context}, timeout=75)
            if not refreshed.get("ok"):
                return {"ok": False, "state": "failed", "filesRestored": True, "refreshRequired": True,
                        "error": refreshed.get("error", "Files restored but MO2 refresh did not complete"), "result": result}
        return {"ok": True, "state": "complete", "filesRestored": mutates, "refreshRequired": False, "result": result}

    def backup_list(self, context_id):
        try:
            context = self._context(str(context_id))
            if context.get("modRoot"):
                result = self.runner.run(self._request(context, "list"))
            else:
                result = self.runner.run({"action": "listProfile", "root": context["modsRoot"], "vault": context["vaultRoot"],
                    "profileId": context["profileId"], "game": context["game"], "modName": "__profile_inventory__"})
            if result.get("ok"):
                rows = {r["mo2InternalName"]: r for r in self.service.native_snapshot().get("items", [])}
                for backup in result.get("backups", []):
                    row = rows.get(backup.get("modName"), {})
                    backup["itemId"] = str(row.get("id") or "")
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def start_batch_restore(self, data):
        try:
            rows = data.get("backups")
            if data.get("confirm") != "RESTORE" or not isinstance(rows, list) or not rows or len(rows) > 256:
                raise ValueError("Confirm RESTORE for a nonempty batch of at most 256 exact backups")
            seen = set()
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get("itemId"), str) or not row["itemId"]
                        or not BACKUP_ID.fullmatch(str(row.get("backupId", ""))) or row["itemId"] in seen):
                    raise ValueError("Batch restore requires one exact retained backup per distinct mod")
                if row.get("action", "restore") not in {"restore", "restoreNew"}:
                    raise ValueError("Batch restore actions must be exact existing-mod or new-target undo operations")
                seen.add(row["itemId"])
            job, duplicate = self._new_job(data, "batchRestore")
            if not duplicate:
                threading.Thread(target=self._worker, args=(job, deepcopy(data), self._batch_work), daemon=True, name="Modhunter-batch-restore").start()
            return self._accepted(job, duplicate)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _batch_work(self, job, data, initial_context, cancel):
        prepared = []
        # Preflight every selected backup before the first file-tree mutation.
        for row in data["backups"]:
            if cancel.is_set():
                raise RuntimeError("Batch restore cancelled before mutation")
            captured = self.capture(row["itemId"])
            if not captured.get("ok"):
                raise ValueError(captured.get("error", "Batch target is unavailable"))
            context = self._context(captured["contextId"])
            if any(context.get(k) != initial_context.get(k) for k in ("profileId", "game", "gameRuntime")):
                raise ValueError("Batch spans a different native profile or game")
            is_new = row.get("action") == "restoreNew"
            if is_new:
                ownership = self._ui("content-new-ownership", {"jobId": job["jobId"], "context": context}, cancel)
                if not ownership.get("ok"):
                    raise ValueError(ownership.get("error", "New target is still owned by an enabled profile"))
            preview = self.runner.run(self._request(context, "previewRestoreNew" if is_new else "previewRestore", id=row["backupId"],
                **({"other": context["profilesRoot"]} if is_new else {})), cancel_event=cancel)
            if preview.get("ok") is not True or preview.get("canRestore") is not True:
                raise ValueError(preview.get("error", "Every batch target must pass restore preflight before any files change"))
            prepared.append((row, context))
        completed, failures = [], []
        for index, (row, original) in enumerate(prepared):
            if cancel.is_set():
                return {"ok": False, "state": "cancelled", "partial": bool(completed), "completed": completed,
                        "remaining": [r for r, _ in prepared[index:]], "failed": failures, "error": "Batch stopped after its last acknowledged mod transaction"}
            captured = self.capture(row["itemId"])
            try:
                if not captured.get("ok"):
                    raise ValueError(captured.get("error", "Batch target is unavailable"))
                context = self._context(captured["contextId"])
                if any(context.get(k) != original.get(k) for k in ("profileId", "game", "gameRuntime", "modName", "modRoot", "installedVersion")):
                    raise ValueError("A batch target changed before its transaction")
                result = self._recovery_work(job, {"action": row.get("action", "restore"), "backupId": row["backupId"], "confirm": "RESTORE"}, context, cancel)
                if result.get("filesRestored"):
                    completed.append({**row, "refreshRequired": result.get("refreshRequired", False)})
                if not result.get("ok"):
                    failures.append({**row, "error": result.get("error"), "filesRestored": result.get("filesRestored", False)})
                    return {"ok": False, "state": "failed", "partial": bool(completed), "completed": completed,
                            "remaining": [r for r, _ in prepared[index + 1:]], "failed": failures,
                            "filesRestored": bool(completed), "refreshRequired": result.get("refreshRequired", False)}
            except Exception as exc:
                failures.append({**row, "error": str(exc)})
                return {"ok": False, "state": "failed", "partial": bool(completed), "completed": completed,
                        "remaining": [r for r, _ in prepared[index + 1:]], "failed": failures, "filesRestored": bool(completed)}
            self._update(job, completed=deepcopy(completed), remaining=[r for r, _ in prepared[index + 1:]])
        return {"ok": True, "state": "complete", "completed": completed, "failed": [], "remaining": [], "filesRestored": True, "refreshRequired": False}

    def install_native_download(self, download_id, ticket, epoch, *, cancel=None):
        """Existing Nexus/public API queue uses the same actual-byte safeguards."""
        if cancel is not None and cancel.is_set():
            return {"ok": False, "state": "cancelled", "error": "Native install preparation was cancelled"}
        captured = self.capture(str(ticket.get("itemId") or ""))
        if not captured.get("ok"):
            return captured
        data = {"contextId": captured["contextId"], "reviewId": f"review-{int(time.time() * 1000)}-{len(self._jobs) + 1}",
                "downloadId": "mo2:" + str(download_id), "confirm": "INSTALL"}
        context = self._context(data["contextId"])
        if epoch != context["executionEpoch"]:
            return {"ok": False, "error": "Native download context changed before content backup"}
        archive = self._ui("content-download", {"downloadId": download_id, "context": context})
        if not archive.get("ok"):
            return archive
        data["_nativeArchive"] = archive["archive"]
        data["_nativeFileId"] = ticket.get("fileId")
        data["_nativeDownloadId"] = download_id
        job, _ = self._new_job(data, "nativeInstall", context=context)
        with self.service._guard:
            active = self.service._install_active
            if active and active.get("kind") == "native" and active.get("downloadId") == download_id:
                active.update(job=job, jobId=job["jobId"], reviewId=job["reviewId"])
        if cancel is not None:
            with self._lock:
                self._cancel[job["jobId"]] = cancel
        result = self._worker(job, data, self._install_work, lock_held=True, context=context)
        result["contentJobId"] = job["jobId"]
        return result


class NativeContent:
    """Small Qt-only adapter. It never hashes/copies an installed mod tree."""
    def __init__(self, plugin):
        self.plugin = plugin
        self._refresh_callbacks = {}

    @property
    def service(self):
        return self.plugin.server

    @property
    def content(self):
        return self.service.content

    def _game_identity(self):
        game = self.plugin.org.managedGame()
        name = (call_str(game, "gameNexusName", "") or call_str(game, "gameShortName", "")).lower().replace(" ", "")
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Actual native game identity is unavailable")
        runtime = str(getattr(self.plugin, "_runtime", "unknown") or "unknown")
        if runtime == "unknown":
            runtime = call_str(game, "gameVersion", "unknown")
        if name == "skyrimspecialedition" and runtime == "1.5.97.0":
            runtime = "1.5.97"
        return name, runtime, call_str(game, "binaryName", "")

    def capture(self, payload):
        org = self.plugin.org
        # These identities belong to the same Qt capture as the native roots
        # and target. Assigning a newer generation after the queued result
        # returns would silently renew an already-invalid review.
        with self.content._lock:
            native_session = self.content._native_session
            native_generation = self.content._native_generation
        snapshot = self.service.selection_snapshot([])
        if str(org.profileName()) != snapshot.get("profileName"):
            raise ValueError("Native MO2 profile changed before context capture")
        game_name, runtime, binary = self._game_identity()
        if (game_name, runtime) != (snapshot.get("game"), snapshot.get("gameRuntime")):
            raise ValueError("Actual native game or runtime changed before context capture; refresh MO2")
        root = real_path(str(org.modsPath()), directory=True)
        profile_path = real_path(str(org.profilePath()), directory=True)
        vault = owned_recovery_root(root, profile_path.parent) / "content-vault"
        target_name = str(payload.get("targetName") or "")
        recovery_name = str(payload.get("recoveryTargetName") or "")
        item_id = str(payload.get("itemId") or "")
        context = {k: snapshot.get(k) for k in ("profileId", "profileName", "game", "gameRuntime", "executionEpoch")}
        context.update(nativeLease=native_session, nativeGeneration=native_generation)
        with self.service._guard:
            context["queueIntentEpoch"] = self.service._install_intent_epoch
        context.update(modsRoot=str(root), profilesRoot=str(profile_path.parent), profilePath=str(profile_path),
                       vaultRoot=str(vault), modRoot="", modName="", itemId=item_id, installedVersion="?", newTarget=bool(target_name), nativeGameBinary=binary)
        if item_id:
            source_row = next((m for m in self.service.native_selection_snapshot([item_id]).get("items", []) if m.get("id") == item_id), None)
            context["queueSourceIdentity"] = {key: deepcopy((source_row or {}).get(key)) for key in ("nexusId", "source", "flags")}
        if recovery_name:
            recovery_name = valid_target_name(recovery_name)
            mod = org.modList().getMod(recovery_name)
            path = real_path(str(mod.absolutePath()) if mod is not None else root / recovery_name, absent=True)
            if path.parent != root:
                raise ValueError("Recovery target is outside the native mods directory")
            row = next((m for m in self.service.native_snapshot().get("items", []) if m.get("mo2InternalName") == recovery_name), None)
            version = str(mod.version()) if mod is not None else "?"
            if version in {"", "Invalid", "None", "0", "0.0"}:
                version = "?"
            context.update(modName=recovery_name, modRoot=str(path), itemId=row["id"] if row else "",
                           installedVersion=version, missingTarget=not path.is_dir())
        elif target_name:
            target_name = valid_target_name(target_name)
            names = list(org.modList().allMods())
            if any(str(n).casefold() == target_name.casefold() for n in names) or any(p.name.casefold() == target_name.casefold() for p in root.iterdir()):
                raise ValueError("The new target name already exists in MO2 or its mods directory")
            context.update(modName=target_name, modRoot=str(root / target_name))
        elif item_id:
            rows = self.service.native_selection_snapshot([item_id]).get("items", [])
            if len(rows) != 1:
                raise ValueError("Exact target mod no longer exists in the active MO2 profile")
            row = rows[0]
            mod = org.modList().getMod(row["mo2InternalName"])
            if mod is None:
                raise ValueError("Exact target mod was removed from MO2")
            path = real_path(str(mod.absolutePath()), absent=True)
            if path.parent != root:
                raise ValueError("Target is not a direct mod folder in MO2's native mods directory")
            version = str(mod.version())
            if version in {"", "Invalid", "None", "0", "0.0"}:
                version = "?"
            if version != row.get("installedVersion"):
                raise ValueError("Native mod version changed; refresh the target before review")
            context.update(modName=row["mo2InternalName"], modRoot=str(path), installedVersion=version, missingTarget=not path.is_dir())
        game = org.managedGame()
        binaries = set()
        name = call_str(game, "binaryName", "")
        if name:
            binaries.add(Path(name.replace("\\", "/")).name)
        try:
            for executable in game.executables():
                binary = executable.binary()
                text = str(binary.absoluteFilePath()) if hasattr(binary, "absoluteFilePath") else str(binary)
                if text:
                    binaries.add(Path(text.replace("\\", "/")).name)
        except (AttributeError, TypeError):
            pass
        context["gameExecutables"] = sorted(binaries)
        context["gameRunning"] = bool(self.content._launched or running_game_processes(context))
        with self.content._lock:
            if native_session != self.content._native_session or native_generation != self.content._native_generation:
                raise ValueError("Native MO2 state changed during Qt context capture")
        return context

    def check(self, context, *, post_install=False, allow_missing=False, read_only=False):
        allow_missing = allow_missing or bool(context.get("missingTarget"))
        org = self.plugin.org
        live = self.service.selection_snapshot([])
        for key in ("profileId", "profileName", "game", "gameRuntime"):
            if context.get(key) != live.get(key):
                raise ValueError("MO2 profile or game changed before content execution")
        if str(org.profileName()) != context["profileName"]:
            raise ValueError("Native MO2 profile changed before content execution")
        if self._game_identity() != (context["game"], context["gameRuntime"], context["nativeGameBinary"]):
            raise ValueError("Actual native game, runtime or executable changed after review")
        if context.get("nativeLease") != self.content._native_session:
            raise ValueError("The native MO2 service changed after content review")
        if not post_install and (context.get("executionEpoch") != live.get("executionEpoch") or context.get("nativeGeneration") != self.content._native_generation):
            raise ValueError("MO2 execution lease changed after content review")
        if str(real_path(org.modsPath(), directory=True)) != context["modsRoot"] or str(real_path(org.profilePath(), directory=True)) != context["profilePath"]:
            raise ValueError("MO2 native paths changed after content review")
        mod = org.modList().getMod(context["modName"]) if context.get("modName") else None
        if context.get("newTarget") and not post_install:
            if mod is not None or any(p.name.casefold() == context["modName"].casefold() for p in Path(context["modsRoot"]).iterdir()):
                raise ValueError("The reviewed new mod name became occupied")
        elif context.get("modName"):
            if mod is None and not allow_missing:
                raise ValueError("Native target mod was removed")
            if mod is not None:
                path = real_path(str(mod.absolutePath()), directory=not allow_missing, absent=allow_missing)
                if str(path) != context["modRoot"]:
                    raise ValueError("Native target mod path changed after review")
                if not post_install:
                    version = str(mod.version())
                    if version in {"", "Invalid", "None", "0", "0.0"}:
                        version = "?"
                    if version != context["installedVersion"]:
                        raise ValueError("Native target mod version changed after review")
            if context.get("missingTarget") and Path(context["modRoot"]).exists():
                raise ValueError("A missing recovery target became occupied after review")
        if read_only:
            return
        if not self.content.launch_guard_available:
            raise ValueError("MO2 process launch guard is unavailable; content mutation cannot acquire a safe lease")
        if self.content._launched or running_game_processes(context):
            raise ValueError("Close the running game and MO2-launched tools before changing mod files")

    def handle(self, request, timer):
        payload = request.payload
        if request.cmd == "content-capture":
            request.finish({"ok": True, "context": self.capture(payload)})
            return
        context = payload["context"]
        if request.cmd == "content-refresh":
            self._refresh(request, timer)
            return
        if request.cmd == "content-duplicate-input":
            self.check(context, read_only=True)
            ml = self.plugin.org.modList()
            try:
                import mobase
                active = int(mobase.ModState.ACTIVE)
            except (ImportError, AttributeError, ValueError, TypeError):
                active = 2
            rows, excluded = [], []
            root = Path(context["modsRoot"])
            for name in ml.allMods():
                name = str(name)
                mod = ml.getMod(name)
                if mod is None:
                    raise ValueError("Native duplicate inventory contains an unresolved mod")
                if getattr(mod, "isForeign", lambda: False)() or getattr(mod, "isOverwrite", lambda: False)():
                    excluded.append({"name": name, "reason": "Native MO2 foreign content or overwrite is outside the managed mods directory"})
                    continue
                path = real_path(str(mod.absolutePath()), directory=True)
                if path.parent != root:
                    raise ValueError("Native duplicate inventory contains a mod outside its real mods root")
                rows.append({"name": name, "path": str(path), "enabled": bool(int(ml.state(name)) & active)})
            rows.sort(key=lambda row: row["name"].casefold())
            request.finish({"ok": True, "mods": rows, "outsideManagedRoot": excluded})
            return
        self.check(context)
        if request.cmd == "content-lease":
            with self.content._lock:
                if self.content._lease not in (None, payload["jobId"]):
                    raise ValueError("Another native content transaction holds the mutation lease")
                self.content._lease = payload["jobId"]
            request.finish({"ok": True})
        elif request.cmd == "content-check":
            self._require_lease(payload)
            request.finish({"ok": True})
        elif request.cmd == "content-profile-backup":
            self._require_lease(payload)
            result = self.plugin._profile_backup()
            request.finish({"ok": bool(result.get("ok")), "backup": result, "error": result.get("error", "")})
        elif request.cmd == "content-download":
            source = real_path(str(self.plugin.org.downloadManager().downloadPath(int(payload["downloadId"]))))
            downloads = real_path(str(self.plugin.org.downloadsPath()), directory=True)
            if not source.is_relative_to(downloads) or source.suffix.lower() not in {".zip", ".7z", ".rar"} or not source.is_file():
                raise ValueError("Native download is outside MO2's real downloads directory")
            request.finish({"ok": True, "archive": str(source)})
        elif request.cmd == "content-install":
            self._require_lease(payload)
            if payload.get("nativeDownloadId") is not None:
                from .install_brief import build_install_brief
                download_id = payload["nativeDownloadId"]
                ticket = self.service.downloads().get(download_id)
                if not ticket or ticket.get("status") != "installing":
                    raise ValueError("Native download is no longer reserved for this installer")
                briefing = build_install_brief(self.service.selection_snapshot([context["itemId"]]), {**ticket, "status": "complete"}, download_id)
                if not briefing.get("canInstall"):
                    raise ValueError("Native download evidence changed before installation: " + "; ".join(briefing.get("errors", [])))
            identity = payload["archiveIdentity"]
            source = real_path(payload["archive"])
            info = source.stat()
            if (str(source) != identity["path"] or info.st_size != identity["bytes"]
                    or [info.st_size, info.st_mtime_ns, info.st_ino] != payload["archiveStamp"]):
                raise ValueError("Installer archive changed before native handoff")
            with self.content._lock:
                job = self.content._jobs[payload["jobId"]]
                if self.content._cancel[job["jobId"]].is_set():
                    raise ValueError("Install cancelled before native execution")
                self.content._update(job, nativeStarted=True)
            try:
                installed = self.plugin.org.installMod(str(source), context["modName"])
                if not installed:
                    request.finish({"ok": False, "nativeStarted": True, "error": "MO2 cancelled or did not confirm installation. Original mod bytes remain in the prepared backup; inspect the target before recovery."})
                    return
                name = str(installed.name())
                self.check(context, post_install=True)
                path = real_path(str(installed.absolutePath()), directory=True)
                exact = name == context["modName"] and str(path) == context["modRoot"]
                request.finish({"ok": exact, "nativeStarted": True, "exactTarget": exact, "installedName": name,
                                "error": "" if exact else "Native installer selected another target. Original backup remains prepared; automatic undo was not claimed."})
            except Exception as exc:
                request.finish({"ok": False, "nativeStarted": True, "error": "Native installation result is uncertain: " + str(exc)})
        elif request.cmd == "content-new-ownership":
            self._require_lease(payload)
            self._new_ownership(context)
            request.finish({"ok": True})
        else:
            raise ValueError("Unknown native content command")

    def _require_lease(self, payload):
        if self.content._lease != payload.get("jobId"):
            raise ValueError("Native content lease no longer belongs to this exact job")

    def _new_ownership(self, context):
        # No profile file is rewritten as a side effect of undo. A new mod can
        # only be moved away when every saved/live profile leaves it disabled.
        ml = self.plugin.org.modList()
        try:
            import mobase
            active = int(mobase.ModState.ACTIVE)
        except (ImportError, AttributeError, ValueError, TypeError):
            active = 2
        if ml.getMod(context["modName"]) is not None and int(ml.state(context["modName"])) & active:
            raise ValueError("Disable the new mod before undoing its installation")
        profiles = real_path(context["profilesRoot"], directory=True)
        for folder in profiles.iterdir():
            if not folder.is_dir():
                continue
            real_path(folder, directory=True)
            path = real_path(folder / "modlist.txt")
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("A saved profile exceeds the ownership review bound")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            matches = [line for line in lines if line[:1] in {"+", "-"} and line[1:].casefold() == context["modName"].casefold()]
            if len(matches) > 1 or any(line.startswith("+") for line in matches):
                raise ValueError("A saved profile still enables or ambiguously owns this new mod; keep its recoverable backup")

    def _refresh(self, request, timer):
        self._require_lease(request.payload)
        key = request.payload["jobId"]
        context = request.payload["context"]
        def finished():
            if key not in self._refresh_callbacks:
                return
            self._refresh_callbacks.pop(key, None)
            try:
                if str(self.plugin.org.profileName()) != context["profileName"]:
                    raise ValueError("MO2 profile changed while content refresh was running")
                if self._game_identity() != (context["game"], context["gameRuntime"], context["nativeGameBinary"]):
                    raise ValueError("Native game changed while content refresh was running")
                result = self.plugin._refresh_snapshot(force_metadata=True)
                request.finish(result)
            except Exception as exc:
                request.finish({"ok": False, "error": "MO2 refresh completion failed: " + str(exc), "refreshRequired": True})
        def expired():
            if key in self._refresh_callbacks:
                self._refresh_callbacks.pop(key, None)
                request.finish({"ok": False, "error": "MO2 did not acknowledge its next refresh; files are retained and a manual refresh is required", "refreshRequired": True})
        self._refresh_callbacks[key] = finished
        try:
            if self.plugin.org.onNextRefresh(finished, False) is False:
                raise RuntimeError("Native next-refresh callback was rejected")
            timer.singleShot(60000, expired)
            self.plugin.org.refresh()
        except Exception:
            self._refresh_callbacks.pop(key, None)
            raise
