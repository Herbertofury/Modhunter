"""Profile-scoped Modhunter update planning and explicit batch-download validation.

No network, credential handling, game process, or MO2 mutation occurs here.
The interactive MO2 install workflow remains a separate user-confirmed operation.
"""
from __future__ import annotations

from typing import Any

RISK_FLAGS = frozenset({"DLL", "HARD_ESP", "HARD_ESM", "NEMESIS", "SKSE", "ROOT_BUILDER", "BEHAVIOR"})
RISK_VERDICTS = frozenset({"hold", "check", "avoid", "ae-only"})


class PlanError(ValueError):
    """Update selection is missing explicit authorization or is no longer current."""


def _positive_int(value: Any) -> int:
    # bool is a subclass of int, but is never a valid Nexus identifier.
    if isinstance(value, bool):
        return 0
    try:
        candidate = int(value)
    except (ValueError, TypeError, OverflowError):
        return 0
    return candidate if candidate > 0 else 0


def risk_reasons(item: dict) -> list[str]:
    flags = {str(f).upper() for f in item.get("flags", []) if isinstance(f, (str, int))}
    risks = [f"Native/configuration flag: {flag}" for flag in sorted(flags & RISK_FLAGS)]
    verdict = str((item.get("audit") or {}).get("verdict") or "").lower()
    if verdict in RISK_VERDICTS:
        risks.append("Existing compatibility advisory: " + verdict)
    return risks


def plan_updates(snapshot: dict) -> dict:
    """Keep every installed record, including duplicate Nexus projects and manual mods.

    All results are advisory. No file is automatically approved from a project
    version: the user must inspect an exact Nexus file and its requirements.
    """
    records: list[dict] = []
    summary = {"candidates": 0, "review": 0, "manual": 0, "current": 0, "risky": 0, "total": 0}
    for item in snapshot.get("items", []):
        nid = _positive_int(item.get("nexusId"))
        update = item.get("update") or {}
        verdict = str(update.get("verdict") or "unverified")
        risks = risk_reasons(item)
        if verdict == "candidate":
            lane = "candidate"
        elif verdict in ("current", "newer-installed"):
            lane = "current"
        elif nid == 0 and verdict in ("unverified", "unknown"):
            lane = "manual"
        else:
            lane = "review"
        summary[{"candidate": "candidates", "review": "review", "manual": "manual", "current": "current"}[lane]] += 1
        summary["risky"] += bool(risks)
        summary["total"] += 1
        src = item.get("source") or {}
        source_url = str(src.get("url") or "") if src.get("verified") else ""
        records.append({
            "id": str(item.get("id") or ""), "name": str(item.get("name") or "Unknown mod"),
            "nexusId": nid, "installedVersion": str(item.get("installedVersion") or ""),
            "latestVersion": str(update.get("latestVersion") or ""),
            "verdict": verdict, "lane": lane, "priority": item.get("priority"),
            "group": str(item.get("group") or ""), "risks": risks,
            "sourceUrl": source_url, "sourceVerified": bool(source_url),
            "provider": str(update.get("provider") or src.get("type") or "unknown"),
            "releaseAssets": update.get("releaseAssets") or [],
            "releaseUrl": str(update.get("releaseUrl") or source_url),
            "checkedAt": update.get("checkedAt"),
        })
    return {"ok": True, "profileId": str(snapshot.get("profileId") or ""),
            "profileName": str(snapshot.get("profileName") or ""),
            "game": str(snapshot.get("game") or ""), "gameRuntime": str(snapshot.get("gameRuntime") or "unknown"),
            "summary": summary, "items": records,
            "note": "Project version is not file compatibility. All queued downloads require explicit per-file review; installs remain interactive in MO2."}


def validate_batch_selection(snapshot: dict, payload: dict) -> list[dict]:
    """Validate the *entire* selection before any native download or backup.

    Reject stale profile/version, fabricated Nexus associations, unknown/current
    rows, duplicate rows, omitted file review, or unacknowledged compatibility risks.
    """
    profile = str(snapshot.get("profileId") or "")
    if not profile or payload.get("profileId") != profile:
        raise PlanError("MO2 profile changed; reopen the Workshop and review the new profile")
    if payload.get("confirm") != "QUEUE_REVIEWED_DOWNLOADS":
        raise PlanError("Explicit reviewed-download confirmation required")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise PlanError("Choose at least one exact Nexus file")
    indexed = {str(i.get("id")): i for i in snapshot.get("items", [])}
    result: list[dict] = []
    seen_items: set[str] = set()
    seen_files: set[tuple[int, int]] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise PlanError("Every selection must be an object")
        item_id = raw.get("itemId")
        if not isinstance(item_id, str) or item_id not in indexed:
            raise PlanError("Selected mod is no longer in the active MO2 profile")
        if item_id in seen_items:
            raise PlanError("A mod was selected twice; remove the duplicate selection")
        seen_items.add(item_id)
        item = indexed[item_id]
        nid, fid = _positive_int(raw.get("modId")), _positive_int(raw.get("fileId"))
        if nid == 0 or fid == 0 or nid != _positive_int(item.get("nexusId")):
            raise PlanError(f"Nexus file association no longer matches {item.get('name', item_id)}")
        if str(item.get("installedVersion") or "") != str(raw.get("installedVersion") or ""):
            raise PlanError(f"Installed version changed for {item.get('name', item_id)}; rescan")
        if (item.get("update") or {}).get("verdict") != "candidate":
            raise PlanError(f"{item.get('name', item_id)} is not a verified update candidate")
        if raw.get("reviewed") is not True:
            raise PlanError(f"Review the exact Nexus file before queueing {item.get('name', item_id)}")
        if risk_reasons(item) and raw.get("riskAcknowledged") is not True:
            raise PlanError(f"Acknowledge the compatibility warning for {item.get('name', item_id)}")
        unique_file = (nid, fid)
        if unique_file in seen_files:
            # Multiple MO2 rows can represent one project. Never download its
            # identical archive twice; keep the first row's provenance.
            continue
        seen_files.add(unique_file)
        result.append({"itemId": item_id, "modId": nid, "fileId": fid,
                       "installedVersion": str(item.get("installedVersion") or "")})
    return result


def validate_release_asset(snapshot: dict, raw: dict) -> dict:
    """Resolve GitHub release assets only from MO2-owned server scan evidence."""
    from urllib.parse import urlsplit
    if not isinstance(raw, dict) or raw.get("kind") != "source":
        raise PlanError("Invalid reviewed source selection")
    item_id = str(raw.get("itemId") or "")
    item = next((x for x in snapshot.get("items", []) if str(x.get("id")) == item_id), None)
    if item is None or raw.get("reviewed") is not True:
        raise PlanError("Source mod must exist and have a reviewed file selection")
    if str(item.get("installedVersion") or "") != str(raw.get("installedVersion") or ""):
        raise PlanError("Installed mod version changed; refresh source selections")
    update = item.get("update") or {}
    if update.get("verdict") != "candidate" or update.get("source") != "github-release-api":
        raise PlanError("Only fresh GitHub release candidates support direct source downloads")
    if risk_reasons(item) and raw.get("riskAcknowledged") is not True:
        raise PlanError("Acknowledge source compatibility risks before download")
    source = item.get("source") or {}
    url = str(source.get("url") or "")
    u = urlsplit(url)
    parts = [p for p in u.path.strip("/").split("/") if p]
    if u.scheme != "https" or u.hostname != "github.com" or len(parts) < 2:
        raise PlanError("Source project is not a verified GitHub repository")
    prefix = f"https://github.com/{parts[0]}/{parts[1]}/releases/download/"
    asset_id = _positive_int(raw.get("assetId"))
    candidate = next((x for x in update.get("releaseAssets", [])
                      if _positive_int(x.get("assetId")) == asset_id), None)
    if candidate is None or asset_id <= 0:
        raise PlanError("The selected file is not in this verified release")
    download_url = str(candidate.get("url") or "")
    name = str(candidate.get("name") or "")
    if (not download_url.startswith(prefix) or "?" in download_url or
            not name.lower().endswith((".zip", ".7z", ".rar"))):
        raise PlanError("Release asset URL failed provider/format validation")
    return {"kind": "source", "itemId": item_id, "assetId": asset_id,
            "url": download_url, "provider": "github", "name": name,
            "installedVersion": str(item.get("installedVersion") or "")}
