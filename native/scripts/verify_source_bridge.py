#!/usr/bin/env python3
"""Exercise production Python -> compiled Rust source adapters with public API fixtures.

The fresh cache seeds are deterministic transport fixtures, not live provider
observations. Every exercised case must make zero network requests.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mo2_update_arsenal.source_registry import SourceScanner
from mo2_update_arsenal.source_adapters import RustSourceRunner
from mo2_update_arsenal.quick_update import choose_release_asset
from mo2_update_arsenal.update_plan import validate_release_asset, PlanError


def fixtures(date):
    github = {"id": 42, "tag_name": "v2.0", "name": "Version 2", "url": "https://api.github.com/repos/owner/mod/releases/42",
        "html_url": "https://github.com/owner/mod/releases/tag/v2.0", "draft": False, "prerelease": False,
        "published_at": date, "body": "Fixes and migration notes.",
        "author": {"login": "Publisher", "html_url": "https://github.com/Publisher", "avatar_url": "https://avatars.githubusercontent.com/u/42?v=4"},
        "assets": [{"id": 50, "name": "Mod.zip", "state": "uploaded", "size": 4200, "digest": "sha256:" + "a" * 64,
                    "browser_download_url": "https://github.com/owner/mod/releases/download/v2.0/Mod.zip"}]}
    gitlab = {"tag_name": "v2.0", "name": "Stable", "description": "Updated plugin", "released_at": date,
        "_links": {"self": "https://gitlab.com/group/subgroup/mod/-/releases/v2.0"},
        "author": {"name": "Release publisher", "web_url": "https://gitlab.com/publisher"},
        "assets": {"links": [{"id": 5, "name": "Mod.zip", "url": "https://gitlab.com/group/subgroup/mod/uploads/abcdef/Mod.zip",
                               "direct_asset_url": "https://gitlab.com/group/subgroup/mod/-/releases/v2.0/downloads/Mod.zip"}]}}
    modrinth = {"id": "AABBCCDD", "slug": "example", "team": "11223344", "title": "Example", "description": "Fixture mod",
        "status": "approved", "project_type": "mod", "icon_url": "https://cdn.modrinth.com/data/AABBCCDD/icon.png",
        "gallery": [{"url": "https://cdn.modrinth.com/data/AABBCCDD/images/screen.png", "title": "Screenshot"}]}
    version = {"id": "VVVVVVVV", "project_id": "AABBCCDD", "author_id": "AAAAAAAA", "name": "Example 2", "version_number": "2.0",
        "version_type": "release", "status": "listed", "date_published": date, "game_versions": ["1.21.1"], "loaders": ["fabric"], "changelog": "Changes.",
        "dependencies": [{"project_id": "DEPNAME1", "version_id": None, "dependency_type": "required", "file_name": None}],
        "files": [{"filename": "Example.zip", "url": "https://cdn.modrinth.com/data/AABBCCDD/versions/VVVVVVVV/Example.zip",
                   "size": 4500, "primary": True, "hashes": {"sha512": "b" * 128}}]}
    thunderstore = {"namespace": "Team", "name": "Example", "full_name": "Team-Example", "owner": "Team", "is_deprecated": False,
        "community_listings": [{"community": "valheim", "review_status": "approved"}],
        "latest": {"namespace": "Team", "name": "Example", "full_name": "Team-Example-2.0.0", "version_number": "2.0.0", "is_active": True,
                   "description": "Fixture", "icon": "https://gcdn.thunderstore.io/live/repository/icons/example.png", "date_created": date,
                   "dependencies": ["Dependency-Library-1.2.3"], "download_url": "https://thunderstore.io/package/download/Team/Example/2.0.0/"}}
    base_versions = "https://api.modrinth.com/v2/project/AABBCCDD/version?"
    return {
        "https://api.github.com/repos/owner/mod/releases/latest": github,
        "https://gitlab.com/api/v4/projects/group%2Fsubgroup%2Fmod/releases/permalink/latest": gitlab,
        "https://api.modrinth.com/v2/project/example": modrinth,
        base_versions + urlencode({"include_changelog": "true"}): [version],
        base_versions + urlencode({"include_changelog": "true", "loaders": '["fabric"]', "game_versions": '["1.21.1"]'}): [version],
        "https://thunderstore.io/api/experimental/package/Team/Example/": thunderstore,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cli = args.cli.resolve()
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    binary_hash = digest(cli)
    files = ["mo2_update_arsenal/source_registry.py", "mo2_update_arsenal/source_adapters.py",
             "mo2_update_arsenal/plugin_intelligence.py", "mo2_update_arsenal/update_plan.py", "mo2_update_arsenal/quick_update.py"]
    files += [str(path.relative_to(ROOT)) for path in sorted((ROOT/"native/crates/modhunter-core/src/source_adapters").glob("*.rs"))]
    files.append("native/crates/modhunter-core/src/bin/modhunter-cli.rs")
    sources = {name: digest(ROOT/name) for name in files}
    checks = []
    def require(condition, name):
        if not condition:
            raise RuntimeError("Source protocol failed: " + name)
        checks.append(name)
    now = int(time.time())
    date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    bodies = fixtures(date)
    seed = {"entries": [{"url": url, "body": body, "checkedAt": now, "expiresAt": now + 900, "retryAt": 0, "lastStatus": 200}
                         for url, body in bodies.items()]}
    rows = [
        ("github", "https://github.com/Owner/Mod", "skyrimspecialedition", "1.6.1170", ""),
        ("gitlab", "https://gitlab.com/group/subgroup/mod", "skyrimspecialedition", "1.6.1170", ""),
        ("modrinth", "https://modrinth.com/mod/example", "minecraft", "1.21.1", "fabric"),
        ("thunderstore", "https://thunderstore.io/c/valheim/p/Team/Example/", "valheim", "unknown", ""),
    ]
    with tempfile.TemporaryDirectory(prefix="modhunter-source-protocol-") as tmp:
        cache = Path(tmp)/"public-fixture-cache.json"
        for provider, url, game, runtime, loader in rows:
            cache.write_text(json.dumps(seed))
            scanner = SourceScanner(runner=RustSourceRunner(cli, timeout=15), cache_path=cache)
            item = {"id": provider + "-row", "installedVersion": "1.0", "nexusId": 0,
                    "source": {"type": provider, "url": url, "verified": True, "kind": "project"}}
            snap = {"profileId": "source-protocol", "game": game, "gameRuntime": runtime, "loader": loader, "items": [item]}
            result = scanner.scan(snap)
            update = result["itemRecords"][item["id"]]
            require(not result["errors"], provider + " response completed")
            require(result["sourceEngine"] == "rust" and result["queried"] == 0, provider + " actual helper/cache with zero network requests")
            require(update["verdict"] == "candidate" and update["freshness"] == "cached", provider + " parsed release compared against installed version")
            item["update"] = update
            picked, reason = choose_release_asset(item)
            require(bool(picked), provider + " normalized exact archive passed Python preflight: " + reason)
            selection = {"kind": "source", "itemId": item["id"], "assetKey": picked["assetKey"], "assetProof": picked["assetProof"],
                         "installedVersion": "1.0", "targetVersion": update["latestVersion"], "reviewed": True, "riskAcknowledged": True}
            require(validate_release_asset(snap, selection)["provider"] == provider, provider + " review resolves from owned record")
            update["releaseAssets"][0]["hashes"] = {"sha256": "c" * 64}
            try:
                validate_release_asset(snap, selection)
            except PlanError:
                checks.append(provider + " same asset identity with changed hash rejected")
            else:
                raise RuntimeError("Changed archive evidence was accepted")
            if provider in {"modrinth", "thunderstore"}:
                snap["game"] = "skyrimspecialedition"
                cache.write_text(json.dumps(seed))
                other = SourceScanner(runner=RustSourceRunner(cli, timeout=15), cache_path=cache).scan(snap)
                require(other["itemRecords"][item["id"]]["verdict"] == "review" and other["queried"] == 0,
                        provider + " mismatched game is review-only")
    require(binary_hash == digest(cli), "helper remained unchanged throughout protocol proof")
    require(sources == {name: digest(ROOT/name) for name in files}, "production sources remained unchanged throughout protocol proof")
    receipt = {"schemaVersion": 1, "ok": True, "recordedAt": datetime.now(timezone.utc).isoformat(),
               "scope": "Synthetic public API cache fixtures through production Python scanner and compiled Rust CLI; no live provider, native MO2, install or game claim.",
               "platform": platform.system(), "python": platform.python_version(), "helperSha256": binary_hash,
               "sourceHashes": sources, "networkRequests": 0, "checksPassed": len(checks), "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ok": True, "checksPassed": len(checks), "networkRequests": 0, "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
