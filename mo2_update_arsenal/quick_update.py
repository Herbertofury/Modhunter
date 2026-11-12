"""Low-friction recommendations backed only by server-side release evidence.

Version matching is NOT compatibility verification. An ambiguous file set is
returned for interactive choice without preventing the user from updating.
"""
from __future__ import annotations

from typing import Any

from .file_advisor import advise_nexus_files
from .update_plan import risk_reasons


def _requires_file_choice(item: dict) -> bool:
    """A generic review note informs; DLL/hold/avoid/edition risks need a choice."""
    return any(reason != "Existing compatibility advisory: check"
               for reason in risk_reasons(item))


def choose_nexus_file(item: dict, files: list[dict]) -> tuple[dict | None, str]:
    """Pick exactly one latest-version MAIN file, or ask for an explicit choice."""
    if (item.get("update") or {}).get("source") != "nexus-api" or (item.get("update") or {}).get("verdict") != "candidate":
        return None, "Review this mod's version evidence before selecting a file"
    if _requires_file_choice(item):
        return None, "Compatibility notes need a file choice; pick an edition below"
    advice = advise_nexus_files(item, files)
    suggested = [f for f in advice["files"] if f.get("suggested") is True]
    if len(suggested) != 1:
        return None, "Several editions or no exact main-file match; choose the file you want"
    picked = suggested[0]
    try:
        identifier = int(picked.get("fileId", 0))
    except (ValueError, TypeError, OverflowError):
        identifier = 0
    if identifier <= 0 or isinstance(picked.get("fileId"), bool):
        return None, "File ID is not valid; choose a file manually"
    return picked, "Matched the published Nexus version to a unique main file"


def choose_github_asset(item: dict) -> tuple[dict | None, str]:
    """Recommend one public archive, never a README, script or ambiguous pair."""
    update = item.get("update") or {}
    if update.get("source") != "github-release-api" or update.get("verdict") != "candidate":
        return None, "Review this release before choosing an archive"
    if _requires_file_choice(item):
        return None, "Compatibility notes need a file choice; select the release asset"
    archives = [a for a in update.get("releaseAssets", []) if isinstance(a, dict)
                and str(a.get("name") or "").lower().endswith((".zip", ".7z", ".rar"))]
    if len(archives) != 1:
        return None, "Multiple release archives or no installable archive; choose the correct one"
    try:
        identifier = int(archives[0].get("assetId", 0))
    except (ValueError, TypeError, OverflowError):
        identifier = 0
    if identifier <= 0 or isinstance(archives[0].get("assetId"), bool):
        return None, "No verified release asset ID"
    return archives[0], "Only installable archive in this verified public release"
