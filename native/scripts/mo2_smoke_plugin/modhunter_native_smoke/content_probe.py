"""Exercise shipped content recovery through real MO2 Qt and loopback HTTP.

Only the explicitly marked disposable smoke instance may be mutated. This
probe never opens an installer, downloads an archive, or touches a user mod.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import urllib.parse


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def exercise_content_recovery(api, instance_root, profile_id, fixture_rows, *, timeout=55,
                              clock=time.monotonic, pause=time.sleep):
    root = Path(instance_root).resolve(strict=True)
    marker_path = root / "modhunter-smoke-fixture.json"
    require(not marker_path.is_symlink() and marker_path.is_file() and marker_path.stat().st_size < 16384,
            "Content recovery requires an owned disposable MO2 smoke instance")
    marker = json.loads(marker_path.read_text("utf-8-sig"))
    require(marker.get("purpose") == "isolated native MO2 CI fixture" and bool(marker.get("runId")),
            "Refusing content recovery outside the marked isolated fixture")
    require(0 < timeout <= 60, "Content smoke deadline must be bounded")
    started = clock()
    deadline = started + timeout
    sequence = 0
    run = int(time.time() * 1000)
    operations = []

    def call(path, data=None):
        remaining = deadline - clock()
        require(remaining > 0, "Native content recovery exceeded its smoke deadline")
        return api(path, data, timeout=min(5, remaining))

    def context(item="", backup=""):
        value = call("/api/content-context?" + urllib.parse.urlencode({"itemId": item, "backupId": backup}))
        require(value.get("ok") is True and value.get("profileId") == profile_id and value.get("game") == "modhuntersmoke",
                "Native content capture rejected its exact fixture profile: " + str(value.get("error")))
        require("nativeContext" not in value, "Regular HTTP client received private native paths")
        return value["contextId"]

    def wait(accepted, *, expect_ok=True):
        require(accepted.get("ok") is True and bool(accepted.get("jobId")), "Content job was not accepted: " + str(accepted))
        while True:
            response = call("/api/content-jobs")
            require(response.get("ok") is True, "Content job polling failed")
            jobs = [row for row in response.get("jobs", []) if row.get("jobId") == accepted["jobId"]]
            require(len(jobs) == 1, "Exact content operation disappeared or was duplicated")
            job = jobs[0]
            require(job.get("profileId") == profile_id, "Content job changed its native profile")
            if job.get("state") in {"complete", "failed", "cancelled", "unknown", "interrupted"}:
                require(job.get("ok") is expect_ok, "Unexpected native content outcome: " + str(job))
                operations.append({"jobId": job["jobId"], "action": job["action"], "state": job["state"], "ok": job["ok"]})
                return job
            pause(min(0.05, max(0, deadline - clock())))

    def recovery(action, item, *, backup=None, expect_ok=True, **values):
        nonlocal sequence
        sequence += 1
        payload = {"action": action, "reviewId": f"review-{run}-{sequence}", "contextId": context(item), **values}
        if backup:
            payload["backupId"] = backup
        return wait(call("/api/content-recovery", payload), expect_ok=expect_ok)

    originals, updated, backups = {}, {}, []
    for name in ("Smoke Enabled", "Smoke Disabled"):
        row = fixture_rows.get(name) or {}
        require(bool(row.get("id")), "Fixture mod identity is missing")
        path = root / "mods" / name / "smoke-fixture.txt"
        require(path.resolve(strict=True) == path and not path.is_symlink() and path.stat().st_size < 65536,
                "Fixture content path is not its owned regular file")
        original = path.read_bytes()
        require(b"Harmless fixture content" in original, "Unrecognized fixture bytes; refusing mutation")
        originals[name] = original
        prepared = recovery("snapshot", row["id"], confirm="BACKUP")
        require(prepared.get("includesModFiles") is True and prepared.get("backup", {}).get("state") == "prepared",
                "Actual native fixture bytes were not prepared in the Rust vault")
        ident = prepared["backupId"]
        replacement = original + ("Modhunter content recovery probe " + marker["runId"] + "\n").encode()
        path.write_bytes(replacement)
        updated[name] = replacement
        sealed = recovery("seal", row["id"], backup=ident, confirm="SEAL")
        require(sealed.get("backup", {}).get("undoAvailable") is True, "Native vault did not seal the exact fixture target")
        backups.append({"itemId": row["id"], "backupId": ident})

    # Another mod's receipt must not cross the exact native target boundary.
    wrong = recovery("previewRestore", fixture_rows["Smoke Enabled"]["id"], backup=backups[1]["backupId"], expect_ok=False)
    require(wrong["state"] == "failed", "Cross-target backup did not fail closed")

    # A later local edit is not covered by the sealed installation receipt.
    path = root / "mods" / "Smoke Disabled" / "smoke-fixture.txt"
    later = updated["Smoke Disabled"] + b"Unrelated later fixture edit\n"
    path.write_bytes(later)
    blocked = recovery("restore", fixture_rows["Smoke Disabled"]["id"], backup=backups[1]["backupId"], confirm="RESTORE", expect_ok=False)
    require(blocked["state"] == "failed" and path.read_bytes() == later, "Undo erased an unrelated later edit")
    path.write_bytes(updated["Smoke Disabled"])

    sequence += 1
    batch_request = {"reviewId": f"review-{run}-{sequence}", "contextId": context(), "backups": backups, "confirm": "RESTORE"}
    accepted = call("/api/content-batch-restore", batch_request)
    batch = wait(accepted)
    require(batch.get("filesRestored") is True and batch.get("refreshRequired") is False
            and len(batch.get("completed", [])) == 2 and not batch.get("remaining") and not batch.get("failed"),
            "Batch restore did not acknowledge every file transaction and real MO2 refresh")
    repeated = call("/api/content-batch-restore", batch_request)
    require(repeated.get("alreadyAccepted") is True and repeated.get("jobId") == accepted["jobId"],
            "A repeated acknowledgement replayed a content mutation")
    hashes = {}
    for name, original in originals.items():
        final = (root / "mods" / name / "smoke-fixture.txt").read_bytes()
        require(final == original, "Native restore did not return exact original fixture bytes")
        hashes[name] = hashlib.sha256(final).hexdigest()
    return {"schemaVersion": 1, "profileId": profile_id, "state": "complete", "realQt": True,
            "actualModBytesRestored": True, "nativeRefreshAcknowledged": True, "batchTargets": 2,
            "wrongTargetRejected": True, "laterEditPreserved": True, "lostAckDeduplicated": True,
            "nativeInstallerOpened": False, "fixtureHashes": hashes, "operations": operations,
            "elapsedMs": round((clock() - started) * 1000, 2),
            "scope": "Shipped Rust helper, actual MO2 Qt capture/refresh and disposable fixture mod bytes; no user files or live-game installation"}
