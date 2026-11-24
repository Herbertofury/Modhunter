"""Read-only MCP projection of one installed row and its observed provider evidence."""
import hashlib
import json
import re
import time

from .file_advisor import nexus_file_id
from .mod_health import nexus_identity
from .source_registry import canonical_source_url


def _fields(record, keys):
    return {key: record[key] for key in keys if key in record}


def evidence_page(snapshot, revision, query):
    raw = query.get("offset", ["0"])
    if len(raw) != 1 or not re.fullmatch(r"[0-9]{1,18}", raw[0]):
        return {"ok": False, "error": "Invalid evidence offset."}
    offset = int(raw[0])
    row = snapshot["items"][0]
    update = row.get("update") or {}
    health = row.get("modHealth") or {}
    advisory = health.get("securityAdvisories") or {}
    decision = _fields(update, ("verdict", "reason", "source", "evidenceScope", "installedFileId", "targetFileId",
                                "latestVersion", "projectVersion", "checkedAt", "expiresAt", "mo2Verdict",
                                "availability", "availableVerdict", "retryAt"))
    if "verdict" not in decision:
        decision["verdict"] = "unknown"
        decision.setdefault("reason", "No update decision has been recorded for this installed row in the current live catalog.")
    expiry = decision.get("expiresAt")
    if isinstance(expiry, (int, float)):
        decision["expired"] = time.time() >= expiry
    identity = nexus_identity(row, snapshot.get("game") or "")
    target = nexus_file_id(decision.get("targetFileId"))
    if identity and target and decision.get("evidenceScope") == "exact-file":
        game, project = identity
        decision["fileUrl"] = f"https://www.nexusmods.com/{game}/mods/{project}?tab=files&file_id={target}"

    summary = {
        "installed": _fields(row, ("id", "name", "enabled", "installedVersion", "latestKnownVersion",
                                   "ignoredVersion", "nexusId", "mo2FileStatus", "installedFileEvidenceIncomplete")),
        "sourceUrl": canonical_source_url((row.get("source") or {}).get("url", "")),
        "updateDecision": decision,
        "health": {"provider": health.get("provider"), "freshness": (health.get("freshness") or {}).get("state"),
                   "page": _fields(health.get("page") or {}, ("status", "checkedAt")),
                   "bugs": _fields(health.get("bugs") or {}, ("state", "checkedAt", "displayedCount", "reasonCode")),
                   "securityAdvisories": _fields(advisory, ("state", "repository", "checkedAt", "affectedCount", "reason"))},
    }
    records = [
        {"kind": "installed-file", "projectId": pair[0], "fileId": pair[1]}
        for pair in row.get("installedFiles") or []
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(nexus_file_id(value) for value in pair)
    ]
    for notice in advisory.get("findings", []):
        records.append({"kind": "advisory", **_fields(notice, ("id", "url", "title", "severity", "publishedAt", "updatedAt",
                       "package", "ecosystem", "affectedRange", "patchedVersions", "applicability", "installedPackageVersion"))})
    for action in health.get("actions", []):
        records.append({"kind": "health-action", **_fields(action, ("id", "severity", "label", "detail", "action", "url", "evidence"))})

    digest = hashlib.sha256(json.dumps([revision, summary, records], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (offset or "evidenceRevision" in query) and query.get("evidenceRevision") != [digest]:
        return {"ok": False, "error": "Evidence changed or the page cursor is missing. Restart this row."}
    if offset > len(records):
        return {"ok": False, "error": "Offset is beyond available evidence."}
    end = min(offset + 64, len(records))
    return {"ok": True, "profileId": snapshot["profileId"], "revision": revision, "evidenceRevision": digest,
            **summary, "total": len(records), "offset": offset, "nextOffset": end if end < len(records) else None,
            "evidence": records[offset:end]}
