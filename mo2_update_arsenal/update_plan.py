"""Profile-scoped Modhunter update planning and explicit batch-download validation.

No network, credential handling, game process, or MO2 mutation occurs here.
The interactive MO2 install workflow remains a separate user-confirmed operation.
"""
from __future__ import annotations

from typing import Any
import time

from .source_adapters import PUBLIC_API_PROVIDERS, REVIEW_COMPATIBILITY_STATES, asset_matches_source, asset_proof

RISK_FLAGS = frozenset({"DLL", "HARD_ESP", "HARD_ESM", "NEMESIS", "SKSE", "ROOT_BUILDER", "BEHAVIOR"})
RISK_VERDICTS = frozenset({"hold", "check", "avoid", "ae-only"})


class PlanError(ValueError):
    """Update selection is missing explicit authorization or is no longer current."""


def _positive_int(value: Any) -> int:
    # bool is a subclass of int, but is never a valid Nexus identifier.
    if isinstance(value, bool):
        return 0
    # int(11.9) is 11, but that is a different file than the reviewed ID.
    if isinstance(value, float) and not value.is_integer():
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
    plugins = (item.get("localAnalysis") or {}).get("plugins") or []
    for plugin in plugins:
        name = str(plugin.get("name") or "Plugin")
        provenance = (plugin.get("provenance") or {}).get("state")
        if provenance in {"source-changed", "output-changed", "source-and-output-changed", "winner-changed"}:
            risks.append("ESLifier conversion needs review: " + name)
        if plugin.get("winner") and plugin.get("active") is True and plugin.get("masterIssues"):
            risks.append("Active plugin has missing, inactive, or late-loading masters: " + name)
        if (plugin.get("records") or {}).get("scanComplete") is False or plugin.get("recordScanComplete") is False:
            risks.append("Plugin record scan is incomplete: " + name)
        elif (plugin.get("eligibility") or {}).get("status") == "blocked":
            risks.append("Plugin format, runtime, or dependency check needs review: " + name)
    compatibility = (item.get("update") or {}).get("compatibility") or {}
    if compatibility.get("status") in REVIEW_COMPATIBILITY_STATES:
        risks.append("Source release does not match this game or loader")
    risks = list(dict.fromkeys(risks))
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
            "checkedAt": update.get("checkedAt"), "expiresAt": update.get("expiresAt"),
            "releaseAssetCount": update.get("releaseAssetCount", len(update.get("releaseAssets") or [])),
            "dependencyCount": update.get("dependencyCount", len(update.get("dependencies") or [])),
            "capabilities": update.get("capabilities"), "compatibility": update.get("compatibility"),
            "freshness": update.get("freshness"), "availability": update.get("availability"),
            "sourceEvidence": update if not update.get("detailsAvailable") else None,
            "localAnalysis": item.get("localAnalysis"), "modHealth": item.get("modHealth"),
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
        if raw.get("targetVersion") is not None and str(raw["targetVersion"]) != str((item.get("update") or {}).get("latestVersion") or ""):
            raise PlanError(f"Published target version changed for {item.get('name', item_id)}; review its files again")
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


def validate_release_asset(snapshot: dict, raw: dict, *, require_fresh=True) -> dict:
    """Resolve only exact archives from owned provider/project/release evidence."""
    if not isinstance(raw, dict) or raw.get("kind") != "source":
        raise PlanError("Invalid reviewed source selection")
    item_id = str(raw.get("itemId") or "")
    item = next((x for x in snapshot.get("items", []) if str(x.get("id")) == item_id), None)
    if item is None or raw.get("reviewed") is not True:
        raise PlanError("Source mod must exist and have a reviewed file selection")
    if str(item.get("installedVersion") or "") != str(raw.get("installedVersion") or ""):
        raise PlanError("Installed mod version changed; refresh source selections")
    update = item.get("update") or {}
    provider = str(update.get("provider") or (item.get("source") or {}).get("type") or "")
    if not provider and update.get("source") == "github-release-api":
        provider = "github"
    if update.get("verdict") != "candidate" or provider not in PUBLIC_API_PROVIDERS or update.get("source") != f"{provider}-release-api":
        raise PlanError("Only verified public release candidates support direct source downloads")
    if require_fresh and (update.get("freshness") in {"stale", "unverified"}
            or isinstance(update.get("expiresAt"), (int, float)) and update["expiresAt"] <= time.time()):
        raise PlanError("Source evidence expired; check this project again before downloading")
    if raw.get("targetVersion") is not None and str(raw["targetVersion"]) != str(update.get("latestVersion") or ""):
        raise PlanError("Published source release changed; review its archives again")
    if risk_reasons(item) and raw.get("riskAcknowledged") is not True:
        raise PlanError("Acknowledge source compatibility risks before download")
    if (update.get("compatibility") or {}).get("status") in REVIEW_COMPATIBILITY_STATES:
        raise PlanError("This release does not match the profile's game or loader")
    source = item.get("source") or {}
    asset_key = raw.get("assetKey")
    asset_id = _positive_int(raw.get("assetId"))
    if asset_key is not None and (not isinstance(asset_key, str) or not asset_key or len(asset_key) > 400):
        raise PlanError("Invalid release asset identity")
    matches = [asset for asset in update.get("releaseAssets", []) if isinstance(asset, dict)
               and (asset.get("assetKey") == asset_key if asset_key else asset_id > 0 and _positive_int(asset.get("assetId")) == asset_id)]
    if len(matches) != 1:
        raise PlanError("The selected file is not uniquely present in this verified release")
    candidate = matches[0]
    if asset_key and raw.get("assetId") is not None and asset_id != _positive_int(candidate.get("assetId")):
        raise PlanError("Conflicting release asset identities")
    if candidate.get("installable") is False or not asset_matches_source(source, update, candidate):
        raise PlanError("Release asset URL failed provider/project/release/format validation")
    proof = asset_proof(source, update, candidate)
    normalized = bool(update.get("outcome") or candidate.get("assetKey") or update.get("projectId") or update.get("releaseId"))
    if (normalized and raw.get("assetProof") != proof) or (raw.get("assetProof") is not None and raw["assetProof"] != proof):
        raise PlanError("The reviewed release archive changed; reopen its file details")
    return {"kind": "source", "itemId": item_id, "assetId": _positive_int(candidate.get("assetId")),
            "assetKey": str(candidate.get("assetKey") or ""), "assetProof": proof,
            "projectId": str(update.get("projectId") or ""), "releaseId": str(update.get("releaseId") or ""),
            "sourceUrl": str(source.get("url") or ""), "targetVersion": str(update.get("latestVersion") or ""),
            "url": str(candidate.get("url") or ""), "provider": provider, "name": str(candidate.get("name") or ""),
            "installedVersion": str(item.get("installedVersion") or "")}
