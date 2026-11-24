"""Export the native offline catalogue and recorded public Nexus observations to local HTML."""

import argparse
import base64
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mo2_update_arsenal.file_advisor import NexusFileIndex
from mo2_update_arsenal.scanner import (
    NexusError,
    incomplete_file_record,
    nexus_project_id,
    public_project_metadata,
    summary_from_metadata,
)
from mo2_update_arsenal.source_registry import canonical_source_url


def timestamp(text):
    value = datetime.fromisoformat(text)
    if value.utcoffset() is None:
        raise ValueError("Observation and catalog timestamps must specify their time zone.")
    return value


def load_catalog(path, expected_hash):
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_hash is not None and expected_hash.lower() != digest:
        raise ValueError("Native catalog SHA-256 differs from the requested snapshot.")
    catalog = json.loads(data)["catalog"]
    if (
        catalog.get("offline") is not True
        or not catalog.get("profileId")
        or not isinstance(catalog.get("items"), list)
    ):
        raise ValueError("Expected an offline modhunter-cli import with profileId and items.")
    timestamp(catalog["importedAt"])
    names, ids = set(), set()
    for row in catalog["items"]:
        if not isinstance(row, dict) or not isinstance(row.get("source") or {}, dict):
            raise ValueError("Invalid native catalog item or source.")
        if not isinstance(row["id"], str) or not row["id"] or row["id"] in ids:
            raise ValueError("Empty or duplicate catalog row ID.")
        if not isinstance(row["name"], str) or not row["name"].strip() or row["name"].casefold() in names:
            raise ValueError("Empty or duplicate catalog mod name.")
        receipts = row.get("installedFiles", [])
        if not isinstance(receipts, list) or any(
            not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int or n <= 0 for n in pair)
            for pair in receipts
        ):
            raise ValueError("Invalid native installedFiles project/file receipt.")
        ids.add(row["id"])
        names.add(row["name"].casefold())
    return catalog, digest


def historical_rows(catalog, database):
    @lru_cache(maxsize=None)
    def observation(game, project):
        records = database.execute(
            "SELECT payload,errors,seen FROM projects WHERE game=? AND mod_id=?", (game, project)
        ).fetchmany(2)
        if not records:
            return None, None, None, "No response for this project in the provider evidence snapshot"
        if len(records) != 1:
            raise ValueError(f"Duplicate observations for {game} project {project}.")
        raw, errors, seen = records[0]
        timestamp(seen)
        errors = json.loads(errors)
        if not isinstance(errors, list):
            raise ValueError("Expected a list of public Nexus errors.")
        if errors:
            return None, None, seen, "Provider: " + "; ".join(map(str, errors))
        project_data, files = json.loads(raw)
        try:
            meta = public_project_metadata(game, project, project_data, files)
        except NexusError as error:
            raise ValueError(f"Invalid observation for {game} project {project}: {error}") from error
        return meta, NexusFileIndex(meta["fileCatalog"]), seen, None

    for item in catalog["items"]:
        raw_game = item.get("sourceGame")
        game = None if raw_game == "unknown" else raw_game
        project = item.get("nexusId")
        seen = None
        url = ""
        info = {"verdict": "unverified"}
        if item.get("installedFileEvidenceIncomplete"):
            info = incomplete_file_record()
        elif not isinstance(game, str) or not re.fullmatch(r"[a-z0-9-]+", game):
            info["reason"] = "Game identity unresolved"
        elif type(project) is not int or project <= 0:
            info["reason"] = "No Nexus project ID"
        elif not nexus_project_id(item, game):
            info["reason"] = "Provider or game identity not admitted by product importer"
        else:
            url = f"https://www.nexusmods.com/{game}/mods/{project}?tab=files"
            meta, index, seen, failure = observation(game, project)
            if failure:
                info["reason"] = failure
                if failure.startswith("Provider:"):
                    info["verdict"] = "unavailable"
            else:
                info = summary_from_metadata(item, meta, timestamp(seen).timestamp(), index)
                if info.get("targetFileId"):
                    url += f"&file_id={int(info['targetFileId'])}"
        source = item.get("source") or {}
        # Match desktop history::binding. Include every native field used to identify or compare releases.
        identity = [
            item.get(key)
            for key in (
                "id",
                "name",
                "installedVersion",
                "latestKnownVersion",
                "sourceGame",
                "nexusId",
                "installedFiles",
                "installationArchive",
                "ignoredVersion",
                "installedFileEvidenceIncomplete",
                "updateEligible",
                "missingFromDisk",
                "notInProfile",
                "foreign",
                "repository",
            )
        ] + [source.get("type"), source.get("url")]
        binding = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        yield {
            "binding": binding,
            "id": item["id"],
            "name": item["name"],
            "game": game,
            "project": project,
            "installed": item.get("installedVersion"),
            "cached": item.get("latestKnownVersion"),
            "seen": seen,
            "verdict": info["verdict"],
            "reason": info.get("reason"),
            "installedFile": info.get("installedFileId"),
            "targetFile": info.get("targetFileId"),
            "release": info.get("latestVersion"),
            "projectVersion": info.get("projectVersion"),
            "scope": info.get("evidenceScope"),
            "receipts": item.get("installedFiles", []),
            "providerUrl": url,
            "sourceUrl": canonical_source_url(source.get("url")),
        }


def verify_rows(path, rows):
    """Cross-check an optional prior JSONL census, including input identity and exact decision."""
    by_name = {row["name"]: row for row in rows}
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            saved = json.loads(line)
            fresh = by_name.pop(saved["name"], None)
            decision = saved["decision"]
            if fresh is None:
                raise ValueError(f"Unknown or duplicate reconciliation row on line {line_number}.")
            compared = (
                (saved["installed"], fresh["installed"]),
                (saved["sourceGame"], fresh["game"]),
                (saved["nexusId"], fresh["project"]),
                (saved["mo2Newest"], fresh["cached"]),
                (saved["observedAt"], fresh["seen"]),
                (saved["sourceUrl"] or "", fresh["providerUrl"]),
                (decision["verdict"], fresh["verdict"]),
                (decision.get("latestVersion"), fresh["release"]),
                (decision.get("installedFileId"), fresh["installedFile"]),
                (decision.get("targetFileId"), fresh["targetFile"]),
                (decision.get("reason") or saved.get("failure"), fresh["reason"]),
                (decision.get("projectVersion"), fresh["projectVersion"]),
            )
            if any(a != b for a, b in compared):
                raise ValueError(f"Reconciliation identity or decision differs on line {line_number}.")
    if by_name:
        raise ValueError(f"Reconciliation is missing {len(by_name)} catalog rows.")


def export(catalog_path, observations_path, output, prior=None, expected_hash=None):
    inputs = [catalog_path, observations_path] + ([prior] if prior else [])
    if output.suffix.lower() not in (".html", ".json") or any(
        path.resolve() == output.resolve() for path in inputs
    ):
        raise ValueError("Choose an HTML or JSON output separate from the evidence inputs.")
    if prior and not expected_hash:
        raise ValueError("--verify-rows requires the original --catalog-sha256.")
    catalog, digest = load_catalog(catalog_path, expected_hash)
    with closing(sqlite3.connect(observations_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = list(historical_rows(catalog, db))
    if prior:
        verify_rows(prior, rows)
    dates = [timestamp(row["seen"]) for row in rows if row["seen"]]
    facts = {
        "format": "modhunter-nexus-history-v1",
        "profile": catalog["profileName"],
        "profileId": catalog["profileId"],
        "catalogSHA256": digest,
        "importedAt": catalog["importedAt"],
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "firstObservation": min(dates).isoformat() if dates else None,
        "lastObservation": max(dates).isoformat() if dates else None,
        "totals": dict(Counter(row["verdict"] for row in rows)),
        "rows": rows,
    }
    data = json.dumps(facts, ensure_ascii=True, separators=(",", ":")).replace("<", r"\u003c")
    document = data
    if output.suffix.lower() == ".html":
        ui = Path(__file__).resolve().parents[1] / "ui"
        template = (ui / "nexus-history.html").read_text(encoding="utf-8")
        script = (ui / "nexus-history.js").read_text(encoding="utf-8")
        style = (ui / "nexus-history.css").read_text(encoding="utf-8")
        script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode("ascii")
        document = (
            template.replace(
                "script-src 'self'; style-src 'self'",
                f"script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'",
            )
            .replace('<script src="nexus-history.js"></script>', f"<script>{script}</script>")
            .replace('<link rel="stylesheet" href="nexus-history.css">', f"<style>{style}</style>")
            .replace('id="data"></script>', f'id="data">{data}</script>')
        )
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".modhunter-report-", delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        temporary.write_text(document, encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"rows": len(rows), "verdicts": facts["totals"], "catalogSHA256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True, help="JSON from modhunter-cli import")
    parser.add_argument(
        "--observations", type=Path, required=True, help="Read-only SQLite public Nexus snapshot"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Local .html report or desktop .json data file"
    )
    parser.add_argument(
        "--verify-rows", type=Path, help="Independently cross-check a complete recorded JSONL census"
    )
    parser.add_argument("--catalog-sha256", help="Expected original native catalog SHA-256")
    args = parser.parse_args()
    try:
        result = export(args.catalog, args.observations, args.output, args.verify_rows, args.catalog_sha256)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, NexusError) as error:
        parser.exit(2, f"Report failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
