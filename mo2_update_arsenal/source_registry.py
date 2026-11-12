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

from .scanner import version_verdict
from .source_adapters import (RustSourceRunner, adapter_capabilities, normalize_adapter_record,
                              CACHE_LIMIT, PUBLIC_API_PROVIDERS, asset_proof)


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
        raw = str(raw or "").strip()[:1800]
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


def discover_local_source(mod_directory: Path, notes: str = "") -> dict:
    """Read only a mod's own small meta.ini and its existing notes."""
    ini = Path(mod_directory) / "meta.ini"
    candidates: list[tuple[str, str]] = []
    custom_enabled = None
    metadata_notes = ""
    try:
        if ini.is_file() and ini.stat().st_size <= 65536:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read_string(ini.read_text(encoding="utf-8-sig", errors="replace"))
            general = next((section for section in parser.sections() if section.lower() == "general"), None)
            if general:
                value = parser.get(general, "hascustomurl", fallback="").strip().lower()
                custom_enabled = False if value in ("false", "0") else True if value in ("true", "1") else None
                metadata_notes = parser.get(general, "notes", fallback="")
            for section in parser.sections():
                for key in ("url", "customurl", "modurl", "updateurl", "homepage", "website", "source"):
                    if parser.has_option(section, key):
                        candidates.append((parser.get(section, key), "MO2 meta.ini"))
    except (OSError, UnicodeError, configparser.Error):
        pass
    for url, origin in candidates if custom_enabled is not False else []:
        record = source_record(url, origin)
        if record["verified"]:
            return record
    for url in re.findall(r"https://[^\s<>'\"\]\)]+", (str(notes or "")[:24000] + " " + metadata_notes[:24000]), flags=re.I):
        record = source_record(url, "MO2 notes")
        if record["verified"]:
            # Notes also link dependencies and troubleshooting. Keep the link
            # available for inspection without claiming it identifies this mod.
            record.update(verified=False, kind="advisory", label="Unverified link from MO2 notes")
            if custom_enabled is False:
                record["customUrlEnabled"] = False
            return record
    record = source_record("", "")
    if custom_enabled is False:
        record["customUrlEnabled"] = False
    return record


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
            name = str(file.get("name") or "")[:190]
            if aid > 0 and url.startswith(prefix) and name.lower().endswith(ARCHIVE_SUFFIXES):
                assets.append({"assetId": aid, "name": name, "url": url,
                               "size": max(0, int(file.get("size") or 0))})
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
    verdict = version_verdict(str(item.get("installedVersion") or ""), version)
    if verdict == "unknown":
        verdict = "review"
    release_url = str(data.get("_links", {}).get("self") or "") if provider == "gitlab" and isinstance(data.get("_links"), dict) else str(data.get("html_url") or "")
    if not release_url.startswith("https://"):
        release_url = str(item.get("source", {}).get("url") or "")
    now = time.time()
    result = {"verdict": verdict, "latestVersion": version, "source": f"{provider}-release-api",
              "provider": provider, "releaseUrl": canonical_source_url(release_url),
              "reason": "Published release tag compared with installed MO2 version; verify exact assets and game runtime.",
              "releaseAssets": _github_assets(project, data) if provider == "github" else [],
              "checkedAt": now, "expiresAt": now + 900, "sourceUrl": canonical_source_url((item.get("source") or {}).get("url") or ""),
              "projectId": project, "releaseId": str(data.get("id") or version), "outcome": "release", "freshness": "fresh",
              "availability": "available", "changelog": str((data.get("body") if provider == "github" else data.get("description")) or "")[:24000],
              "publishedAt": str(data.get("published_at") or data.get("released_at") or "")[:80]}
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
    def __init__(self, fetch_json: Callable[[str], Any] | None = None, *, runner=None, cache_path: Path | None = None):
        self.fetch_json = fetch_json or _get_json
        self._injected_fetch = fetch_json is not None
        self.runner = runner or RustSourceRunner(timeout=130)
        self.cache_path = cache_path
        self._cache_lock = threading.Lock()
        self._cache = {"entries": []}
        if cache_path:
            try:
                if cache_path.is_file() and cache_path.stat().st_size <= CACHE_LIMIT:
                    loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
                        self._cache = loaded
            except (OSError, ValueError, UnicodeError):
                pass

    def _save_cache(self, cache: dict) -> None:
        if not isinstance(cache, dict) or not isinstance(cache.get("entries"), list) or len(cache["entries"]) > 256:
            return
        payload = json.dumps(cache, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) > CACHE_LIMIT:
            return
        with self._cache_lock:
            self._cache = deepcopy(cache)
            if not self.cache_path:
                return
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
                    temporary.unlink(missing_ok=True)

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
        """Check exact external sources with Rust; keep missing helpers recoverable."""
        items = self._items(snapshot)
        if self._injected_fetch or not self.runner.available:
            return self._scan_legacy(snapshot, items, progress, cancel_event=cancel_event)
        with self._cache_lock:
            cache = deepcopy(self._cache)
        request = {"schemaVersion": 1, "profileId": str(snapshot.get("profileId") or ""),
                   "game": str(snapshot.get("game") or "unknown"),
                   "gameRuntime": str(snapshot.get("gameRuntime") or "unknown"),
                   "loader": str(snapshot.get("loader") or ""), "force": bool(force),
                   "budgetMs": 45_000, "workers": 4, "cache": cache,
                   "items": [{"id": str(i.get("id") or ""), "installedVersion": str(i.get("installedVersion") or ""),
                              "source": {**(i.get("source") or {}), "url": canonical_source_url((i.get("source") or {}).get("url") or "")}}
                             for i in items]}
        if progress:
            progress({"phase": "checking-source-adapters", "checked": 0, "total": len(items), "sourceEngine": "rust"})
        try:
            result = self.runner.run(request, cancel_event=cancel_event)
        except (RuntimeError, ValueError, OSError, TypeError) as exc:
            # A failed or incompatible helper is explicit. Retrying via a second
            # transport could bypass its cooldown/deadline and mask the failure.
            records = {str(item.get("id")): self._unavailable(item, "Rust source check unavailable: " + str(exc)[:300]) for item in items}
            return {"records": {}, "itemRecords": records, "mode": "sources", "sourceEngine": "rust",
                    "sourceProjects": 0, "manualSources": 0, "queried": 0,
                    "errors": ["Rust source check failed; existing observations were not promoted to current."], "checkedAt": time.time()}
        if cancel_event is not None and cancel_event.is_set():
            return {"records": {}, "itemRecords": {}, "mode": "sources", "queried": 0, "errors": [], "cancelled": True}
        self._save_cache(result.get("cache") or {})
        records = {}
        for item in items:
            raw = result["itemRecords"].get(str(item.get("id")))
            records[str(item.get("id"))] = normalize_adapter_record(item, raw) if isinstance(raw, dict) else self._unavailable(item, "Adapter did not return this exact source; retry the check.")
        stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
        queried = int(stats.get("queried") or stats.get("requests") or 0)
        projects = int(stats.get("sourceProjects") or len({(r.get("provider"), r.get("projectId")) for r in records.values() if r.get("projectId")}))
        if progress:
            progress({"phase": "source-adapters-complete", "checked": len(items), "total": len(items), "sourceEngine": "rust"})
        return {"records": {}, "itemRecords": records, "mode": "sources", "sourceEngine": "rust",
                "sourceProjects": projects, "manualSources": sum(r.get("outcome") in {"manual", "unsupported"} for r in records.values()),
                "queried": queried, "adapterStats": stats, "errors": [str(x)[:300] for x in result.get("errors", [])[:100]], "checkedAt": time.time()}

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
