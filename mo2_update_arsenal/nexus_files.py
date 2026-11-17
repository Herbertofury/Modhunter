"""Exact Nexus archive identity and author-declared replacement chains.

An optional file is its own release lineage. Project versions, a similar name,
the largest file ID, and MAIN category are never evidence of its replacement.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PureWindowsPath

from .version_evidence import compare_versions

FILE_EVIDENCE_SCHEMA = 1
ACTIVE_CATEGORIES = frozenset({1, 2, 3, 5})


def positive_id(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        result = int(value)
        return result if result > 0 and str(value).strip() == str(result) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def archive_name(value) -> str:
    """MO2 stores an absolute Windows archive path or the original basename."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ""
    return PureWindowsPath(value).name


def installed_file_identity(item: dict) -> tuple:
    """Hashable row ownership key, including same-version reinstalls/options."""
    pairs = []
    for value in item.get("installedFiles") or []:
        if isinstance(value, dict):
            mid, fid = positive_id(value.get("modId")), positive_id(value.get("fileId"))
            if mid and fid:
                pairs.append((mid, fid))
    return (tuple(sorted(set(pairs))), archive_name(item.get("installationFile")),
            str(item.get("ignoredVersion") or ""))


def installed_file_context(item: dict) -> dict:
    """JSON-stable installed archive identity for native approval receipts."""
    files, name, ignored = installed_file_identity(item)
    return {"files": [{"modId": mid, "fileId": fid} for mid, fid in files],
            "archiveName": name, "ignoredVersion": ignored}


def normalize_file_payload(payload: object, game: str, mod_id: int) -> dict:
    """Retain every valid file/edge; malformed entries invalidate completeness.

    Both the raw Nexus JSON and the owned MO2 bridge's normalized response use
    this representation. No row count limit silently drops later optional files.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise ValueError("Nexus did not return a complete file list")
    for key in ("mod_id", "modId"):
        if key in payload and positive_id(payload[key]) != mod_id:
            raise ValueError("Nexus file list belongs to a different project")
    if payload.get("domain_name", payload.get("game", game)) != game:
        raise ValueError("Nexus file list belongs to a different game")
    files, issues, by_id = [], [], {}
    for row in payload["files"]:
        if not isinstance(row, dict):
            issues.append("A Nexus file entry was not an object.")
            continue
        fid = positive_id(row.get("fileId", row.get("file_id")))
        try:
            category = int(row.get("category", row.get("category_id", 0)) or 0)
            size_kb = (int(row["size_in_bytes"]) // 1024 if row.get("size_in_bytes") is not None
                       else int(row.get("sizeInKB", row.get("size_kb", row.get("size", 0))) or 0))
        except (ValueError, TypeError, OverflowError):
            issues.append("A Nexus file has invalid category or size metadata.")
            continue
        if not fid:
            issues.append("A Nexus file has an invalid file ID.")
            continue
        file = {"fileId": fid, "name": str(row.get("name") or row.get("fileName") or row.get("file_name") or f"File {fid}"),
                "version": str(row.get("version") or ""), "category": category,
                "sizeInKB": max(0, size_kb), "fileName": str(row.get("fileName", row.get("file_name", "")) or ""),
                "description": str(row.get("description") or ""),
                "uploaded_time": str(row.get("uploaded_time", row.get("uploaded", "")) or "")}
        if fid in by_id:
            if by_id[fid] != file:
                issues.append(f"Conflicting Nexus records for file {fid}.")
            continue
        by_id[fid] = file
        files.append(file)
    updates_available = payload.get("updatesAvailable", "file_updates" in payload or "fileUpdates" in payload) is True
    raw_updates = payload.get("fileUpdates", payload.get("file_updates", []))
    updates, seen, edge_names = [], set(), {}
    if not isinstance(raw_updates, list):
        issues.append("Nexus replacement history was not an array.")
        raw_updates = []
    for row in raw_updates:
        if not isinstance(row, dict):
            issues.append("A Nexus replacement entry was not an object.")
            continue
        old, new = positive_id(row.get("oldFileId", row.get("old_file_id"))), positive_id(row.get("newFileId", row.get("new_file_id")))
        if not old or not new or old == new:
            issues.append("Nexus supplied an invalid replacement link.")
            continue
        edge = {"oldFileId": old, "newFileId": new,
                "oldFileName": str(row.get("oldFileName", row.get("old_file_name", "")) or ""),
                "newFileName": str(row.get("newFileName", row.get("new_file_name", "")) or "")}
        key = tuple(edge.values())
        pair = (old, new)
        names = (edge["oldFileName"], edge["newFileName"])
        if pair in edge_names and edge_names[pair] != names:
            issues.append(f"Conflicting archive names for Nexus replacement {old} to {new}.")
        edge_names[pair] = names
        if key not in seen:
            seen.add(key)
            updates.append(edge)
    files.sort(key=lambda row: (row["category"] != 1, -row["fileId"]))
    supplied_issues = payload.get("issues", [])
    if not isinstance(supplied_issues, list) or any(not isinstance(value, str) for value in supplied_issues):
        supplied_issues = ["Nexus file-list issues were malformed."]
    issues = list(dict.fromkeys([*supplied_issues, *issues]))
    return {"schema": FILE_EVIDENCE_SCHEMA, "game": game, "modId": mod_id,
            "files": files, "fileUpdates": updates, "updatesAvailable": updates_available,
            "complete": payload.get("complete") is not False and not issues,
            "issues": issues}


def valid_file_payload(payload: object, game: str, mod_id: int) -> bool:
    if not (isinstance(payload, dict) and type(payload.get("schema")) is int
            and payload["schema"] == FILE_EVIDENCE_SCHEMA
            and payload.get("game") == game and type(payload.get("modId")) is int
            and payload["modId"] == mod_id and payload.get("complete") is True
            and payload.get("updatesAvailable") is True
            and payload.get("issues", []) == []
            and isinstance(payload.get("files"), list) and isinstance(payload.get("fileUpdates"), list)):
        return False
    seen = set()
    for row in payload["files"]:
        if (not isinstance(row, dict) or type(row.get("fileId")) is not int or row["fileId"] <= 0
                or row["fileId"] in seen or type(row.get("category")) is not int
                or any(not isinstance(row.get(key), str) for key in ("name", "fileName", "version"))):
            return False
        seen.add(row["fileId"])
    pairs = {}
    for edge in payload["fileUpdates"]:
        if (not isinstance(edge, dict)
                or any(type(edge.get(key)) is not int or edge[key] <= 0 for key in ("oldFileId", "newFileId"))
                or edge["oldFileId"] == edge["newFileId"]
                or any(not isinstance(edge.get(key), str) for key in ("oldFileName", "newFileName"))):
            return False
        pair = (edge["oldFileId"], edge["newFileId"])
        names = (edge["oldFileName"], edge["newFileName"])
        if pair in pairs and pairs[pair] != names:
            return False
        pairs[pair] = names
    return True


class FileLineage:
    """Index one complete project once, then evaluate each installed row."""

    def __init__(self, payload: dict, game: str, mod_id: int):
        self.payload = payload if isinstance(payload, dict) else {}
        self.game, self.mod_id = game, mod_id
        self.valid = valid_file_payload(payload, game, mod_id)
        self.files = {row["fileId"]: row for row in payload.get("files", [])} if self.valid else {}
        self.next = defaultdict(set)
        self.names = defaultdict(set)
        self.file_names = defaultdict(set)
        for row in self.files.values():
            if name := archive_name(row.get("fileName")):
                self.names[name.casefold()].add(row["fileId"])
                self.file_names[row["fileId"]].add(name)
        for edge in payload.get("fileUpdates", []) if self.valid else []:
            self.next[edge["oldFileId"]].add(edge["newFileId"])
            for name_key, id_key in (("oldFileName", "oldFileId"), ("newFileName", "newFileId")):
                if name := archive_name(edge.get(name_key)):
                    self.names[name.casefold()].add(edge[id_key])
                    self.file_names[edge[id_key]].add(name)
        self.targets = {}

    def _target(self, installed: int) -> tuple[int | None, str]:
        if installed in self.targets:
            return self.targets[installed]
        path, seen, current = [], set(), installed
        while True:
            if current in seen:
                result = (None, "The publisher's replacement history contains a cycle; choose the file manually.")
                break
            seen.add(current)
            path.append(current)
            successors = self.next.get(current, set())
            if len(successors) > 1:
                result = (None, "This installed file branches into several replacements; choose the intended option manually.")
                break
            if not successors:
                result = (current, "")
                break
            current = next(iter(successors))
        for fid in path:
            self.targets[fid] = result
        return result

    def evaluate(self, item: dict) -> dict:
        payload = self.payload
        file_name = archive_name(item.get("installationFile"))
        evidence = {"status": "unmatched", "basis": "installed-file-id", "installedFileIds": [],
                    "installedFileName": file_name, "updated": False}

        def result(verdict: str, reason: str, **extra) -> dict:
            evidence["reason"] = reason
            return {"verdict": verdict, "reason": reason, "latestVersion": "", "fileEvidence": dict(evidence), **extra}

        if (not self.valid or positive_id(item.get("nexusId")) != self.mod_id
                or item.get("sourceGame", self.game) != self.game):
            evidence["status"] = "unavailable"
            return result("review", "Complete Nexus files and replacement history are unavailable; project versions cannot identify this installed option.")
        recorded = installed_file_identity(item)[0]
        if any(mid != self.mod_id for mid, _ in recorded):
            evidence["status"] = "ambiguous"
            return result("review", "This MO2 row combines files from several Nexus projects; review its merged content before updating.")
        installed = {fid for mid, fid in recorded if mid == self.mod_id}
        if not installed and file_name:
            evidence["basis"] = "archive-name"
            installed = self.names.get(file_name.casefold(), set())
            if len(installed) > 1:
                evidence["status"] = "ambiguous"
                return result("review", "Several Nexus file IDs share the installed archive name; the installed file needs manual identification.")
        evidence["installedFileIds"] = sorted(installed)
        if not file_name and len(installed) == 1:
            known_names = self.file_names.get(next(iter(installed)), set())
            if len(known_names) == 1:
                evidence["installedFileName"] = next(iter(known_names))
        if not installed:
            return result("review", "MO2 has no matching installed Nexus file ID or exact archive name. Identify the installed option before checking its replacement.")
        targets = set()
        for fid in installed:
            target, error = self._target(fid)
            if error:
                evidence["status"] = "ambiguous"
                return result("review", error)
            targets.add(target)
        if len(targets) != 1:
            evidence["status"] = "ambiguous"
            return result("review", "This MO2 row contains several file lineages. Review its merged files separately.")
        target_id = next(iter(targets))
        target = self.files.get(target_id)
        if target is None:
            return result("review", "The installed file or its replacement is missing, removed, or hidden in the current Nexus file list.")
        evidence.update(targetFileId=target_id, targetFileName=target.get("fileName") or target.get("name") or "",
                        targetFileLabel=target.get("name") or "",
                        targetVersion=target.get("version") or "", updated=any(fid != target_id for fid in installed))
        if target.get("category") not in ACTIVE_CATEGORIES:
            return result("review", "The matched file is archived, old, removed, or has unknown status; no active replacement was verified.")
        latest = str(target.get("version") or "")
        if not latest:
            return result("review", "The exact matched file has no version label; review the publisher's file details.")
        evidence["status"] = "matched"
        comparison = compare_versions(item.get("installedVersion", ""), latest)
        ignored = str(item.get("ignoredVersion") or "")
        if ignored and compare_versions(ignored, latest)["verdict"] == "current":
            return result("review", "MO2 marks this exact target version as ignored; review that choice before updating.", latestVersion=latest,
                          versionComparison=comparison, ignored=True)
        if evidence["updated"]:
            if comparison["verdict"] == "newer-installed":
                return result("review", "The publisher's replacement has a lower version than MO2's installed label; review before replacing files.",
                              latestVersion=latest, versionComparison=comparison)
            return result("candidate", "Nexus explicitly links the installed file to this active replacement. Review runtime, dependencies, and installer options.",
                          latestVersion=latest, versionComparison=comparison)
        if comparison["verdict"] == "candidate":
            return result("review", "The same installed file ID now has a different version label; no replacement archive was verified.",
                          latestVersion=latest, versionComparison=comparison)
        return result(comparison["verdict"], "The exact installed file is still active and has no published replacement." if comparison["verdict"] == "current" else comparison["reason"],
                      latestVersion=latest, versionComparison=comparison)
