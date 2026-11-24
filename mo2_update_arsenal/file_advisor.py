"""Conservative, per-MO2-row guidance for choosing exact Nexus file archives.

The advisor annotates file lists without removing entries or claiming game/runtime
compatibility. Recommendations require an active, verified successor of the
installed file in the same provider release line.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
import math
import time

from .version_evidence import compare_versions, normalized_version


def nexus_file_id(value: Any) -> int:
    """Accept only positive integer identities, never lossy numeric coercion."""
    if type(value) is int:
        return value if value > 0 else 0
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 20:
        return int(value) if int(value) > 0 else 0
    return 0


def index_nexus_files(files: Any) -> dict[int, dict | None]:
    """Index exact IDs; duplicate IDs remain ambiguous even with equal metadata.

    Malformed rows cannot match, and do not hide unrelated valid files.
    """
    indexed: dict[int, dict | None] = {}
    if not isinstance(files, list):
        return indexed
    for file in files:
        identifier = nexus_file_id(file.get("fileId")) if isinstance(file, dict) else 0
        if identifier:
            indexed[identifier] = None if identifier in indexed else file
    return indexed


# Nexus categories used by MO2 for an active downloadable file.
ACTIVE_FILES = {1, 2, 3, 5}


class NexusFileIndex:
    """One provider result shared by every installed row belonging to the project."""

    def __init__(self, catalog):
        files = catalog.get("files", [])
        self.by_id = index_nexus_files(files)
        self.archives = {}
        self.successors = {}
        self.active_by_group = {}
        self.has_groups = catalog.get("providerGroups") is True
        self.has_history = isinstance(catalog.get("file_updates"), list)

        def archive(name, identifier):
            if name and identifier:
                self.archives.setdefault(str(name).casefold(), set()).add(identifier)

        for row in files:
            archive(row.get("fileName"), nexus_file_id(row.get("fileId")))
            if self.has_groups and row.get("category") in ACTIVE_FILES and row.get("groupId"):
                self.active_by_group.setdefault(row["groupId"], []).append(row)
        for edge in catalog.get("file_updates") or []:
            if not isinstance(edge, dict):
                self.has_history = False
                continue
            old, new = nexus_file_id(edge.get("old_file_id")), nexus_file_id(edge.get("new_file_id"))
            if not old or not new:
                self.has_history = False
                continue
            self.successors.setdefault(old, set()).add(new)
            archive(edge.get("old_file_name"), old)
            archive(edge.get("new_file_name"), new)

    def resolve(self, item):
        def review(reason):
            return {"verdict": "review", "latestVersion": "", "reason": reason}
        if item.get("installedFileEvidenceIncomplete"):
            return review("MO2's installed-file receipt is incomplete. Review this installation's identity.")
        receipts = item.get("installedFiles") or []
        ids = set()
        for pair in receipts:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                return review("MO2's installed-file receipt is incomplete.")
            project, fid = map(nexus_file_id, pair)
            if project != nexus_file_id(item.get("nexusId")) or not fid:
                return review("MO2 recorded mixed projects or incomplete file identities for this installation.")
            ids.add(fid)
        archive = str(item.get("installationArchive") or "").casefold()
        names = self.archives.get(archive, set())
        if len(ids) > 1 or len(names) > 1 or (ids and names and ids != names):
            return review("Several installed file identities are possible. Review the installation or merge.")
        if not ids and not names:
            return review("The provider could not identify the installed file from its MO2 receipt or archive name.")
        fid = next(iter(ids or names))
        if self.has_groups:
            installed_row = self.by_id.get(fid)
            if not installed_row or not installed_row.get("groupId"):
                return review("The installed Nexus file has no unambiguous provider release group.")
            active = self.active_by_group.get(installed_row["groupId"], [])
            if len(active) != 1 or self.by_id.get(active[0]["fileId"]) is None:
                return review("The installed file group has no unique active release. Review its variants or withdrawal.")
            return self.compare(item, fid, active[0])
        if not self.has_history:
            return review("Provider file history is incomplete. A newer compatible successor could not be verified.")
        current = fid
        seen = set()
        target = None
        while current not in seen:
            seen.add(current)
            row = self.by_id.get(current)
            if current in self.by_id and row is None:
                return review("The provider lists a duplicate file ID. Review the archive identity.")
            if row and row["category"] in ACTIVE_FILES:
                target = row
            destinations = self.successors.get(current)
            if not destinations:
                break
            if len(destinations) != 1:
                return review("The installed file has several published successors. Review the intended variant.")
            current = next(iter(destinations))
        else:
            return review("The provider file history contains a cycle.")
        if not target or current != target["fileId"]:
            return review("The provider's terminal file is absent, obsolete or withdrawn. Review the release history.")
        return self.compare(item, fid, target)

    @staticmethod
    def compare(item, fid, target):
        installed = item.get("installedVersion")
        comparison = compare_versions(installed, target.get("version"))
        verdict = comparison["verdict"]
        if target["fileId"] != fid and verdict == "current":
            verdict = "review"
            reason = "The provider replaced the archive without changing its version. Review the replacement."
        elif fid != target["fileId"] and verdict == "newer-installed":
            verdict = "review"
            reason = "The provider replaced the archive with a lower version. Review the author's release history."
        else:
            reason = f"MO2 installed file {fid}; exact Nexus file {target['fileId']} ({target['name']}). " + comparison["reason"]
        return {"verdict": verdict, "latestVersion": target["version"], "versionComparison": comparison,
                "installedFileId": fid, "targetFileId": target["fileId"], "evidenceScope": "exact-file", "reason": reason}


def _comparison_version(value):
    version = normalized_version(str(value or ""))
    if version is None:
        return None
    end = len(version)
    while end > 1 and version[end - 1] == 0:
        end -= 1
    return version[:end]


def advise_nexus_files(item: dict, files: list[dict]) -> dict:
    """Retain every choice. Only a fresh exact-file decision can suggest a replacement."""
    update = item.get("update") or {}
    latest = str(update.get("latestVersion") or "")
    target = nexus_file_id(update.get("targetFileId"))
    installed = nexus_file_id(update.get("installedFileId"))
    candidate = update.get("source") == "nexus-api" and update.get("verdict") == "candidate"
    try:
        start, end = update["checkedAt"], update["expiresAt"]
        fresh = type(start) in (int, float) and type(end) in (int, float) and 0 < start <= time.time() < end < math.inf
    except KeyError:
        fresh = False
    trusted = (candidate and fresh and installed and target and target != installed
               and update.get("evidenceScope") == "exact-file")
    published = _comparison_version(latest) if candidate else None
    unique_target = trusted and sum(nexus_file_id(raw.get("fileId")) == target
                                    for raw in files if isinstance(raw, dict)) == 1
    selected, matching_mains, matching_options, mains, alternates = [], [], [], [], []
    match_count = 0
    for raw in files:
        if not isinstance(raw, dict):
            continue
        file = deepcopy(raw)
        category = nexus_file_id(file.get("category"))
        fid = nexus_file_id(file.get("fileId"))
        same = published is not None and _comparison_version(file.get("version")) == published
        if unique_target and fid == target and published is None:
            same = compare_versions(file.get("version"), latest)["verdict"] == "current"
        exact = bool(unique_target and fid == target and category in ACTIVE_FILES and same)
        if category == 1 and same:
            match_count += 1
        if exact:
            tier, bucket = "exact-file", selected
        elif same:
            tier, bucket = ("matches-project", matching_mains) if category == 1 else ("matching-option", matching_options)
        else:
            tier, bucket = ("main-file", mains) if category == 1 else ("alternate", alternates)
        file.update(releaseVersionMatch=same, suggested=exact, reviewTier=tier,
                    reviewHint="Verified successor of the installed file. Verify runtime and prerequisites." if exact
                    else "Review this archive's purpose, edition and runtime before selecting it.")
        bucket.append(file)
    ranked = selected + matching_mains + matching_options + mains + alternates
    return {"files": ranked, "reviewAdvice": {"hasVerifiedSuccessor": bool(selected),
            "hasUniqueMainMatch": match_count == 1, "matchingMainFiles": match_count,
            "fileCount": len(ranked), "scope": "Exact installed file and provider succession; compatibility requires review."}}
