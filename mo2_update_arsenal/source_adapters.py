"""Public source capabilities and the bounded Rust adapter protocol.

Provider facts are observations. The browser cannot choose a helper, API URL,
project identity or download URL; archive choices resolve from owned records.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit

from .plugin_intelligence import RustAuditRunner

PUBLIC_API_PROVIDERS = frozenset({"github", "gitlab", "modrinth", "thunderstore"})
REVIEW_COMPATIBILITY_STATES = frozenset({"mismatch", "incompatible", "unsupported", "game-mismatch", "context-required", "community-required", "deprecated"})
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
CACHE_LIMIT = 4_194_304

# Capabilities describe this integration, never a claim about an account session.
PROVIDER_LABELS = {
    "nexus": "Nexus Mods", "github": "GitHub", "gitlab": "GitLab",
    "modrinth": "Modrinth", "thunderstore": "Thunderstore", "curseforge": "CurseForge",
    "patreon": "Patreon", "loverslab": "LoversLab", "moddb": "Mod DB",
    "bethesda": "Bethesda Creations", "dwemer": "Dwemer Mods", "modding-guild": "Modding Guild",
    "ko-fi": "Ko-fi", "boosty": "Boosty", "afdian": "Afdian", "tumblr": "Tumblr",
    "subscribestar": "SubscribeStar", "modcollective": "ModCollective", "simsfinds": "SimsFinds",
    "itch": "itch.io", "steam-workshop": "Steam Workshop", "steam": "Steam",
    "discord": "Discord", "youtube": "YouTube", "bilibili": "Bilibili", "x": "X",
    "facebook": "Facebook", "aml": "AML", "f95": "F95", "sims-resource": "The Sims Resource",
    "modthesims": "Mod The Sims", "website": "Original website", "unknown": "Unlinked source",
}


def adapter_capabilities(provider: str, *, helper_available=True) -> dict:
    label = PROVIDER_LABELS.get(provider, provider.replace("-", " ").title())
    mode, auth, check, download = "manual", "browser", False, False
    reason = "Open the exact original project with your own browser session; this adapter does not establish an automatic release version."
    if provider == "nexus":
        mode, auth, check, download = "native-api", "mo2", True, True
        reason = "Uses MO2's native Nexus connection for project metadata, file selection and downloads."
    elif provider in PUBLIC_API_PROVIDERS:
        mode, auth, check, download = "public-api", "none", True, True
        reason = "Checks the provider's public release API; exact identity, compatible context and an installable archive remain required."
        if not helper_available and provider in {"modrinth", "thunderstore"}:
            check = download = False
            reason = "Install the matching Modhunter Rust helper to enable this provider's public API checks."
    elif provider == "curseforge":
        mode, auth = "api-key", "api-key"
        reason = "CurseForge API access requires a provider-issued key; this build retains the original page and does not claim a configured API session."
    elif provider == "bethesda":
        auth = "provider"
        reason = "Creations use Bethesda's own entitlement and installation workflow; review updates in the official client."
    elif provider in {"steam-workshop", "steam"}:
        auth = "provider"
        reason = "Steam owns subscription and Workshop delivery; use the original item and Steam's update workflow."
    elif provider == "unknown":
        auth = "none"
        reason = "Link the exact original project; a name match or a dependency URL cannot establish this mod's identity."
    elif provider in {"discord", "youtube", "bilibili", "x", "facebook"}:
        reason = "This is a creator/community link. Follow its original release link; a post date is not an installed-mod version."
    return {"provider": provider, "label": label, "checkMode": mode, "versionCheck": check,
            "reviewedDownload": download, "authentication": auth, "reason": reason}


class RustSourceRunner(RustAuditRunner):
    """Same process bounds and cancellation as plugin audits, a fixed other command."""
    command = "scan-sources"

    def _validate_result(self, result: dict, request: dict) -> dict:
        if (not isinstance(result, dict) or result.get("schemaVersion") != 1
                or not isinstance(result.get("itemRecords"), dict)):
            raise RuntimeError("Rust source adapters returned an unsupported schema")
        if result.get("profileId") != request.get("profileId"):
            raise RuntimeError("Rust source adapters returned a different profile")
        allowed = {str(row.get("id")) for row in request.get("items", [])}
        if any(not isinstance(k, str) or k not in allowed or not isinstance(v, dict)
               for k, v in result["itemRecords"].items()):
            raise RuntimeError("Rust source adapters returned an unrequested mod identity")
        return result

    def run(self, request: dict, *, cancel_event=None) -> dict:
        try:
            return super().run(request, cancel_event=cancel_event)
        except RuntimeError as exc:
            message = str(exc).replace("Plugin analysis", "Source check").replace("plugin analysis", "source check").replace("plugin analyzer", "source adapter helper")
            raise RuntimeError(message) from exc


def _safe_url(raw: str) -> str:
    from .source_registry import canonical_source_url
    return canonical_source_url(raw)


def public_hashes(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {name: str(raw[name]).lower() for name, length in (("sha256", 64), ("sha512", 128), ("sha1", 40))
            if name in raw and re.fullmatch(r"[0-9a-fA-F]{" + str(length) + "}", str(raw[name]))}


def _person(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    result = {"name": str(raw.get("name") or raw.get("login") or "")[:180]}
    for field in ("url", "avatarUrl"):
        result[field] = _safe_url(raw.get(field) or "")
    if raw.get("role"):
        result["role"] = str(raw["role"])[:80]
    return result if result["name"] or result["url"] else None


def normalize_adapter_record(item: dict, raw: dict, *, now=None) -> dict:
    """Whitelist the Rust observation; compare only fresh actual release evidence."""
    from .version_evidence import compare_versions
    from .source_registry import provider_of
    now = time.time() if now is None else now
    source = item.get("source") or {}
    source_url = _safe_url(source.get("url") or "")
    provider = provider_of(source_url)
    outcome = str(raw.get("outcome") or "unavailable")
    freshness = str(raw.get("freshness") or "unverified")
    checked = raw.get("checkedAt")
    expires = raw.get("expiresAt")
    valid_time = (isinstance(checked, (int, float)) and not isinstance(checked, bool)
                  and math.isfinite(checked) and 0 < checked <= now + 300)
    expired = (isinstance(expires, (int, float)) and not isinstance(expires, bool)
               and math.isfinite(expires) and expires <= now)
    same_source = (raw.get("provider") == provider and raw.get("sourceUrl") == source_url
                   and source.get("verified") is True and source.get("kind") != "advisory")
    result = {"provider": provider, "source": f"{provider}-release-api" if provider in PUBLIC_API_PROVIDERS else provider,
              "sourceUrl": source_url, "outcome": outcome, "availability": str(raw.get("availability") or "unknown")[:80],
              "freshness": freshness, "checkedAt": checked if valid_time else None,
              "expiresAt": expires if isinstance(expires, (int, float)) and not isinstance(expires, bool) and math.isfinite(expires) else None,
              "capabilities": adapter_capabilities(provider), "reason": str(raw.get("reason") or "Source evidence needs review.")[:1200],
              "verdict": "unverified" if outcome in {"manual", "unsupported"} else "review" if outcome == "review" else "unknown"}
    if outcome in {"manual", "unsupported"} and (raw.get("sourceUrl") or "") == source_url:
        result["reason"] = (str(raw.get("reason") or "")[:1200] if raw.get("provider") == provider else result["capabilities"]["reason"])
        if not source.get("verified") or source.get("kind") == "advisory":
            result["capabilities"]["versionCheck"] = False
            result["capabilities"]["reviewedDownload"] = False
            result["reason"] = "Link the exact original project before checking releases; this source identity is unverified."
        return result
    if not same_source:
        result.update(outcome="unavailable", verdict="unknown", reason="Source identity changed or the adapter result does not match the selected project.")
        return result
    for field in ("projectId", "releaseId", "latestVersion", "publishedAt", "releaseName", "channel", "publisherId", "projectTeamId", "teamId", "title", "projectStatus", "moderationStatus"):
        if isinstance(raw.get(field), (str, int, float)) and not isinstance(raw.get(field), bool):
            result[field] = str(raw[field])[:240]
    if type(raw.get("manualAssetCount")) is int and raw["manualAssetCount"] >= 0:
        result["manualAssetCount"] = raw["manualAssetCount"]
    result["releaseUrl"] = _safe_url(raw.get("releaseUrl") or source_url)
    for field, limit in (("summary", 4000), ("changelog", 24000)):
        if isinstance(raw.get(field), str):
            result[field] = raw[field][:limit]
    result["iconUrl"] = _safe_url(raw.get("iconUrl") or "")
    if isinstance(raw.get("media"), list):
        result["media"] = [{"type": "image", "url": _safe_url(media.get("url") or ""), "caption": str(media.get("caption") or "")[:240]}
                           for media in raw["media"][:6] if isinstance(media, dict) and media.get("type") == "image" and _safe_url(media.get("url") or "")]
    for field in ("author", "publisher"):
        if person := _person(raw.get(field)):
            result[field] = person
    compatibility = raw.get("compatibility")
    if isinstance(compatibility, dict):
        result["compatibility"] = {key: deepcopy(value) for key, value in compatibility.items()
                                   if key in {"status", "reason", "game", "games", "loader", "loaders", "gameVersions", "runtime", "requiredContext", "community"}
                                   and isinstance(value, (str, list, bool))}
    if isinstance(raw.get("dependencies"), list):
        result["dependencies"] = [{key: str(value)[:300] for key, value in dep.items()
                                   if key in {"id", "projectId", "versionId", "name", "kind", "type", "version", "url"}
                                   and isinstance(value, (str, int)) and not isinstance(value, bool)}
                                  for dep in raw["dependencies"][:150] if isinstance(dep, dict)]
    for field in ("retryAt", "rateLimitRemaining", "rateLimitReset"):
        value = raw.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[field] = value
    result["releaseAssets"] = []
    for asset in raw.get("releaseAssets", [])[:200] if isinstance(raw.get("releaseAssets"), list) else []:
        if not isinstance(asset, dict):
            continue
        safe = {k: str(asset[k])[:1800 if k == "url" else 300] for k in
                ("assetKey", "provider", "projectId", "releaseId", "releaseVersion", "name", "url", "originUrl")
                if isinstance(asset.get(k), (str, int)) and not isinstance(asset.get(k), bool)}
        if type(asset.get("assetId")) is int and asset["assetId"] > 0:
            safe["assetId"] = asset["assetId"]
        if type(asset.get("size")) is int and asset["size"] >= 0:
            safe["size"] = asset["size"]
        safe["hashes"] = public_hashes(asset.get("hashes"))
        safe["installable"] = asset.get("installable") is not False and asset_matches_source(source, result, safe)
        result["releaseAssets"].append(safe)
    incompatible = (result.get("compatibility") or {}).get("status") in REVIEW_COMPATIBILITY_STATES
    if outcome == "release" and same_source and valid_time and not expired and freshness in {"fresh", "cached", "revalidated"} and not incompatible:
        comparison = compare_versions(str(item.get("installedVersion") or ""), str(result.get("latestVersion") or ""))
        verdict = comparison["verdict"]
        result["versionComparison"] = comparison
        result["verdict"] = "review" if verdict == "unknown" else verdict
        if verdict in {"review", "unknown"}:
            result["reason"] = comparison["reason"]
    elif outcome == "release":
        result.update(verdict="review" if incompatible else "unknown", outcome="review" if incompatible else "unavailable")
        result["reason"] = "Release evidence is stale, incomplete or incompatible; recheck this exact source before selecting an update."
    for asset in result["releaseAssets"]:
        asset["assetProof"] = asset_proof(source, result, asset)
    return result


def _strict_url(raw: str):
    try:
        url = urlsplit(str(raw or ""))
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.port is not None or url.query or url.fragment or "\\" in raw
                or any(ord(char) < 32 or ord(char) == 127 for char in raw)
                or any(part in {".", ".."} or "/" in part or "\\" in part or any(ord(c) < 32 or ord(c) == 127 for c in part)
                       for part in map(unquote, url.path.split("/")))):
            return None
        return url
    except (ValueError, TypeError):
        return None


def asset_matches_source(source: dict, update: dict, asset: dict) -> bool:
    """Defensive provider/project/release binding for each server-owned URL."""
    from .source_registry import provider_of
    provider = provider_of(source.get("url") or "")
    if provider not in PUBLIC_API_PROVIDERS or source.get("verified") is not True or source.get("kind") == "advisory":
        return False
    if update.get("source") != f"{provider}-release-api" or update.get("provider", provider) != provider:
        return False
    name = str(asset.get("name") or "")
    if not name.lower().endswith(ARCHIVE_SUFFIXES) or any(x in name for x in ("/", "\\", "\0", "\r", "\n")):
        return False
    url = _strict_url(str(asset.get("url") or ""))
    original = urlsplit(_safe_url(source.get("url") or ""))
    if url is None:
        return False
    parts = [unquote(p) for p in original.path.split("/") if p]
    project = str(update.get("projectId") or "")
    release = str(update.get("releaseId") or "")
    # Older GitHub rows predate the normalized protocol. Preserve those exact
    # public repository assets, while every normalized row gets full binding.
    normalized = bool(update.get("outcome") or asset.get("assetKey") or project or release)
    if normalized and (not project or not release or not asset.get("assetKey")
                       or asset.get("provider") != provider or asset.get("projectId") != project
                       or asset.get("releaseId") != release or update.get("sourceUrl") != _safe_url(source.get("url") or "")):
        return False
    if provider == "github":
        if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", p) for p in parts[:2]):
            return False
        repo = parts[0] + "/" + parts[1].removesuffix(".git")
        return (url.hostname == "github.com" and url.path.casefold().startswith(("/" + repo + "/releases/download/").casefold())
                and (not normalized or (project.casefold() == repo.casefold()
                     and unquote(url.path[len("/" + repo + "/releases/download/"):].split("/", 1)[0]) == str(update.get("latestVersion") or "")))
                and type(asset.get("assetId")) is int and asset["assetId"] > 0)
    if provider == "gitlab":
        namespace = parts[:parts.index("-")] if "-" in parts else parts
        repo = "/".join(namespace).removesuffix(".git")
        def owned_gitlab_path(candidate):
            return candidate and candidate.hostname == "gitlab.com" and (
                candidate.path.startswith("/" + repo + "/-/uploads/")
                or candidate.path.startswith("/" + repo + "/uploads/")
                or candidate.path.startswith("/" + repo + "/-/releases/" + str(update.get("latestVersion") or "") + "/downloads/"))
        origin = _strict_url(str(asset.get("originUrl") or asset.get("url") or ""))
        return bool(repo and project == repo and owned_gitlab_path(url) and owned_gitlab_path(origin))
    if provider == "modrinth":
        return (url.hostname == "cdn.modrinth.com" and bool(re.fullmatch(r"[A-Za-z0-9]+", project))
                and bool(re.fullmatch(r"[A-Za-z0-9]+", release))
                and (url.path.startswith("/data/" + project + "/versions/" + release + "/")
                     or asset.get("releaseVersion") == update.get("latestVersion") and bool(asset.get("releaseVersion"))
                     and url.path.startswith("/data/" + project + "/versions/" + str(asset["releaseVersion"]) + "/")))
    if provider == "thunderstore":
        # projectId is the API's Namespace/Package identity; a community prefix
        # may be carried separately in compatibility and never changes ownership.
        package = project.split("/")
        version = str(update.get("latestVersion") or "")
        return (len(package) == 2 and all(re.fullmatch(r"[A-Za-z0-9_]+", p) for p in package)
                and bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version))
                and url.hostname == "thunderstore.io"
                and url.path == "/package/download/" + project + "/" + version + "/")
    return False


def asset_proof(source: dict, update: dict, asset: dict) -> str:
    """Bind the exact reviewed archive, including published hashes, across queue/install."""
    fields = {"sourceUrl": _safe_url(source.get("url") or ""), "provider": update.get("provider") or source.get("type"),
              "projectId": update.get("projectId"), "releaseId": update.get("releaseId"),
              "targetVersion": update.get("latestVersion"), "assetKey": asset.get("assetKey"),
              "assetId": asset.get("assetId"), "name": asset.get("name"), "url": asset.get("url"),
              "size": asset.get("size"), "originUrl": asset.get("originUrl"), "hashes": public_hashes(asset.get("hashes"))}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compact_source_update(update: dict) -> dict:
    """Small catalog facts; full release evidence is an exact-row request."""
    if not isinstance(update, dict):
        return {}
    if not (update.get("provider") in PUBLIC_API_PROVIDERS or update.get("outcome")
            or str(update.get("source", "")).endswith("-release-api")):
        return deepcopy(update)
    fields = {"provider", "source", "sourceUrl", "projectId", "releaseId", "releaseUrl", "releaseName",
              "verdict", "latestVersion", "checkedAt", "expiresAt", "retryAt", "availability", "freshness",
              "outcome", "reason", "capabilities", "sourceEngine", "publishedAt", "manualAssetCount", "channel", "moderationStatus"}
    result = {key: deepcopy(value) for key, value in update.items() if key in fields}
    compatibility = update.get("compatibility")
    if isinstance(compatibility, dict):
        result["compatibility"] = {key: deepcopy(value) for key, value in compatibility.items() if key in {"status", "reason"}}
    result["summary"] = str(update.get("summary") or "")[:300]
    result["releaseAssetCount"] = len(update.get("releaseAssets") or [])
    result["dependencyCount"] = len(update.get("dependencies") or [])
    result["detailsAvailable"] = True
    return result
