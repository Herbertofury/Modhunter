"""Genuine author-version evidence for a disposable native MO2 update."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from urllib.parse import urlencode

from .content_probe import require

PROJECT = "https://github.com/markekraus/ChairrowsSkyrimSEMod"
FILENAME = "MarkekrausChairrows.7z"
OLD_DIGEST = "318d6e4f2952ced680ac4f1a673eeae723172d34e8643c31a8b58fc5e94420b0"
NEW_DIGEST = "bf7922674ef241ffa491bb79a7e85c80733ebc63e5f297e6663102688e4fe46e"
OLD_FILES = {
    "MarkekrausChairrows.bsa": "3c4b6688000bf9df9c0cc79e84a1df2a334b873c636cacf4711f6dd59fbb0386",
    "MarkekrausChairrows.esp": "00a407ce944ceb7e4d7d28e73a2eeaa6074fe086e7f79642021766a07309678d",
}
NEW_FILES = {
    "MarkekrausChairrows.bsa": "4a4dbdc722bc586779c69d0de1e487c9c3dbc83a48b0b40ed299af0ae2ecde51",
    "MarkekrausChairrows.esp": "0af6310e77f6f819f198c1063fcefd738d4ffd4e55cd437cdb28b64a567b6e0d",
}
TARGET_CHOICES = ("MarkekrausChairrows.esp", "No (Default)")


def accept_chairrows(qt, dialog, intent):
    if dialog.objectName() != "FomodInstallerDialog" or intent["dialogs"]:
        return None
    button = dialog.findChild(qt.QPushButton, "nextBtn")
    combo = dialog.findChild(qt.QComboBox, "nameCombo")
    stack = dialog.findChild(qt.QStackedWidget, "stepsStack")
    if not (
        button
        and button.text().replace("&", "") == "Install"
        and combo
        and combo.isEditable()
        and combo.currentText() in {intent["modName"], "Chairrows (Chair Arrows)"}
        and stack
        and stack.currentWidget()
    ):
        return None
    page = stack.currentWidget()
    required = page.findChildren(qt.QCheckBox)
    options = {c.text(): c for c in page.findChildren(qt.QRadioButton)}
    main, no_sources = TARGET_CHOICES
    # MO2 renders a single SelectExactlyOne option as a locked, checked
    # checkbox. The two script-source options remain ordinary radio buttons.
    if (len(required) != 1 or required[0].text() != main
            or required[0].isEnabled() or not required[0].isChecked()
            or set(options) != {no_sources, "Yes (For Mod Developers)"}
            or not all(c.isEnabled() for c in options.values())):
        return None
    combo.setEditText(intent["modName"])
    if not options[no_sources].isChecked():
        options[no_sources].click()
    if (
        not button.isEnabled()
        or combo.currentText() != intent["modName"]
        or {k for k, v in options.items() if v.isChecked()} != {no_sources}
    ):
        return None
    intent["nameConfirmed"] = True
    intent["dialogs"].append(
        {
            "dialog": dialog.objectName(),
            "target": intent["modName"],
            "selected": list(TARGET_CHOICES),
        }
    )
    return button


def native_version(value):
    return tuple(int(v) for v in str(value).lstrip("v").split("."))


def content_hashes(target):
    return {
        p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(target.rglob("*"))
        if p.is_file()
    }


def poll(description, operation, predicate, seconds=30):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = operation()
        if predicate(last):
            return last
        time.sleep(0.12)
    raise AssertionError(f"{description} exceeded its deadline: {str(last)[:450]}")


def update_chairrows(api, root, profile, row, cli, arm, disarm, wait, context):
    name, item = row["mo2InternalName"], row["id"]
    target = root / "mods" / name
    prior = content_hashes(target)
    require(
        {p: h for p, h in prior.items() if p != "meta.ini"} == OLD_FILES,
        "Unexpected initial Chairrows payload",
    )
    require(
        native_version(row["installedVersion"])[:3] == (2, 0, 0),
        "Native installation did not report the old version",
    )
    progress = api("/api/progress")["progress"]
    progress.setdefault("rows", {}).setdefault(item, {})["url"] = PROJECT
    require(
        api("/api/progress", {**progress, "profileId": profile}).get("ok"),
        "Explicit upstream project was not retained",
    )

    def scan():
        started = api("/api/scan", {"mode": "sources", "profileId": profile})
        require(started.get("ok"), "Source scan rejected: " + str(started))
        poll(
            "Public release scan",
            lambda: api("/api/scan")["scan"],
            lambda s: s["state"] not in {"running", "queued", "starting"},
            45,
        )
        result = api(
            "/api/source-details?" + urlencode({"profileId": profile, "itemId": item})
        )
        require(result.get("ok"), "Missing source decision for the exact mod")
        return result["update"]

    found = scan()
    require(
        found.get("verdict") == "candidate"
        and native_version(found["latestVersion"])[:3] == (2, 0, 1),
        "Exact publisher release was not identified: " + str(found)[:400],
    )
    assets = [
        a
        for a in found["releaseAssets"]
        if a.get("name") == FILENAME and int(a.get("assetId", 0)) == 53563837
    ]
    require(
        len(assets) == 1,
        "The native updater did not uniquely identify the newer version",
    )
    asset = assets[0]
    require(
        asset["url"] == PROJECT + "/releases/download/v2.0.1/" + FILENAME,
        "The reviewed release path changed",
    )
    selection = {
        "kind": "source",
        "itemId": item,
        "assetKey": asset["assetKey"],
        "assetId": asset["assetId"],
        "assetProof": asset["assetProof"],
        "provider": "github",
        "installedVersion": row["installedVersion"],
        "targetVersion": found["latestVersion"],
        "profileId": profile,
        "reviewed": True,
        "riskAcknowledged": True,
    }
    queue = api(
        "/api/update-queue",
        {
            "profileId": profile,
            "confirm": "QUEUE_REVIEWED_DOWNLOADS",
            "entries": [selection],
        },
    )
    require(
        queue.get("ok") and len(queue["queued"]) == 1,
        "Native reviewed download failed: " + str(queue)[:450],
    )
    did = queue["queued"][0]["downloadId"]

    def ticket():
        return api("/api/downloads")["downloads"].get(str(did), {})

    downloaded = poll(
        "Native MO2 download",
        ticket,
        lambda d: d.get("status") in {"complete", "failed", "removed"},
        60,
    )
    require(
        downloaded.get("status") == "complete",
        "Native release failed to download: " + str(downloaded)[:400],
    )
    archive = root / "downloads" / FILENAME
    require(
        archive.resolve(strict=True).parent == (root / "downloads").resolve(strict=True)
        and archive.stat().st_size == 2734932
        and hashlib.sha256(archive.read_bytes()).hexdigest() == NEW_DIGEST,
        "Native MO2 download did not match the exact publisher bytes",
    )
    stage = root / "public-acquisition" / "chairrows-native-update-review"
    request = {
        "action": "inspect",
        "archive": str(archive),
        "stage": str(stage),
        "expectedSha256": NEW_DIGEST,
        "expectedBytes": 2734932,
        "selected": [],
    }
    manifest = json.loads(
        subprocess.check_output(
            [str(cli), "archive-review", "--request", "-"],
            input=json.dumps(request),
            text=True,
            timeout=30,
        )
    )
    entries = {e["path"]: e["sha256"] for e in manifest["entries"]}
    require(
        manifest["requiresInstaller"]
        and len(entries) == 7
        and all(entries.get(p) == h for p, h in NEW_FILES.items()),
        "The publication's reviewed FOMOD manifest changed",
    )
    arm(
        {
            "downloadId": "mo2:" + str(did),
            "profileId": profile,
            "modName": name,
            "modRoot": str(target),
            "deadline": time.monotonic() + 50,
            "publicSha256": NEW_DIGEST,
            "publicUpdate": True,
        }
    )
    try:
        accepted = api("/api/install", {"confirm": "INSTALL", "downloadId": did})
        require(
            accepted.get("ok"), "Native MO2 update was not admitted: " + str(accepted)
        )
        job = poll(
            "Native version update",
            lambda: next(
                (
                    j
                    for j in api("/api/content-jobs")["jobs"]
                    if j.get("downloadId") == "mo2:" + str(did)
                ),
                {},
            ),
            lambda j: (
                j.get("state")
                in {"complete", "unknown", "failed", "interrupted", "cancelled"}
            ),
            50,
        )
    finally:
        dialogs = disarm()
    require(
        job.get("ok")
        and job.get("verifiedUpdate")
        and job.get("installedName") == name,
        "Native version update not verified: " + str(job)[:500],
    )
    require(
        len(dialogs) == 2
        and dialogs[0]["selected"] == list(TARGET_CHOICES)
        and dialogs[1]["dialog"] == "QueryOverwriteDialog",
        "Unexpected native update dialogs: " + str(dialogs),
    )
    now = content_hashes(target)
    require(
        {p: h for p, h in now.items() if p != "meta.ini"} == NEW_FILES,
        "Updated assets differ from the reviewed FOMOD choices",
    )
    live = next(r for r in api("/api/snapshot")["catalog"]["items"] if r["id"] == item)
    require(
        not live["enabled"]
        and native_version(live["installedVersion"])[:3] == (2, 0, 1),
        "Updated native state or version changed",
    )
    after = scan()
    require(
        after.get("verdict") == "current",
        "Newer release not reconciled as current: " + str(after)[:500],
    )
    restored = wait(
        api(
            "/api/content-recovery",
            {
                "reviewId": f"review-{int(time.time() * 1000)}-892",
                "contextId": context({"itemId": item}),
                "action": "restore",
                "backupId": job["backupId"],
                "confirm": "RESTORE",
            },
        )
    )
    require(
        restored.get("filesRestored") and content_hashes(target) == prior,
        "Native undo did not restore the complete prior tree",
    )
    return {
        "githubAssetId": 53563837,
        "sha256": NEW_DIGEST,
        "manifestDigest": manifest["manifestDigest"],
        "dialogs": dialogs,
        "beforeVersion": row["installedVersion"],
        "afterVersion": live["installedVersion"],
        "afterVerdict": after["verdict"],
        "afterFiles": now,
        "priorTreeRestored": True,
    }
