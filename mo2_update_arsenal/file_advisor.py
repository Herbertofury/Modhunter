"""Conservative, per-MO2-row guidance for choosing exact Nexus file archives.

The advisor annotates file lists without removing entries or claiming game/runtime
compatibility. Recommendations require published Nexus metadata and an unambiguous
single main file with the same normalized numeric version as that project.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .scanner import normalized_version


def _same_numeric_version(a: Any, b: Any) -> bool:
    x, y = normalized_version(str(a or "")), normalized_version(str(b or ""))
    if x is None or y is None:
        return False
    n = max(len(x), len(y))
    return x + (0,) * (n - len(x)) == y + (0,) * (n - len(y))


def advise_nexus_files(item: dict, files: list[dict]) -> dict:
    """Return every file with a plain-language, non-authorizing review signal.

    Numeric version *equality* is the only strong evidence. File names such as
    "AE", "SE", "NG" or "main" are not treated as compatibility evidence.
    """
    update = item.get("update") or {}
    latest = str(update.get("latestVersion") or "")
    source = str(update.get("source") or "")
    trusted_version = source == "nexus-api" and str(update.get("verdict")) == "candidate"
    ranked: list[dict] = []
    for order, raw in enumerate(files):
        if not isinstance(raw, dict):
            continue
        file = deepcopy(raw)
        try:
            category = int(file.get("category") or 0)
        except (TypeError, ValueError, OverflowError):
            category = 0
        exact = bool(trusted_version and _same_numeric_version(file.get("version"), latest))
        file["releaseVersionMatch"] = exact
        file["reviewTier"] = "matches-project" if exact and category == 1 else (
            "matching-option" if exact else "main-file" if category == 1 else "alternate")
        file["suggested"] = False
        file["reviewHint"] = ("Main file; review variants, prerequisites and your game runtime" if category == 1
                              else "Optional or alternate file; verify which installer option you need")
        ranked.append((order, file))

    # Multiple main files matching a published version may target different
    # game runtimes, mod editions or installation roles. Never pick one blindly.
    exact_mains = [file for _, file in ranked if file["reviewTier"] == "matches-project"]
    unique = len(exact_mains) == 1
    for _, file in ranked:
        if file["reviewTier"] == "matches-project":
            file["suggested"] = unique
            file["reviewHint"] = (
                "Only main file with the published project version. Still verify runtime, requirements and installer options."
                if unique else "Multiple main files match this release; review the edition and runtime of each."
            )
        elif file["reviewTier"] == "matching-option":
            file["reviewHint"] = "Version matches the published project, but this is not a main file. Review its purpose."
        # Retain original source order inside each relevance tier. The full
        # underlying file list is never truncated or silently discarded.
    priority = {"matches-project": 0, "matching-option": 1, "main-file": 2, "alternate": 3}
    ranked.sort(key=lambda pair: (priority[pair[1]["reviewTier"]], pair[0]))
    return {"files": [file for _, file in ranked],
            "reviewAdvice": {"hasUniqueMainMatch": unique,
                             "matchingMainFiles": len(exact_mains),
                             "fileCount": len(ranked),
                             "scope": "Published Nexus version match only; compatibility is never inferred."}}
