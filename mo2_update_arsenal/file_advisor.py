"""Conservative, per-MO2-row guidance for choosing exact Nexus file archives.

The advisor annotates file lists without removing entries or claiming game/runtime
compatibility. Recommendations require published Nexus metadata and an unambiguous
single main file with the same normalized numeric version as that project.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .scanner import normalized_version


def _comparison_version(value: Any) -> tuple[int, ...] | None:
    """Canonicalize the existing equality rule: trailing zero components agree."""
    version = normalized_version(str(value or ""))
    if version is None:
        return None
    length = len(version)
    while length > 1 and version[length - 1] == 0:
        length -= 1
    return version[:length]


def advise_nexus_files(item: dict, files: list[dict]) -> dict:
    """Return every file with a plain-language, non-authorizing review signal.

    Numeric version *equality* is the only strong evidence. File names such as
    "AE", "SE", "NG" or "main" are not treated as compatibility evidence.
    """
    update = item.get("update") or {}
    latest = str(update.get("latestVersion") or "")
    source = str(update.get("source") or "")
    trusted_version = source == "nexus-api" and str(update.get("verdict")) == "candidate"
    published = _comparison_version(latest) if trusted_version else None
    # Fixed relevance tiers; appending keeps the provider's exact order in
    # each tier without making per-file sort keys or rearranging the files.
    exact_mains, matching_options, other_mains, alternates = [], [], [], []
    for raw in files:
        if not isinstance(raw, dict):
            continue
        file = deepcopy(raw)
        try:
            category = int(file.get("category") or 0)
        except (TypeError, ValueError, OverflowError):
            category = 0
        exact = published is not None and _comparison_version(file.get("version")) == published
        if exact and category == 1:
            bucket, tier = exact_mains, "matches-project"
            hint = ""  # Set after we know whether another main file matches.
        elif exact:
            bucket, tier = matching_options, "matching-option"
            hint = "Version matches the published project, but this is not a main file. Review its purpose."
        elif category == 1:
            bucket, tier = other_mains, "main-file"
            hint = "Main file; review variants, prerequisites and your game runtime"
        else:
            bucket, tier = alternates, "alternate"
            hint = "Optional or alternate file; verify which installer option you need"
        file["releaseVersionMatch"] = exact
        file["reviewTier"] = tier
        file["suggested"] = False
        file["reviewHint"] = hint
        bucket.append(file)

    # Multiple main files matching a published version may target different
    # game runtimes, mod editions or installation roles. Never pick one blindly.
    unique = len(exact_mains) == 1
    main_hint = (
        "Only main file with the published project version. Still verify runtime, requirements and installer options."
        if unique else "Multiple main files match this release; review the edition and runtime of each."
    )
    for file in exact_mains:
        file["suggested"] = unique
        file["reviewHint"] = main_hint
    ranked = [file for bucket in (exact_mains, matching_options, other_mains, alternates) for file in bucket]
    return {"files": ranked,
            "reviewAdvice": {"hasUniqueMainMatch": unique,
                             "matchingMainFiles": len(exact_mains),
                             "fileCount": len(ranked),
                             "scope": "Published Nexus version match only; compatibility is never inferred."}}
