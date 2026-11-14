"""Complete duplicate-mod reports and explicit, reversible native cleanup.

Python owns MO2's live identities; Rust independently reads all saved profiles
and hashes every mod tree. Reports never infer duplicate ownership from names.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import threading

from .content_recovery import ContentRunner, TERMINAL, canonical_json, read_json, real_path, valid_target_name


class DuplicateRunner(ContentRunner):
    command = "duplicate-report"


def public_report(value):
    if isinstance(value, list):
        return [public_report(row) for row in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"path", "root", "modsRoot", "profilesRoot", "vault"} and isinstance(item, str) and (Path(item).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", item) or item.startswith("\\\\")):
            continue
        result[key] = public_report(item)
    return result


def saved_profiles(profiles_root, mods):
    root = real_path(profiles_root, directory=True)
    names = {row["name"].casefold(): row["name"] for row in mods}
    if len(names) != len(mods):
        raise ValueError("Native mods have colliding Windows identities")
    profiles = []
    total = 0
    for folder in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if not folder.is_dir():
            continue
        real_path(folder, directory=True)
        path = real_path(folder / "modlist.txt")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("A saved profile exceeds the supported complete modlist bound")
        raw = path.read_bytes()
        total += len(raw)
        if total > 64 * 1024 * 1024 or len(profiles) >= 2000:
            raise ValueError("Saved profile inventory exceeds its complete review bound")
        entries = {}
        priority = 0
        for line in raw.decode("utf-8-sig").splitlines():
            if line[:1] not in {"+", "-", "*"}:
                continue
            name = line[1:]
            key = name.casefold()
            if not name or key in entries:
                raise ValueError("Saved profile has ambiguous duplicate mod identities: " + folder.name)
            entries[key] = {"enabled": line[0] == "+", "priority": priority}
            priority += 1
        profiles.append((folder.name, entries))
    if not profiles:
        raise ValueError("No readable saved profiles were available for duplicate ownership review")
    result = deepcopy(mods)
    for row in result:
        row["profiles"] = [{"id": name, **entries.get(row["name"].casefold(), {"enabled": False})} for name, entries in profiles]
    return result


class DuplicateReports:
    def __init__(self, service, directory, *, runner=None):
        self.service = service
        self.content = service.content
        self.directory = Path(directory)
        self.runner = runner or DuplicateRunner()

    def state(self):
        jobs = [j for j in self.content.jobs()["jobs"] if j.get("action", "").startswith("duplicate:")]
        latest = max(jobs, key=lambda j: j.get("createdAt", 0), default=None)
        scanned = max((j for j in jobs if j.get("reportId") and j.get("action") == "duplicate:scan"), key=lambda j: j.get("createdAt", 0), default=None)
        observed = max((j for j in jobs if j.get("reportId")), key=lambda j: j.get("createdAt", 0), default=None)
        result = {"ok": True, "state": latest.get("state") if latest else "idle", "jobId": latest.get("jobId") if latest else "", "helperAvailable": self.runner.available}
        if latest and latest.get("error"):
            result["error"] = latest["error"]
        if scanned:
            try:
                result["report"] = public_report(read_json(self.directory / (scanned["reportId"] + ".json"), 64 * 1024 * 1024))
                result["reportId"] = scanned["reportId"]
            except Exception as exc:
                result.update(ok=False, error="Saved duplicate report could not be read: " + str(exc))
        if observed:
            try:
                result["operationResult"] = public_report(read_json(self.directory / (observed["reportId"] + ".json"), 64 * 1024 * 1024))
                result["operationJobId"] = observed["jobId"]
            except Exception as exc:
                result.update(ok=False, error="Saved duplicate operation could not be read: " + str(exc))
        return result

    def start(self, data):
        try:
            action = data.get("action", "scan")
            if action not in {"scan", "previewQuarantine", "quarantine", "previewRestore", "restore", "recover", "previewDrop", "drop", "list"}:
                raise ValueError("Unknown duplicate review operation")
            if action in {"previewQuarantine", "quarantine"}:
                a, b = valid_target_name(data.get("modName")), valid_target_name(data.get("retainedModName"))
                if a.casefold() == b.casefold():
                    raise ValueError("A duplicate cleanup requires a distinct retained counterpart")
                if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("expectedDigest", ""))):
                    raise ValueError("Review the complete current duplicate report before selecting cleanup")
            if action == "quarantine" and data.get("confirm") != "QUARANTINE":
                raise ValueError("Confirm QUARANTINE for the exact disabled duplicate and retained counterpart")
            if action in {"previewRestore", "restore", "recover", "previewDrop", "drop"} and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", str(data.get("id", ""))):
                raise ValueError("Choose an exact retained duplicate quarantine receipt")
            if action == "restore" and (data.get("confirm") != "RESTORE" or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("expectedDigest", "")))):
                raise ValueError("Preview the exact duplicate undo and confirm RESTORE with its current digest")
            if action == "recover" and data.get("confirm") != "RECOVER":
                raise ValueError("Confirm RECOVER for the exact interrupted duplicate transaction")
            if action == "drop" and (data.get("confirm") != "DELETE QUARANTINE" or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("expectedDigest", "")))):
                raise ValueError("Preview the exact retained quarantine record and confirm DELETE QUARANTINE with its digest")
            job, duplicate = self.content._new_job(data, "duplicate:" + action)
            if not duplicate:
                if action in {"quarantine", "restore", "recover", "drop"}:
                    worker = lambda: self.content._worker(job, deepcopy(data), self._mutate)
                else:
                    worker = lambda: self._read_worker(job, deepcopy(data))
                threading.Thread(target=worker, daemon=True, name="Modhunter-duplicate-review").start()
            return self.content._accepted(job, duplicate)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _input(self, job, data, context, cancel):
        native = self.content._ui("content-duplicate-input", {"jobId": job["jobId"], "context": context}, cancel)
        if not native.get("ok"):
            raise RuntimeError(native.get("error", "Native duplicate inventory is unavailable"))
        data["_nativeExclusions"] = native.get("outsideManagedRoot", [])
        mods = saved_profiles(context["profilesRoot"], native["mods"])
        digest = hashlib.sha256(canonical_json({"profileId": context["profileId"], "game": context["game"],
            "gameRuntime": context["gameRuntime"], "executionEpoch": context["executionEpoch"], "nativeMods": native["mods"]})).hexdigest()
        request = {"action": data.get("action", "scan"), "modsRoot": context["modsRoot"], "profilesRoot": context["profilesRoot"],
            "vault": str(Path(context["vaultRoot"]).with_name("duplicate-vault")), "profileId": context["profileId"], "game": context["game"],
            "mods": mods, "contextDigest": digest}
        for key in ("expectedDigest", "modName", "retainedModName", "id", "confirm"):
            if key in data:
                request[key] = data[key]
        return request

    def _read_worker(self, job, data):
        cancel = self.content._cancel[job["jobId"]]
        try:
            context = self.content._context(job["contextId"])
            self.content._update(job, state="scanning")
            request = self._input(job, data, context, cancel)
            result = self.runner.run(request, cancel_event=cancel)
            if result.get("ok") is not True:
                raise RuntimeError(result.get("error", "Complete duplicate report was not produced"))
            result["outsideManagedRoot"] = data.get("_nativeExclusions", [])
            self.content._context(job["contextId"])
            if cancel.is_set():
                raise RuntimeError("Duplicate report cancelled before publication")
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / (job["jobId"] + ".json")
            payload = canonical_json(result)
            if len(payload) > 64 * 1024 * 1024:
                raise ValueError("Complete duplicate report exceeds the supported artifact bound")
            pending = path.with_suffix(".pending")
            with pending.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
            summary = {"digest": result.get("digest"), "groups": len(result.get("groups", [])), "reportAvailable": True}
            for key in ("canRestore", "canQuarantine", "record"):
                if key in result:
                    summary[key] = public_report(result[key])
            self.content._update(job, ok=True, state="complete", reportId=job["jobId"], result=summary)
        except Exception as exc:
            self.content._update(job, ok=False, state="cancelled" if cancel.is_set() else "failed", error=str(exc))
        finally:
            with self.content._lock:
                self.content._cancel.pop(job["jobId"], None)

    def _mutate(self, job, data, context, cancel):
        request = self._input(job, data, context, cancel)
        # All live native names/enabled states are captured anew, while Rust
        # verifies full directory inventory, every saved modlist, and both
        # complete content trees against the original reviewed digest.
        checked = self.content._ui("content-check", {"jobId": job["jobId"], "context": context}, cancel)
        if not checked.get("ok") or cancel.is_set():
            raise ValueError(checked.get("error", "Duplicate operation cancelled before mutation"))
        self.content._update(job, state="quarantining" if data["action"] == "quarantine" else "restoring", mutationStarted=True)
        result = self.runner.run(request)
        if result.get("ok") is not True:
            return {"ok": False, "state": "failed", "result": result, "error": result.get("error", "Duplicate transaction did not complete")}
        if data["action"] == "drop":
            return {"ok": True, "state": "complete", "quarantineDeleted": True, "result": result, "refreshRequired": False}
        self.content._update(job, state="refreshing", filesRestored=data["action"] == "restore", result=result)
        refreshed = self.content._ui("content-refresh", {"jobId": job["jobId"], "context": context}, timeout=75)
        if not refreshed.get("ok"):
            return {"ok": False, "state": "failed", "filesRestored": data["action"] == "restore", "filesQuarantined": data["action"] == "quarantine",
                    "refreshRequired": True, "result": result, "error": refreshed.get("error", "MO2 refresh failed after duplicate transaction")}
        return {"ok": True, "state": "complete", "filesRestored": data["action"] == "restore", "filesQuarantined": data["action"] == "quarantine",
                "refreshRequired": False, "result": result}
