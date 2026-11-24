"""Modhunter's provider-aware source discovery and honest update checks.

The packaged Rust engine checks exact public release projects. GitHub/GitLab
retain a bounded legacy fallback when the helper is unavailable. Account-gated
sources retain useful capabilities without inventing versions or access.
"""
from __future__ import annotations
import concurrent.futures
import configparser
from copy import deepcopy
import ipaddress
import json
import logging
import re
import time
import threading
import tempfile
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .version_evidence import compare_versions
from .source_adapters import (RustSourceRunner, adapter_capabilities, normalize_adapter_record,
                              CACHE_LIMIT, PUBLIC_API_PROVIDERS, PUBLIC_RELEASE_TTL, asset_proof)


LOG = logging.getLogger(__name__)

PROVIDER_HOSTS = {
    "nexusmods.com": "nexus", "github.com": "github",
    "gitlab.com": "gitlab", "patreon.com": "patreon",
    "loverslab.com": "loverslab", "moddb.com": "moddb",
    "curseforge.com": "curseforge", "creations.bethesda.net": "bethesda",
    "dwemermods.com": "dwemer", "modding-guild.com": "modding-guild",
    "modrinth.com": "modrinth", "boosty.to": "boosty",
    "thunderstore.io": "thunderstore", "ko-fi.com": "ko-fi",
    "afdian.com": "afdian", "afdian.net": "afdian", "tumblr.com": "tumblr",
    "subscribestar.com": "subscribestar", "subscribestar.adult": "subscribestar",
    "modcollective.gg": "modcollective", "simsfinds.com": "simsfinds", "itch.io": "itch",
    "steamcommunity.com": "steam-workshop", "store.steampowered.com": "steam",
    "discord.com": "discord", "discord.gg": "discord", "youtube.com": "youtube", "youtu.be": "youtube",
    "bilibili.com": "bilibili", "b23.tv": "bilibili", "x.com": "x", "twitter.com": "x",
    "facebook.com": "facebook", "aml.name": "aml", "f95zone.to": "f95", "f95zone.com": "f95",
    "thesimsresource.com": "sims-resource", "modthesims.info": "modthesims",
}
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
PRIVATE_QUERY_KEYS = frozenset({
    "token", "access_token", "refresh_token", "id_token", "apikey", "api_key", "api-key", "key",
    "auth", "auth_token", "private_token", "oauth_token", "download_key", "authorization", "password", "passwd", "secret", "client_secret", "session", "sessionid",
    "session_id", "sid", "signature", "sig", "fbclid", "gclid", "dclid", "msclkid", "igshid",
    "ref_src", "ref_url",
})


def _public_query(query: str) -> str:
    # Keep raw surviving components: form re-encoding would change forum
    # routes such as index.php?/topic/123/ into a different destination.
    kept = []
    for component in query.split("&"):
        key = urllib.parse.unquote_plus(component.split("=", 1)[0]).lower()
        if key in PRIVATE_QUERY_KEYS or key.startswith(("utm_", "x-amz-", "x-goog-", "__cf_chl_")):
            continue
        kept.append(component)
    return "&".join(kept)


def canonical_source_url(raw: str) -> str:
    """Accept public HTTPS page links only; keep passwords and query tokens out."""
    try:
        raw = str(raw or "")
        # Match the native source-identity boundary before urlsplit silently
        # removes controls or truncation invents a different project route.
        if (len(raw) > 1800 or len(raw.encode("utf-8")) > 1800
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c == "\\" for c in raw)):
            return ""
        raw = raw.strip()
        u = urllib.parse.urlsplit(raw)
        host = (u.hostname or "").lower()
        if u.scheme.lower() != "https" or not host or u.username or u.password or u.port:
            return ""
        if host == "localhost" or host.endswith((".local", ".localhost")) or "." not in host:
            return ""
        if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host:
            return ""
        if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", host.rstrip(".").rsplit(".", 1)[-1]):
            return ""  # Also reject browser-normalized shorthand/octal/hex IPv4 forms.
        try:
            ipaddress.ip_address(host)
            return ""  # Source identities must be public domain names, not local/IP endpoints.
        except ValueError:
            pass
        return urllib.parse.urlunsplit(("https", host, u.path or "/", _public_query(u.query), ""))
    except (TypeError, ValueError):
        return ""


def provider_of(raw: str) -> str:
    url = canonical_source_url(raw)
    if not url:
        return "unknown"
    host = urllib.parse.urlsplit(url).hostname or ""
    host = host.removeprefix("www.")
    for domain, provider in PROVIDER_HOSTS.items():
        if host == domain or (provider in {"patreon", "loverslab", "curseforge", "moddb", "modrinth", "itch", "tumblr", "bilibili", "thunderstore"}
                              and host.endswith("." + domain)):
            return provider
    return "website"


def source_record(url: str, origin: str = "") -> dict:
    clean = canonical_source_url(url)
    if not clean:
        return {"type": "unknown", "verified": False, "kind": "unresolved", "label": "Source unlinked", "url": ""}
    provider = provider_of(clean)
    return {"type": provider, "verified": True, "kind": "project",
            "label": provider.replace("-", " ").title(), "url": clean, "origin": origin}


_SOURCE_FIELDS = {"url", "customurl", "modurl", "updateurl", "homepage", "website", "source"}
_RECEIPT_FIELD = re.compile(r"([0-9]+)\\(modid|fileid)$")


def _projected_ini_lines(stream):
    """Preserve QSettings sections, options and continuation semantics without retaining descriptions."""
    previous_indent = None
    keep_continuation = False
    relevant = _SOURCE_FIELDS | {"hascustomurl", "installationfile", "gamename", "nexusfilestatus",
                                 "ignoredversion", "notes", "size"}
    while True:
        line = stream.readline(65536)
        if not line:
            return
        truncated = len(line) == 65536 and not line.endswith("\n")
        if truncated:
            # Consume the remainder of this physical line without allocating it.
            while True:
                rest = stream.readline(65536)
                if not rest or rest.endswith("\n"):
                    break
        content = line.strip()
        if not content or content.startswith(("#", ";")):
            yield "\n"
            continue
        indent = len(line) - len(line.lstrip())
        if previous_indent is not None and indent > previous_indent:
            if keep_continuation:
                if truncated:
                    raise ValueError("Incomplete multiline identity in large MO2 metadata")
                yield line
            continue
        if content.startswith("["):
            previous_indent = None
            keep_continuation = False
            yield line
            continue
        option = re.match(r"([^:=]+)[:=]", content)
        if option is None:
            yield line
            continue
        field = option[1].strip().lower()
        keep_continuation = field in relevant or _RECEIPT_FIELD.fullmatch(field) is not None
        previous_indent = indent
        if not keep_continuation:
            yield line[:indent] + "__uninspected_value__=\n"
        elif field == "notes" and truncated:
            yield line[:32768] + "\n"  # Source discovery consumes only the first 24,000 note characters.
        elif truncated:
            raise ValueError("Incomplete identity field in large MO2 metadata")
        else:
            yield line


def _installed_receipts(parser, section):
    if section is None:
        return [], False
    indexed = {}
    incomplete = False
    for key, value in parser.items(section):
        found = _RECEIPT_FIELD.fullmatch(key)
        if found:
            if len(found[1]) > 20 or not found[1].isascii():
                incomplete = True
                continue
            indexed.setdefault(int(found[1]), {})[found[2]] = value
    declared = parser.get(section, "size", fallback=None)
    if declared is not None:
        incomplete |= not (declared.isascii() and declared.isdecimal() and len(declared) <= 20
                           and int(declared) == len(indexed))
    if indexed:
        incomplete |= min(indexed) != 1 or max(indexed) != len(indexed)
    receipts = []
    for record in indexed.values():
        mid, fid = record.get("modid", ""), record.get("fileid", "")
        if not all(x.isascii() and x.isdecimal() and len(x) <= 20 and int(x) > 0 for x in (mid, fid)):
            incomplete = True
            continue
        receipts.append([mid, fid])
    return receipts, incomplete


def discover_local_source(mod_directory: Path, notes: str = "") -> dict:
    """Read only a mod's own meta.ini and its existing notes."""
    ini = Path(mod_directory) / "meta.ini"
    candidates: list[tuple[str, str]] = []
    custom_enabled = None
    metadata_notes = ""
    file_identity = {}
    try:
        if ini.is_file():
            parser = None
            if ini.stat().st_size > 65536:
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                with ini.open(encoding="utf-8-sig", errors="replace") as stream:
                    parser.read_file(_projected_ini_lines(stream))
            else:
                text = ini.read_text(encoding="utf-8-sig", errors="replace")
                lower = text.lower()
                if "://" in text or any(field in lower for field in (
                        "hascustomurl", "installationfile", "[installedfiles]", "gamename", "nexusfilestatus", "ignoredversion")):
                    parser = configparser.ConfigParser(interpolation=None, strict=False)
                    parser.read_string(text)
            if parser is not None:
                general = next((section for section in parser.sections() if section.lower() == "general"), None)
                if general:
                    value = parser.get(general, "hascustomurl", fallback="").strip().lower()
                    custom_enabled = False if value in ("false", "0") else True if value in ("true", "1") else None
                    metadata_notes = parser.get(general, "notes", fallback="")
                    archive = parser.get(general, "installationFile", fallback="").replace("\\", "/").rsplit("/", 1)[-1]
                    installed = next((section for section in parser.sections() if section.lower() == "installedfiles"), None)
                    receipts, incomplete = _installed_receipts(parser, installed)
                    file_identity = {"installationArchive": archive, "installedFiles": receipts, "incomplete": incomplete,
                                     "game": parser.get(general, "gameName", fallback=""),
                                     "status": parser.get(general, "nexusFileStatus", fallback=""),
                                     "ignoredVersion": parser.get(general, "ignoredVersion", fallback="")}
                if not any(file_identity.values()):
                    file_identity = {}
                for section in parser.sections():
                    for key in ("url", "customurl", "modurl", "updateurl", "homepage", "website", "source"):
                        if parser.has_option(section, key):
                            candidates.append((parser.get(section, key), "MO2 meta.ini"))
    except (OSError, ValueError, UnicodeError, configparser.Error):
        file_identity = {"incomplete": True}
    for url, origin in candidates if custom_enabled is not False else []:
        record = source_record(url, origin)
        if record["verified"]:
            return {**record, "_mo2FileIdentity": file_identity} if file_identity else record
    for url in re.findall(r"https://[^\s<>'\"\]\)]+", (str(notes or "")[:24000] + " " + metadata_notes[:24000]), flags=re.I):
        record = source_record(url, "MO2 notes")
        if record["verified"]:
            # Notes also link dependencies and troubleshooting. Keep the link
            # available for inspection without claiming it identifies this mod.
            record.update(verified=False, kind="advisory", label="Unverified link from MO2 notes")
            if custom_enabled is False:
                record["customUrlEnabled"] = False
            return {**record, "_mo2FileIdentity": file_identity} if file_identity else record
    record = source_record("", "")
    if custom_enabled is False:
        record["customUrlEnabled"] = False
    return {**record, "_mo2FileIdentity": file_identity} if file_identity else record


def _release_identity(source: dict) -> tuple[str, str, str] | None:
    if not source.get("verified") or source.get("kind") == "advisory":
        return None
    url = source.get("url") or ""
    provider = provider_of(url)
    parts = [urllib.parse.unquote(p) for p in urllib.parse.urlsplit(url).path.split("/") if p]
    if provider not in ("github", "gitlab") or len(parts) < 2:
        return None
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", p) for p in parts[:2]):
        return None
    if provider == "github":
        return provider, f"{parts[0]}/{parts[1].removesuffix('.git')}", (
            f"https://api.github.com/repos/{parts[0]}/{parts[1].removesuffix('.git')}/releases/latest")
    # gitlab.com supports nested group paths. If URL points inside /-/,
    # the project path stops before that separator.
    group = parts[:parts.index("-")] if "-" in parts else parts
    if len(group) < 2 or len(group) > 8 or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", p) for p in group):
        return None
    project = "/".join(group)
    encoded = urllib.parse.quote(project, safe="")
    return provider, project, f"https://gitlab.com/api/v4/projects/{encoded}/releases/permalink/latest"


def _get_json(url: str) -> Any:
    class SameProviderRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            before, after = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
            if (after.scheme != "https" or after.hostname != before.hostname or after.port
                    or after.username or after.password):
                raise ValueError("Provider redirected outside its public API")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    req = urllib.request.Request(url, headers={"Accept": "application/json",
        "User-Agent": "Modhunter-Update-Scanner/0.9"})
    with urllib.request.build_opener(SameProviderRedirect()).open(req, timeout=13) as response:
        payload = response.read(3_000_001)
    if len(payload) > 3_000_000:
        raise ValueError("Provider response exceeded 3 MB")
    return json.loads(payload)


def _github_assets(project: str, data: dict) -> list[dict]:
    prefix = f"https://github.com/{project}/releases/download/"
    assets: list[dict] = []
    for file in data.get("assets", []) if isinstance(data.get("assets"), list) else []:
        if not isinstance(file, dict):
            continue
        try:
            aid = int(file.get("id") or 0)
            url = str(file.get("browser_download_url") or "")
            name = str(file.get("name") or "")
            if aid > 0 and url.startswith(prefix) and len(name) <= 240 and name.lower().endswith(ARCHIVE_SUFFIXES):
                entry = {"assetId": aid, "name": name, "url": url, "size": max(0, int(file.get("size") or 0))}
                digest = file.get("digest")
                if isinstance(digest, str) and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
                    entry["hashes"] = {"sha256": digest[7:].lower()}
                assets.append(entry)
        except (TypeError, ValueError, OverflowError):
            continue
    return assets


def release_verdict(item: dict, data: Any, provider: str, project: str) -> dict:
    """Only compare an actual published release tag; unknown is never current."""
    if not isinstance(data, dict) or not data:
        return {"verdict": "unknown", "reason": "No release metadata returned"}
    if data.get("draft") is True or data.get("prerelease") is True or data.get("upcoming_release") is True:
        return {"verdict": "review", "provider": provider, "source": f"{provider}-release-api",
                "reason": "Draft, prerelease or upcoming release requires an explicit channel choice."}
    version = str(data.get("tag_name") or "")[:120]
    # Enforce the same check for both providers; missing tag is not a version.
    if not version:
        return {"verdict": "unknown", "reason": "Provider did not publish a release tag"}
    comparison = compare_versions(str(item.get("installedVersion") or ""), version)
    verdict = comparison["verdict"]
    if verdict == "unknown":
        verdict = "review"
    release_url = str(data.get("_links", {}).get("self") or "") if provider == "gitlab" and isinstance(data.get("_links"), dict) else str(data.get("html_url") or "")
    if not release_url.startswith("https://"):
        release_url = str(item.get("source", {}).get("url") or "")
    now = time.time()
    result = {"versionComparison": comparison, "verdict": verdict, "latestVersion": version, "source": f"{provider}-release-api",
              "provider": provider, "releaseUrl": canonical_source_url(release_url),
              "reason": comparison["reason"],
              "releaseAssets": _github_assets(project, data) if provider == "github" else [],
              "checkedAt": now, "expiresAt": now + PUBLIC_RELEASE_TTL, "sourceUrl": canonical_source_url((item.get("source") or {}).get("url") or ""),
              "projectId": project, "releaseId": str(data.get("id") or version), "outcome": "release", "freshness": "fresh",
              "availability": "available", "changelog": str((data.get("body") if provider == "github" else data.get("description")) or "")[:24000],
              "publishedAt": str(data.get("published_at") or data.get("released_at") or "")[:80]}
    listed = data.get("assets")
    if provider == "gitlab":
        listed = listed.get("links") if isinstance(listed, dict) else None
    if isinstance(listed, list):
        reviewed = {asset["assetId"] for asset in result["releaseAssets"]}
        result["manualFiles"] = [
            {"name": str(entry.get("name") or ""), "assetId": entry.get("id") if type(entry.get("id")) is int else None,
             "size": entry.get("size") if type(entry.get("size")) is int and entry["size"] >= 0 else None}
            for entry in listed if isinstance(entry, dict) and not (type(entry.get("id")) is int and entry["id"] in reviewed)]
        result["manualAssetCount"] = len(result["manualFiles"])
    author = data.get("author")
    if isinstance(author, dict):
        result["publisher"] = {"name": str(author.get("name") or author.get("login") or author.get("username") or "")[:180],
                               "url": canonical_source_url(author.get("html_url") or author.get("web_url") or ""),
                               "avatarUrl": canonical_source_url(author.get("avatar_url") or ""), "role": "release-publisher"}
    for asset in result["releaseAssets"]:
        asset.update(assetKey=f"github:{asset['assetId']}", provider=provider, projectId=project, releaseId=result["releaseId"])
        asset["assetProof"] = asset_proof(item.get("source") or {}, result, asset)
    return result


class SourceScanner:
    def __init__(self, fetch_json: Callable[[str], Any] | None = None, *, runner=None, cache_path: Path | None = None, advisory_store=None):
        self.advisory_store = advisory_store
        self.fetch_json = fetch_json or _get_json
        self._injected_fetch = fetch_json is not None
        self.runner = runner or RustSourceRunner(timeout=130)
        self.cache_path = cache_path
        self._cache_lock = threading.Lock()
        self._scan_epoch = 0
        self._cache = {"entries": []}
        if cache_path:
            try:
                if cache_path.is_file() and cache_path.stat().st_size <= CACHE_LIMIT:
                    loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
                        self._cache = loaded
            except (OSError, ValueError, UnicodeError):
                pass

    def _save_cache(self, cache: dict, *, epoch: int, cancel_event) -> bool:
        if not isinstance(cache, dict) or not isinstance(cache.get("entries"), list) or len(cache["entries"]) > 256:
            with self._cache_lock:
                return self._scan_epoch == epoch and not cancel_event.is_set()
        payload = json.dumps(cache, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) > CACHE_LIMIT:
            with self._cache_lock:
                return self._scan_epoch == epoch and not cancel_event.is_set()
        with self._cache_lock:
            # Cancellation checked before this lock cannot protect a delayed
            # write. Only the newest owned scan may update memory or disk.
            if self._scan_epoch != epoch or cancel_event.is_set():
                return False
            self._cache = deepcopy(cache)
            if not self.cache_path:
                return True
            temporary = None
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=self.cache_path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.cache_path)
            except OSError:
                pass  # The in-memory observation remains usable; no stale cache claim.
            finally:
                if temporary:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as exc:
                        # Cleanup is best-effort too: keep the completed scan
                        # usable and the last-good on-disk cache untouched.
                        LOG.warning("Source cache temporary cleanup failed: %s", type(exc).__name__)
            return True

    @staticmethod
    def _items(snapshot: dict) -> list[dict]:
        items = []
        for item in snapshot.get("items", []):
            source = item.get("source") or {}
            try:
                nexus = int(item.get("nexusId") or 0)
            except (ValueError, TypeError, OverflowError):
                nexus = 0
            if nexus > 0 and not (source.get("verified") is True and
                                 provider_of(source.get("url", "")) not in {"nexus", "unknown"}):
                continue
            items.append(item)
        return items

    def scan(self, snapshot: dict, progress: Callable[[dict], None] | None = None, *, cancel_event=None, force=False) -> dict:
        cancel_event = cancel_event if cancel_event is not None else threading.Event()
        result = self._scan_releases(snapshot, progress, cancel_event=cancel_event, force=force)
        if self.advisory_store and not result.get("cancelled") and not cancel_event.is_set():
            self.advisory_store.annotate(snapshot.get("items", []), result.get("itemRecords", {}), cancel_event)
        return result

    def _scan_releases(self, snapshot, progress, *, cancel_event, force):
        """Check exact external sources with Rust; keep missing helpers recoverable."""
        cancel_event = cancel_event if cancel_event is not None else threading.Event()
        cancelled_result = {"records": {}, "itemRecords": {}, "mode": "sources", "queried": 0, "errors": [], "cancelled": True}
        with self._cache_lock:
            if cancel_event.is_set():
                return cancelled_result
            self._scan_epoch += 1
            epoch = self._scan_epoch
            cache = deepcopy(self._cache)
        items = self._items(snapshot)
        if self._injected_fetch or not self.runner.available:
            return self._scan_legacy(snapshot, items, progress, cancel_event=cancel_event)
        request = {"schemaVersion": 1, "profileId": str(snapshot.get("profileId") or ""),
                   "game": str(snapshot.get("game") or "unknown"),
                   "gameRuntime": str(snapshot.get("gameRuntime") or "unknown"),
                   "loader": str(snapshot.get("loader") or ""), "force": bool(force),
                   "budgetMs": 45_000, "workers": 4, "cache": cache,
                   "items": [{"id": str(i.get("id") or ""), "installedVersion": str(i.get("installedVersion") or ""),
                              "source": {**(i.get("source") or {}), "url": canonical_source_url((i.get("source") or {}).get("url") or "")}}
                             for i in items]}
        # The Rust fetch and parser depend on profile context and verified source,
        # while installed-version comparison belongs to each Python catalog row.
        # Send one representative of an identical source across the process boundary.
        representatives, owners, seen_ids, lookup_items = {}, {}, set(), []
        for item in request["items"]:
            source = item["source"]
            key = source["url"]
            if item["id"] in seen_ids or not item["id"]:
                # Preserve the helper's validation of missing or duplicate native row IDs.
                lookup_items = request["items"]
                owners = {}
                break
            seen_ids.add(item["id"])
            if source.get("verified") is True and source.get("kind") != "advisory" and provider_of(key) in PUBLIC_API_PROVIDERS:
                if key in representatives:
                    owners[item["id"]] = representatives[key]
                    continue
                representatives[key] = item["id"]
            lookup_items.append(item)
        request["items"] = lookup_items
        if progress:
            progress({"phase": "checking-source-adapters", "checked": 0, "total": len(items), "sourceEngine": "rust"})
        deadline = time.monotonic() + request["budgetMs"] / 1000
        try:
            result = self.runner.run(request, cancel_event=cancel_event)
        except (RuntimeError, ValueError, OSError, TypeError) as exc:
            # A failed or incompatible helper is explicit. Retrying via a second
            # transport could bypass its cooldown/deadline and mask the failure.
            records = {str(item.get("id")): self._unavailable(item, "Rust source check unavailable: " + str(exc)[:300]) for item in items}
            return {"records": {}, "itemRecords": records, "mode": "sources", "sourceEngine": "rust",
                    "sourceProjects": 0, "manualSources": 0, "queried": 0,
                    "errors": ["Rust source check failed; existing observations were not promoted to current."], "checkedAt": time.time()}
        self._complete_release_details(request, result, cancel_event, deadline, epoch)
        if not self._save_cache(result.get("cache") or {}, epoch=epoch, cancel_event=cancel_event):
            return cancelled_result
        records, normalized = {}, {}
        shared = set(owners.values())
        checked_now = time.time()
        for item in items:
            item_id = str(item.get("id") or "")
            owner = owners.get(item_id, item_id)
            raw = result["itemRecords"].get(owner)
            if not isinstance(raw, dict):
                records[item_id] = self._unavailable(item, "Adapter did not return this exact source; retry the check.")
                continue
            key = (owner, str(item.get("installedVersion") or ""))
            if key in normalized:
                # Consumers may attach row-specific health evidence or mutate nested metadata.
                records[item_id] = deepcopy(normalized[key])
            else:
                records[item_id] = normalize_adapter_record(item, raw, now=checked_now)
                if owner in shared:
                    normalized[key] = records[item_id]
        stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
        stats.update(items=len(items), providerLookupRows=len(lookup_items))
        queried = int(stats.get("queried") or stats.get("requests") or 0)
        projects = int(stats.get("sourceProjects") or len({(r.get("provider"), r.get("projectId")) for r in records.values() if r.get("projectId")}))
        if progress:
            progress({"phase": "source-adapters-complete", "checked": len(items), "total": len(items), "sourceEngine": "rust"})
        return {"records": {}, "itemRecords": records, "mode": "sources", "sourceEngine": "rust",
                "sourceProjects": projects, "manualSources": sum(r.get("outcome") in {"manual", "unsupported"} for r in records.values()),
                "queried": queried, "adapterStats": stats, "errors": [str(x)[:300] for x in result.get("errors", [])[:100]], "checkedAt": time.time()}

    def _complete_release_details(self, request, result, cancel_event, deadline, epoch):
        """Recover rows compacted by the native helper's aggregate response byte budget."""
        records = result["itemRecords"]
        partial = {key for key, raw in records.items() if isinstance(raw, dict) and raw.get("detailsTruncated")}
        if not partial:
            return
        # The bounded Rust batch has finished. Retain its complete HTTP
        # observations before any slower per-project detail requests.
        if not self._save_cache(result.get("cache") or {}, epoch=epoch, cancel_event=cancel_event):
            return
        complete = {}
        for raw in records.values():
            if isinstance(raw, dict) and not raw.get("detailsTruncated") and raw.get("sourceUrl") and raw.get("outcome") == "release":
                complete[raw["sourceUrl"]] = raw
        for item in request["items"]:
            key = item["id"]
            if key not in partial:
                continue
            source = item["source"]["url"]
            if not source or records[key].get("sourceUrl") != source:
                continue
            if source not in complete:
                remaining = int((deadline - time.monotonic()) * 1000)
                if remaining < 100 or cancel_event.is_set():
                    break
                with self._cache_lock:
                    if self._scan_epoch != epoch:
                        break
                # A single project cannot lose details behind unrelated records.
                # Reuse the latest provider cache and backoff, including earlier retries.
                retry = dict(request, items=[item], force=False, budgetMs=min(remaining, request["budgetMs"]),
                             cache=result.get("cache") or {"entries":[]})
                try:
                    single = self.runner.run(retry, cancel_event=cancel_event)
                except (RuntimeError, ValueError, TypeError, OSError):
                    complete[source] = None
                    continue
                candidate = single.get("itemRecords", {}).get(key)
                complete[source] = candidate if isinstance(candidate, dict) and candidate.get("sourceUrl") == source else None
                if isinstance(single.get("cache"), dict):
                    result["cache"] = single["cache"]
                totals = result.setdefault("stats", {})
                extra = single.get("stats") or {}
                for name in ("queried","cacheHits","revalidated","rateLimited"):
                    if type(extra.get(name)) is int:
                        totals[name] = int(totals.get(name) or 0) + extra[name]
                totals["elapsedMs"] = (time.monotonic() - deadline + request["budgetMs"] / 1000) * 1000
                result.setdefault("errors", []).extend(single.get("errors") or [])
            recovered = complete[source]
            if recovered is not None:
                records[key] = recovered

    def _unavailable(self, item: dict, reason: str) -> dict:
        source = item.get("source") or {}
        provider = provider_of(source.get("url") or "")
        return {"provider": provider, "source": provider, "sourceUrl": canonical_source_url(source.get("url") or ""),
                "verdict": "unknown", "outcome": "unavailable", "availability": "unknown", "freshness": "unverified",
                "checkedAt": time.time(), "reason": reason, "capabilities": adapter_capabilities(provider, helper_available=self.runner.available)}

    def _scan_legacy(self, snapshot, items, progress, *, cancel_event=None) -> dict:
        """Bounded compatibility path for installations without the matching helper."""
        checked_at = time.time()
        grouped, output, errors = {}, {}, []
        for item in items:
            ident = _release_identity(item.get("source") or {})
            if ident:
                grouped.setdefault(ident, []).append(item)
            else:
                src = item.get("source") or {}
                provider = provider_of(src.get("url", ""))
                capability = adapter_capabilities(provider, helper_available=False)
                output[str(item.get("id") or "")] = {
                    "verdict": "unverified", "source": provider, "outcome": "manual", "freshness": "unverified",
                    "checkedAt": checked_at, "provider": provider, "availability": "unknown", "capabilities": capability,
                    "sourceUrl": canonical_source_url(src.get("url", "")), "reason": capability["reason"],
                }
        manual_count = len(output)
        if progress:
            progress({"phase": "checking-non-nexus", "checked": 0, "total": len(grouped), "sourceEngine": "python-fallback"})
        completed = 0
        remaining = iter(grouped)
        deadline = time.monotonic() + 45
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        pending = {}
        def submit_one():
            ident = next(remaining, None)
            if ident is not None:
                pending[pool.submit(self.fetch_json, ident[2])] = ident
        for _ in range(min(4, len(grouped))):
            submit_one()
        try:
            while pending and time.monotonic() < deadline and not (cancel_event and cancel_event.is_set()):
                done, _ = concurrent.futures.wait(pending, timeout=min(0.2, max(0.0, deadline-time.monotonic())), return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    provider, project, _ = ident = pending.pop(future)
                    completed += 1
                    try:
                        data = future.result()
                        for item in grouped[ident]:
                            result = release_verdict(item, data, provider, project)
                            result.update(capabilities=adapter_capabilities(provider, helper_available=False),
                                          freshness="fresh", sourceEngine="python-fallback")
                            output[str(item.get("id") or "")] = result
                    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, TypeError, OSError):
                        errors.append(f"{provider}/{project}: public release check unavailable")
                        for item in grouped[ident]:
                            output[str(item.get("id") or "")] = self._unavailable(item, "Release check failed; do not assume this mod is current.")
                    if progress:
                        progress({"phase": "checking-non-nexus", "checked": completed, "total": len(grouped), "sourceEngine": "python-fallback"})
                    if time.monotonic() < deadline and not (cancel_event and cancel_event.is_set()):
                        submit_one()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for group in grouped.values():
            for item in group:
                key = str(item.get("id") or "")
                if key not in output:
                    output[key] = self._unavailable(item, "Source check was cancelled or reached its time budget; retry this source.")
                    errors.append("Source check did not finish within its budget")
        return {"records": {}, "itemRecords": output, "mode": "sources", "sourceEngine": "python-fallback",
                "sourceProjects": len(grouped), "manualSources": manual_count,
                "queried": completed, "errors": list(dict.fromkeys(errors)), "checkedAt": checked_at}
