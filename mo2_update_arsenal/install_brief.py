"""Exact, non-mutating MO2 install briefing shared by the UI and installer gate.

A complete download is not a guarantee of compatibility. This module never
claims that an archive is safe, never reads local files and never installs.
"""
from __future__ import annotations

from typing import Any

from .download_ids import download_id as parse_download_id
from .update_plan import risk_reasons, validate_release_asset, PlanError


PROFILE_BACKUP_FILES = ["modlist.txt", "plugins.txt", "loadorder.txt", "archives.txt", "lockedorder.txt"]


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        n = int(value)
    except (ValueError, TypeError, OverflowError):
        return 0
    return n if n > 0 else 0


def build_install_brief(snapshot: dict, ticket: dict | None, download_id: Any) -> dict:
    """Return an evidence-based briefing and precise reasons to block an install.

    Scope this to an active profile, its game/runtime, a specific MO2 mod row,
    the originally selected archive and the version observed when queued.
    A changed profile/runtime/version cannot be approved by a browser prompt.
    """
    did = parse_download_id(download_id)
    if did is None or not isinstance(ticket, dict):
        return {"ok": False, "canInstall": False, "errors": ["Unknown MO2 download ticket"]}
    failures: list[str] = []
    notices: list[str] = []
    profile = str(snapshot.get("profileId") or "")
    if not profile or str(ticket.get("profileId") or "") != profile:
        failures.append("The active MO2 profile no longer matches this download")
    if ticket.get("status") != "complete":
        failures.append("MO2 has not marked this archive ready to install")
    if ticket.get("game") and str(ticket["game"]) != str(snapshot.get("game") or ""):
        failures.append("The active game changed since the archive was queued")
    if ticket.get("gameRuntime") and str(ticket["gameRuntime"]) != str(snapshot.get("gameRuntime") or "unknown"):
        failures.append("Your target game runtime changed: review this file again")
    if ticket.get("profileName") and str(ticket["profileName"]) != str(snapshot.get("profileName") or ""):
        failures.append("The MO2 profile name changed since this archive was queued")

    rows = {str(x.get("id")): x for x in snapshot.get("items", [])}
    item_id = str(ticket.get("itemId") or "")
    item = rows.get(item_id) if item_id else None
    if item_id and not item:
        failures.append("The originally selected mod is no longer installed in this MO2 profile")
    if item:
        queued_project = _positive_int(ticket.get("modId"))
        if (ticket.get("provider") == "nexus" and queued_project
                and _positive_int(item.get("nexusId")) != queued_project):
            failures.append("The Nexus project linked to this mod changed; review the exact archive again")
        if str(item.get("installedVersion") or "") != str(ticket.get("installedVersion") or ""):
            failures.append("The installed version changed after you queued this download")
        latest = str((item.get("update") or {}).get("latestVersion") or "")
        if ticket.get("targetVersion") and str(ticket["targetVersion"]) != latest:
            failures.append("The published update target changed; inspect the exact file again")
        current_risks = risk_reasons(item)
        previous_risks = set(ticket.get("riskReasons") or [])
        if set(current_risks) - previous_risks:
            failures.append("New compatibility warnings were discovered after file review")
        notices.extend(current_risks)
        if ticket.get("assetProof"):
            selection = {"kind": "source", "itemId": item_id, "installedVersion": ticket.get("installedVersion"),
                         "targetVersion": ticket.get("targetVersion"), "assetProof": ticket["assetProof"],
                         "reviewed": True, "riskAcknowledged": True,
                         **({"assetKey": ticket["assetKey"]} if ticket.get("assetKey") else {"assetId": ticket.get("assetId")})}
            try:
                validate_release_asset(snapshot, selection, require_fresh=False)
            except PlanError as exc:
                failures.append("The downloaded source archive no longer matches its reviewed evidence: " + str(exc))
    else:
        notices.append("Archive is not tied to an exact reviewed MO2 row. Check the mod and file identity in MO2.")
    if not ticket.get("reviewed"):
        notices.append("This archive was not added through the reviewed Update Workshop queue.")
    if snapshot.get("gameRuntime") in ("", None, "unknown"):
        notices.append("Game runtime is unknown. Verify the correct executable and SKSE/Address Library build.")
    if ticket.get("fileVersion") and ticket.get("targetVersion") and str(ticket["fileVersion"]) != str(ticket["targetVersion"]):
        notices.append("The exact archive version differs from the latest published project version. Confirm this is intentional.")

    return {
        "ok": True, "canInstall": not failures, "downloadId": did,
        "errors": failures, "warnings": list(dict.fromkeys(notices)),
        "modName": str(item.get("name") or item_id) if item else str(ticket.get("name") or "Unlinked download"),
        "itemId": item_id, "provider": str(ticket.get("provider") or "unknown"),
        "fileId": _positive_int(ticket.get("fileId") or ticket.get("assetId")),
        "assetKey": str(ticket.get("assetKey") or ""), "assetProof": str(ticket.get("assetProof") or ""),
        "archiveName": str(ticket.get("fileName") or ticket.get("name") or "Exact archive in MO2 Downloads"),
        "fileVersion": str(ticket.get("fileVersion") or ""),
        "installedVersion": str(ticket.get("installedVersion") or "?"),
        "targetVersion": str(ticket.get("targetVersion") or "?"),
        "profileName": str(snapshot.get("profileName") or ""),
        "game": str(snapshot.get("game") or ""),
        "gameRuntime": str(snapshot.get("gameRuntime") or "unknown"),
        "backup": {"kind": "Profile load-order files only", "files": PROFILE_BACKUP_FILES,
                   "includesModFiles": False},
        "steps": ["Revalidate active MO2 profile and exact archive", "Back up MO2 profile text files",
                  "Open MO2 interactive installer", "Inspect FOMOD, dependencies and overwrites",
                  "Refresh the profile and verify the installed version"],
    }
