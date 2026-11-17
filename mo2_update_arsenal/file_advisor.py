"""Conservative, per-MO2-row guidance for choosing exact Nexus file archives.

The advisor annotates file lists without removing entries or claiming game/runtime
compatibility. Recommendations require the exact replacement ID proven by the
installed file's Nexus lineage; optional files remain optional files.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .scanner import normalized_version
from .nexus_files import ACTIVE_CATEGORIES, positive_id
from .version_evidence import compare_versions


def _same_numeric_version(a: Any, b: Any) -> bool:
    x, y = normalized_version(str(a or "")), normalized_version(str(b or ""))
    if x is None or y is None:
        return False
    n = max(len(x), len(y))
    return x + (0,) * (n - len(x)) == y + (0,) * (n - len(y))


def advise_nexus_files(item: dict, files: list[dict]) -> dict:
    """Return every file with a plain-language, non-authorizing review signal.

    Names, MAIN category, and project-version equality never identify a
    replacement. An existing exact-file verdict still does not prove runtime
    compatibility or that all required installer options have been selected.
    """
    update = item.get("update") or {}
    latest = str(update.get("latestVersion") or "")
    source = str(update.get("source") or "")
    evidence = update.get("fileEvidence") or {}
    target_id = positive_id(evidence.get("targetFileId"))
    trusted_version = (source == "nexus-api" and str(update.get("verdict")) == "candidate"
                       and evidence.get("status") == "matched" and evidence.get("updated") is True and target_id > 0)
    ranked: list[dict] = []
    for order, raw in enumerate(files):
        if not isinstance(raw, dict):
            continue
        file = deepcopy(raw)
        try:
            category = int(file.get("category") or 0)
        except (TypeError, ValueError, OverflowError):
            category = 0
        exact = bool(trusted_version and positive_id(file.get("fileId")) == target_id
                     and category in ACTIVE_CATEGORIES
                     and compare_versions(file.get("version"), latest)["verdict"] == "current")
        file["releaseVersionMatch"] = exact
        file["reviewTier"] = "matches-project" if exact else "main-file" if category == 1 else "alternate"
        file["suggested"] = False
        file["reviewHint"] = ("Main file; review variants, prerequisites and your game runtime" if category == 1
                              else "Optional or alternate file; verify which installer option you need")
        ranked.append((order, file))

    # Even duplicate/conflicting records for the target cannot authorize a
    # choice. Preserve every provided file for explicit inspection.
    exact_mains = [file for _, file in ranked if file["reviewTier"] == "matches-project"]
    unique = len(exact_mains) == 1
    for _, file in ranked:
        if file["reviewTier"] == "matches-project":
            file["suggested"] = unique
            file["reviewHint"] = (
                "Exact replacement linked to your installed file. Still verify runtime, requirements and installer options."
                if unique else "The target file has conflicting records; recheck before selecting it."
            )
        elif file["reviewTier"] == "matching-option":
            file["reviewHint"] = "Review this file's purpose and installer options."
        # Retain original source order inside each relevance tier. The full
        # underlying file list is never truncated or silently discarded.
    priority = {"matches-project": 0, "matching-option": 1, "main-file": 2, "alternate": 3}
    ranked.sort(key=lambda pair: (priority[pair[1]["reviewTier"]], pair[0]))
    return {"files": [file for _, file in ranked],
            "reviewAdvice": {"hasUniqueMainMatch": unique and exact_mains[0].get("category") == 1,
                             "hasUniqueFileMatch": unique,
                             "matchingMainFiles": sum(file.get("category") == 1 for file in exact_mains),
                             "fileCount": len(ranked),
                             "scope": "Exact installed-file replacement only; compatibility is never inferred."}}
