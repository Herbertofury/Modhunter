"""Exercise shipped content recovery through real MO2 Qt and loopback HTTP.

Only the explicitly marked disposable smoke instance may be mutated. Recovery
uses recognized fixture bytes; the separate native-install probe opens MO2's
installer for an owned fixture ZIP. Neither downloads content or touches user mods.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import zipfile


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


def exercise_native_install(api, instance_root, profile_id, fixture_rows, arm, disarm,
                            *, cli_path, timeout=50, clock=time.monotonic, pause=time.sleep):
    """Install one owned ZIP using production MO2 APIs and exact Qt dialogs."""
    root = Path(instance_root).resolve(strict=True)
    marker = json.loads((root / "modhunter-smoke-fixture.json").read_text("utf-8-sig"))
    require(marker.get("purpose") == "isolated native MO2 CI fixture" and marker.get("runId"),
            "Native install requires the marked disposable MO2 instance")
    require(0 < timeout <= 60, "Native install probe needs a bounded deadline")
    target = root / "mods" / "Smoke Disabled"
    require(target.resolve(strict=True) == target and not target.is_symlink(), "Native install target must be the exact disposable mod")
    item = fixture_rows["Smoke Disabled"]["id"]
    started = clock()
    deadline = started + timeout
    run = int(time.time() * 1000)
    review_id = f"review-{run}-701"
    download_id = "download-native-install-smoke"

    def tree():
        result = {}
        for path in sorted(target.rglob("*")):
            require(not path.is_symlink(), "Fixture target unexpectedly contains a link")
            if path.is_file():
                require(len(result) < 1000 and path.stat().st_size < 4 * 1024 * 1024, "Native install fixture exceeded its complete hash bound")
                result[str(path.relative_to(target)).replace("\\", "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    before = tree()
    original = (target / "smoke-fixture.txt").read_bytes()
    require(b"Harmless fixture content" in original, "Native installer target has unrecognized fixture bytes")
    replacement = original + b"Installed by the real native MO2 installer.\n"
    acquisitions = root / "watchtower-acquisitions"
    acquisitions.mkdir(exist_ok=False)
    folder = acquisitions / download_id
    folder.mkdir()
    archive = folder / "archive.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("smoke-fixture.txt", replacement)
        output.writestr("textures/modhunter-smoke.txt", "Inert CI fixture texture-directory marker; never loaded by a game.\n")
        output.writestr("ModhunterSmoke.esp", (target / "ModhunterSmoke.esp").read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    size = archive.stat().st_size
    stage = root / "native-install-archive-review"
    inspection = subprocess.run([str(Path(cli_path).resolve(strict=True)), "archive-review", "--request", "-"],
        input=json.dumps({"action": "inspect", "archive": str(archive), "stage": str(stage),
                          "expectedSha256": digest, "expectedBytes": size, "selected": []}),
        capture_output=True, text=True, encoding="utf-8", errors="strict", check=False,
        timeout=min(15, max(0.1, deadline - clock())), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    require(inspection.returncode == 0 and len(inspection.stdout) < 256 * 1024,
            "The bundled Rust CLI and adjacent pinned archive tools did not inspect the owned ZIP: " + inspection.stderr[-1000:])
    inspected = json.loads(inspection.stdout)
    require(inspected.get("ok") is True and inspected.get("layout") == "gameData"
            and inspected.get("requiresInstaller") is False and len(inspected.get("entries", [])) == 3,
            "The bundled archive helper did not return the complete expected fixture manifest")
    receipt = {"id": download_id, "filename": "Smoke Disabled.zip", "state": "ready", "extension": "zip",
               "bytes": size, "received": size, "total": size, "sha256": digest, "createdAt": run, "updatedAt": run,
               "message": "Owned disposable native installer fixture",
               "binding": {"watchId": "watch-native-install-smoke", "watchGeneration": 1,
                           "sessionId": "session-native-install-smoke", "sessionGeneration": 1,
                           "sourceOrigin": "https://modhunter-fixture.invalid", "contentIntent": "update",
                           "association": {"profileId": profile_id, "itemId": item, "name": "Smoke Disabled", "sourceUrl": "https://modhunter-fixture.invalid/archive"}}}
    (acquisitions / "receipts.json").write_text(json.dumps({"schema": 1, "revision": 1, "jobs": [receipt]}), encoding="utf-8")

    def call(path, data=None, *, native=False):
        remaining = deadline - clock()
        require(remaining > 0, "Native fixture installation exceeded its deadline")
        return api(path, data, timeout=min(5, remaining), native=native)

    def context():
        value = call("/api/content-context?" + urllib.parse.urlencode({"itemId": item}))
        require(value.get("ok") and value.get("profileId") == profile_id and value.get("target", {}).get("modName") == "Smoke Disabled",
                "Native installer fixture target changed before capture")
        return value["contextId"]

    def wait(accepted):
        require(accepted.get("ok") is True and accepted.get("jobId"), "Native install request was not accepted: " + str(accepted))
        while True:
            result = call("/api/content-jobs")
            rows = [j for j in result.get("jobs", []) if j.get("jobId") == accepted["jobId"]]
            require(len(rows) == 1, "Native installer lost its exact operation identity")
            job = rows[0]
            if job.get("state") in {"complete", "failed", "cancelled", "unknown", "interrupted"}:
                require(job.get("ok") is True and job.get("state") == "complete", "Native fixture installer did not complete: " + str(job))
                return job
            pause(min(0.05, max(0, deadline - clock())))

    request = {"reviewId": review_id, "downloadId": download_id, "contextId": context(), "confirm": "INSTALL",
               "resolvedArchive": {"archivePath": str(archive), "acquisitionRoot": str(acquisitions), "receipt": receipt,
                                   "review": {"archiveIdentity": {"sha256": digest, "bytes": size},
                                              "stage": str(stage), "incomingRoot": inspected["incomingRoot"],
                                              "manifestDigest": inspected["manifestDigest"], "requiresInstaller": False}}}
    arm({"reviewId": review_id, "downloadId": download_id, "profileId": profile_id, "modName": "Smoke Disabled", "modRoot": str(target), "deadline": deadline})
    try:
        installed = wait(call("/api/content-install", request, native=True))
    finally:
        dialogs = disarm()
    require(installed.get("nativeStarted") is True and installed.get("installedName") == "Smoke Disabled"
            and installed.get("backup", {}).get("undoAvailable") is True,
            "Native installer did not acknowledge its exact target and actual-byte backup")
    require((target / "smoke-fixture.txt").read_bytes() == replacement
            and (target / "textures/modhunter-smoke.txt").is_file(),
            "The native installer did not deliver the owned archive bytes")
    require([d.get("dialog") for d in dialogs] == ["SimpleInstallDialog", "QueryOverwriteDialog"]
            and [d.get("action") for d in dialogs] == ["accept exact fixture name", "merge exact fixture target"],
            "Native installer dialog acceptance did not follow the exact reviewed fixture sequence")
    restored = wait(call("/api/content-recovery", {"reviewId": f"review-{run}-702", "contextId": context(),
                    "action": "restore", "backupId": installed["backupId"], "confirm": "RESTORE"}))
    require(restored.get("filesRestored") is True and restored.get("refreshRequired") is False and tree() == before,
            "Native installation undo did not restore the complete original mod tree")
    return {"schemaVersion": 1, "profileId": profile_id, "state": "complete", "realQt": True,
            "nativeInstallerOpened": True, "nativeInstallVerified": True, "actualModBytesRestored": True,
            "bundledArchiveReviewVerified": True, "archiveManifestDigest": inspected["manifestDigest"],
            "nativeRefreshAcknowledged": True, "exactTarget": "Smoke Disabled", "archiveSha256": digest,
            "archiveBytes": size, "backupId": installed["backupId"], "dialogs": dialogs,
            "originalFiles": len(before), "elapsedMs": round((clock() - started) * 1000, 2),
            "scope": "Real MO2 installMod and exact Qt name/merge dialogs against one marked disposable fixture mod; complete tree undo verified, no user mods"}
