"""Deterministic raw Nexus files and author-declared replacement fixtures."""
import time
from mo2_update_arsenal.nexus_files import FileLineage, normalize_file_payload
from mo2_update_arsenal.scanner import summary_from_metadata

GAME = "skyrimspecialedition"


def files_payload(version="2.0", *, mod_id=1, installed="1.0"):
    return {"mod_id": mod_id, "domain_name": GAME,
            "files": [
                {"file_id": 100 + mod_id, "name": "Installed file", "file_name": f"installed-{mod_id}.7z",
                 "version": installed, "category_id": 4},
                {"file_id": 200 + mod_id, "name": "Replacement file", "file_name": f"replacement-{mod_id}.7z",
                 "version": version, "category_id": 1}],
            "file_updates": [{"old_file_id": 100 + mod_id, "new_file_id": 200 + mod_id,
                              "old_file_name": f"installed-{mod_id}.7z", "new_file_name": f"replacement-{mod_id}.7z"}],
            "meta": {"version": version, "mod_id": mod_id, "domain_name": GAME}}


def file_item(mod_id=1, *, current=False, version=None, ident=None):
    return {"id": ident or str(mod_id), "nexusId": mod_id,
            "installedVersion": version or ("2.0" if current else "1.0"),
            "installedFiles": [{"modId": mod_id, "fileId": (200 if current else 100) + mod_id}]}


def cached_files(mod_id=1, version="2.0"):
    return normalize_file_payload(files_payload(version, mod_id=mod_id), GAME, mod_id)


def review_payload(mod_id, files, edges=()):
    """Complete provider fixture with explicitly supplied replacement edges."""
    return {"ok": True, **normalize_file_payload({"files": files,
            "file_updates": [{"old_file_id": old, "new_file_id": new} for old, new in edges]},
            GAME, mod_id), "checkedAt": time.time()}


def with_lineage(row, payload, installed_file_id):
    row["installedFiles"] = [{"modId": row["nexusId"], "fileId": installed_file_id}]
    row["update"] = summary_from_metadata(row, {"version": "2.0"}, payload["checkedAt"],
                                           FileLineage(payload, GAME, row["nexusId"]))
    return row
