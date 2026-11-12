"""Modhunter's provider-aware source discovery and honest update checks.

Only public GitHub/GitLab release APIs can establish version comparisons here.
Authenticated/non-API pages are retained as manual-review sources: never infer
"current" from an inaccessible website, timestamp, or missing credential.
"""
from __future__ import annotations
import concurrent.futures
import configparser
import ipaddress
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .scanner import version_verdict


PROVIDER_HOSTS = {
    "nexusmods.com": "nexus", "github.com": "github",
    "gitlab.com": "gitlab", "patreon.com": "patreon",
    "loverslab.com": "loverslab", "moddb.com": "moddb",
    "curseforge.com": "curseforge", "creations.bethesda.net": "bethesda",
    "dwemermods.com": "dwemer", "modding-guild.com": "modding-guild",
    "modrinth.com": "modrinth", "boosty.to": "boosty",
    "thunderstore.io": "thunderstore", "ko-fi.com": "ko-fi",
}
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
PRIVATE_QUERY_KEYS = frozenset({
    "token", "access_token", "refresh_token", "id_token", "apikey", "api_key", "api-key", "key",
    "auth", "authorization", "password", "passwd", "secret", "client_secret", "session", "sessionid",
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
        if host == domain or (provider in {"patreon", "loverslab", "curseforge", "moddb", "modrinth"}
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
    group = parts[:parts.index("-")] if "-" in parts else parts[:2]
    if len(group) < 2 or len(group) > 8 or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", p) for p in group):
        return None
    project = "/".join(group)
    encoded = urllib.parse.quote(project, safe="")
    return provider, project, f"https://gitlab.com/api/v4/projects/{encoded}/releases/permalink/latest"


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json",
        "User-Agent": "Modhunter-Update-Scanner/0.9"})
    with urllib.request.urlopen(req, timeout=13) as response:
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
    result = {"verdict": verdict, "latestVersion": version, "source": f"{provider}-release-api",
              "provider": provider, "releaseUrl": canonical_source_url(release_url),
              "reason": "Published release tag compared with installed MO2 version; verify exact assets and game runtime.",
              "releaseAssets": _github_assets(project, data) if provider == "github" else [],
              "checkedAt": time.time()}
    return result


class SourceScanner:
    def __init__(self, fetch_json: Callable[[str], Any] | None = None):
        self.fetch_json = fetch_json or _get_json

    def scan(self, snapshot: dict, progress: Callable[[dict], None] | None = None) -> dict:
        """Check all non-Nexus sources, de-duplicated by public release project."""
        # A Nexus-linked mod with no confirmed external source remains the
        # Nexus scanner's responsibility. Never overwrite a real Nexus verdict
        # with an unrelated "unverified" provider result.
        items = [i for i in snapshot.get("items", []) if
                 int(i.get("nexusId") or 0) <= 0 or (
                     bool((i.get("source") or {}).get("verified"))
                     and provider_of((i.get("source") or {}).get("url", "")) not in ("nexus", "unknown")
                 )]
        checked_at = time.time()
        grouped: dict[tuple[str, str, str], list[dict]] = {}
        output: dict[str, dict] = {}
        errors: list[str] = []
        manual_count = 0
        for item in items:
            ident = _release_identity(item.get("source") or {})
            if ident:
                grouped.setdefault(ident, []).append(item)
            else:
                src = item.get("source") or {}
                provider = provider_of(src.get("url", ""))
                output[str(item.get("id") or "")] = {
                    "verdict": "unverified", "source": provider,
                    "checkedAt": checked_at, "provider": provider,
                    "sourceUrl": canonical_source_url(src.get("url", "")),
                    "reason": "Open the original project page with your own authenticated browser session; no public version API verified this mod.",
                }
                manual_count += 1
        if progress:
            progress({"phase": "checking-non-nexus", "checked": 0, "total": len(grouped)})
        def fetch(pair):
            provider, project, api_url = pair
            return pair, self.fetch_json(api_url)
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch, ident): ident for ident in grouped}
            for future in concurrent.futures.as_completed(futures):
                ident = futures[future]
                provider, project, _ = ident
                completed += 1
                try:
                    _, data = future.result()
                    for item in grouped[ident]:
                        output[str(item.get("id") or "")] = release_verdict(item, data, provider, project)
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError,
                        TypeError, json.JSONDecodeError, OSError) as exc:
                    errors.append(f"{provider}/{project}: {type(exc).__name__}")
                    for item in grouped[ident]:
                        output[str(item.get("id") or "")] = {
                            "verdict": "unknown", "provider": provider,
                            "source": f"{provider}-release-api", "checkedAt": checked_at,
                            "reason": "Release check failed; do not assume this mod is current."}
                if progress:
                    progress({"phase": "checking-non-nexus", "checked": completed, "total": len(grouped)})
        return {"records": {}, "itemRecords": output, "mode": "sources",
                "sourceProjects": len(grouped), "manualSources": manual_count,
                "queried": completed, "errors": errors,
                "checkedAt": checked_at}
