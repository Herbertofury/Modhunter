"""Read-only, current native updater facts for discreet desktop notifications.

This module never starts a scan, downloads a file, infers account entitlement, or
turns a project-version candidate into an installation decision.
"""
from __future__ import annotations

import hashlib
import json
import math
import time

from .scanner import nexus_project_id, version_verdict
from .source_adapters import PUBLIC_API_PROVIDERS, REVIEW_COMPATIBILITY_STATES
from .source_registry import canonical_source_url, provider_of

MAX_ITEMS = 200_000
MAX_FACTS = 10_000
CONTEXT_KEYS = ("profileId", "profileName", "game", "gameRuntime", "executionEpoch")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _time(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def collect(snapshot: dict, context: dict, *, now: float | None = None, detailed=False) -> dict:
    """Produce a complete bounded result from independently captured native state."""
    now = time.time() if now is None else now
    lease, generation = context.get("nativeLease"), context.get("nativeGeneration")
    if (not isinstance(lease, str) or len(lease) != 32
            or any(c not in "0123456789abcdef" for c in lease.lower())
            or type(generation) is not int or generation < 0
            or not context.get("profileId")
            or any(snapshot.get(key) != context.get(key) for key in CONTEXT_KEYS)):
        raise ValueError("Native updater context changed or is incomplete")
    items = snapshot.get("items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("Native updater inventory exceeds its complete bound")
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Native updater inventory contains an invalid row")
        ident = item.get("id")
        if not isinstance(ident, str) or not ident.startswith("live-"):
            continue
        if len(ident) > 256 or ident in seen or any(ord(c) < 32 for c in ident):
            raise ValueError("Native updater inventory has ambiguous item identities")
        seen.add(ident)
        update = item.get("update") or {}
        source = item.get("source") or {}
        if not isinstance(update, dict) or not isinstance(source, dict):
            continue
        checked, expires = update.get("checkedAt"), update.get("expiresAt")
        installed, latest = item.get("installedVersion"), update.get("latestVersion")
        if (update.get("verdict") != "candidate" or item.get("updateEligible") is False
                or not isinstance(installed, str) or not isinstance(latest, str)
                or len(installed) > 240 or len(latest) > 240
                or version_verdict(installed, latest) != "candidate"
                or not _time(checked) or not _time(expires) or checked > now + 300
                or expires <= now or expires <= checked
                or update.get("freshness") not in {"fresh", "cached", "revalidated"}
                or update.get("error") or update.get("errors")
                or (update.get("compatibility") or {}).get("status") in REVIEW_COMPATIBILITY_STATES):
            continue
        url = canonical_source_url(source.get("url") or "")
        if update.get("source") == "nexus-api":
            project = nexus_project_id(item, str(context.get("game") or ""))
            if project <= 0:
                continue
            provider = "nexus"
            identity = {"provider": provider, "game": context["game"], "projectId": project,
                        "sourceUrl": url or f"https://www.nexusmods.com/{context['game']}/mods/{project}"}
        else:
            provider = provider_of(url)
            if (provider not in PUBLIC_API_PROVIDERS or source.get("verified") is not True
                    or source.get("kind") == "advisory" or not url
                    or update.get("provider") != provider or update.get("sourceUrl") != url
                    or update.get("outcome") != "release" or update.get("availability") != "available"
                    or not update.get("releaseId")):
                continue
            identity = {"provider": provider, "sourceUrl": url,
                        "projectId": str(update.get("projectId") or "")}
        source_identity = _digest(identity)
        proof = {"profileId": context["profileId"], "itemId": ident,
                 "game": context["game"], "gameRuntime": context["gameRuntime"],
                 "sourceIdentity": source_identity, "installedVersion": installed,
                 "latestVersion": latest, "releaseId": str(update.get("releaseId") or "")}
        row = {**proof, "proofDigest": _digest(proof), "provider": provider,
               "checkedAt": int(checked * 1000), "expiresAt": int(expires * 1000),
               "kind": "newerVersionCandidate"}
        if detailed:
            row["itemName"] = str(item.get("name") or item.get("mo2InternalName") or ident)[:512]
            row["sourceUrl"] = identity["sourceUrl"]
            row["scope"] = "A current project-version candidate. Exact file, compatibility and access still require review; nothing was downloaded or installed."
        result.append(row)
        if len(result) > MAX_FACTS:
            raise ValueError("Verified updater facts exceed their complete result bound")
    return {"ok": True, **{key: context.get(key) for key in CONTEXT_KEYS},
            "nativeLease": lease, "nativeGeneration": generation,
            "capturedAt": int(now * 1000), "facts": result}


def facts(service, item_id: str = "") -> dict:
    """Authenticated native route; only opaque native item IDs are accepted."""
    context_id = ""
    try:
        if (not isinstance(item_id, str) or len(item_id) > 256
                or item_id and (not item_id.startswith("live-") or any(ord(c) < 32 for c in item_id))):
            raise ValueError("Choose one exact native updater item")
        captured = service.content.capture(item_id, native=False)
        if captured.get("ok") is not True:
            raise ValueError(captured.get("error") or "Native updater identity is unavailable")
        context_id = captured["contextId"]
        context = service.content._context(context_id)
        with service._guard:
            revision = service._generation
        snapshot = service.selection_snapshot([item_id]) if item_id else service.snapshot()
        result = collect(snapshot, context, detailed=bool(item_id))
        current = service.content._context(context_id)
        with service._guard:
            if (service._generation != revision or service._stopped
                    or any(current.get(key) != context.get(key) for key in (*CONTEXT_KEYS, "nativeLease", "nativeGeneration"))):
                raise ValueError("Native updater facts changed during capture; refresh before opening a notification")
        if item_id and (len(result["facts"]) != 1 or result["facts"][0]["itemId"] != item_id):
            raise ValueError("This item no longer has the verified current update fact")
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "facts": []}
    finally:
        # These short read-only captures never become installer review contexts.
        if context_id:
            with service.content._lock:
                service.content._contexts.pop(context_id, None)
