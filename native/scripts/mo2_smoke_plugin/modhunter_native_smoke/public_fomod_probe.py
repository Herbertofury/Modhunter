"""Verify an author-published FOMOD using a real prior Watchtower receipt in-place."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.parse
from pathlib import Path

from . import public_version_probe as versions
from .content_probe import new_fixture_name, require

FILENAME = "skyrim-tiered-casters-1.0.0.zip"
DIGEST = "a9e7bfdc1c849d7a25d20d51b476046b41667b27bac5bfc2939c8de8f09632c7"
CHOICES = (
    ("Standard (recommended)", "Vanilla+ (subtle)", "Ruthless"),
    ("Restoration Self-Heal", "Alteration Armour Spells", "Illusion Pressure"),
)
SELECTED = {
    "Presets/TieredCasters_Standard_DISTR.ini": "63cce5fee04cf4c6038ab90d752f72b216120138de8dbbc4ed3b7b8a904ee95b",
    "Modules/TieredCasters_Healers_DISTR.ini": "9bc2a3ee28e1668cea143eedb0f57e67c7bcc2a9035328e7c77cf9c0bf2c71f6",
}


def accept_tiered_casters(QtWidgets, dialog, intent):
    """Return the next button only after checking the exact dialog, page and package."""
    page = len(intent["dialogs"])
    if page >= len(CHOICES) or dialog.objectName() != "FomodInstallerDialog":
        return None
    stack = dialog.findChild(QtWidgets.QStackedWidget, "stepsStack")
    combo = dialog.findChild(QtWidgets.QComboBox, "nameCombo")
    button = dialog.findChild(QtWidgets.QPushButton, "nextBtn")
    if (
        stack is None
        or stack.currentWidget() is None
        or combo is None
        or not combo.isEditable()
        or button is None
        or button.text().replace("&", "") != ("Next", "Install")[page]
    ):
        return None
    if combo.currentText() not in {intent["modName"], "Tiered Casters"}:
        return None
    cls = (QtWidgets.QRadioButton, QtWidgets.QCheckBox)[page]
    options = {child.text(): child for child in stack.currentWidget().findChildren(cls)}
    if set(options) != set(CHOICES[page]) or not all(
        c.isEnabled() for c in options.values()
    ):
        return None
    combo.setEditText(intent["modName"])
    chosen = CHOICES[page][0]
    options[chosen].setChecked(True)
    if page == 1:
        for label in CHOICES[page][1:]:
            options[label].setChecked(False)
    if (
        combo.currentText() != intent["modName"]
        or not button.isEnabled()
        or [k for k, v in options.items() if v.isChecked()] != [chosen]
    ):
        return None
    intent["dialogs"].append(
        {
            "dialog": "FomodInstallerDialog",
            "target": intent["modName"],
            "selected": chosen,
        }
    )
    intent["nameConfirmed"] = True
    return button


def run_public_fomod(api, root, profile, before, cli, arm, disarm, *, history=False):
    root = Path(root).resolve(strict=True)
    if history:
        filename, digest, size = versions.FILENAME, versions.OLD_DIGEST, 2734912
        selected, count = versions.OLD_FILES, 7
        source = versions.PROJECT + "/releases/expanded_assets/v2.0.0"
    else:
        filename, digest, size = FILENAME, DIGEST, 16202
        selected, count = SELECTED, 10
        source = "https://github.com/3pvxiozkua/skyrim-tiered-casters/releases/expanded_assets/v1.0.0"
    name = new_fixture_name(
        json.loads((root / "modhunter-smoke-fixture.json").read_text("utf-8-sig"))
    )
    output = root / "public-acquisition"
    proof = json.loads((output / "managed.json").read_text("utf-8"))
    require(
        proof.get("ok") is True and proof.get("nativeWebView2") is True,
        "Public manager did not complete",
    )
    acquired = Path(proof["acquisitionRoot"]).resolve(strict=True)
    require(
        acquired.name == "watchtower-acquisitions"
        and acquired.parent.parent.parent == output
        and acquired.parent.name == "data"
        and acquired.parent.parent.name.startswith("watchtower-fixture-"),
        "Refusing public receipt outside this exact native fixture",
    )
    matches = [
        check["receipt"]
        for check in proof["checks"]
        if "receipt" in check
        and check["filename"] == filename
        and check["source"] == source
    ]
    require(len(matches) == 1, "Required exact approved release receipt is missing")
    receipt = matches[0]
    require(
        receipt["sha256"] == digest
        and receipt["bytes"] == size
        and receipt["state"] == "ready"
        and receipt["binding"]["contentIntent"] == "newContent"
        and receipt["binding"]["sourceOrigin"] == "https://github.com",
        "Unexpected public release identity",
    )
    original = acquired / receipt["id"] / ("archive.7z" if history else "archive.zip")
    target = root / "mods" / name
    require(
        not target.exists() and not target.is_symlink() and name not in before,
        "Public target already exists",
    )
    require(
        original.resolve(strict=True).is_relative_to(acquired)
        and original.stat().st_size == size
        and hashlib.sha256(original.read_bytes()).hexdigest() == digest,
        "Retained original no longer matches the publisher's content",
    )
    stage = output / ("native-chairrows-old" if history else "native-review")
    request = {
        "action": "inspect",
        "archive": str(original),
        "stage": str(stage),
        "expectedSha256": digest,
        "expectedBytes": size,
        "selected": [],
    }
    completed = subprocess.run(
        [str(cli), "archive-review", "--request", "-"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    manifest = json.loads(completed.stdout)
    require(
        manifest.get("ok")
        and manifest.get("requiresInstaller")
        and len(manifest.get("entries", [])) == count
        and all(
            {e["path"]: e["sha256"] for e in manifest["entries"]}.get(path) == digest
            for path, digest in selected.items()
        ),
        "Author's FOMOD or one of the selected variant files changed",
    )
    run = int(time.time() * 1000)

    def context(params):
        c = api("/api/content-context?" + urllib.parse.urlencode(params))
        require(
            c.get("ok")
            and c.get("profileId") == profile
            and c["target"]["modName"] == name,
            "Wrong public FOMOD target",
        )
        return c["contextId"]

    def wait(accepted):
        deadline = time.monotonic() + 55
        require(
            accepted.get("ok") and accepted.get("jobId"),
            "Native public operation rejected: " + str(accepted),
        )
        while time.monotonic() < deadline:
            rows = [
                j
                for j in api("/api/content-jobs")["jobs"]
                if j["jobId"] == accepted["jobId"]
            ]
            require(len(rows) == 1, "Public job identity changed")
            if rows[0]["state"] in {
                "complete",
                "failed",
                "cancelled",
                "unknown",
                "interrupted",
            }:
                require(
                    rows[0].get("ok") and rows[0]["state"] == "complete",
                    "Native FOMOD operation failed: " + str(rows[0]),
                )
                return rows[0]
            time.sleep(0.05)
        raise AssertionError("Public FOMOD operation exceeded its deadline")

    review_id = f"review-{run}-871"
    arm(
        {
            "reviewId": review_id,
            "downloadId": receipt["id"],
            "profileId": profile,
            "modName": name,
            "modRoot": str(target),
            "deadline": time.monotonic() + 55,
            "publicSha256": digest,
        }
    )
    try:
        job = wait(
            api(
                "/api/content-install",
                {
                    "reviewId": review_id,
                    "downloadId": receipt["id"],
                    "contextId": context({"targetName": name}),
                    "confirm": "INSTALL",
                    "targetName": name,
                    "resolvedArchive": {
                        "acquisitionRoot": str(acquired),
                        "archivePath": str(original),
                        "receipt": receipt,
                        "review": {
                            "archiveIdentity": {"sha256": digest, "bytes": size},
                            "stage": str(stage),
                            "incomingRoot": manifest["incomingRoot"],
                            "manifestDigest": manifest["manifestDigest"],
                            "requiresInstaller": True,
                        },
                    },
                },
                native=True,
            )
        )
    finally:
        dialogs = disarm()
    require(
        job.get("nativeStarted")
        and job.get("installedName") == name
        and job.get("backup", {}).get("undoAvailable"),
        "Public FOMOD native transaction missing confirmed target or backup",
    )
    require(
        [d["selected"] for d in dialogs]
        == ([list(versions.TARGET_CHOICES)] if history else [c[0] for c in CHOICES]),
        "Wrong FOMOD choices",
    )
    files = {
        p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in target.rglob("*")
        if p.is_file() and p != target / "meta.ini"
    }
    require(
        files == selected and (target / "meta.ini").is_file(),
        "Installed original-choice paths/hashes differ from the author's XML and archive: "
        + str(files),
    )
    rows = {r["mo2InternalName"]: r for r in api("/api/snapshot")["catalog"]["items"]}
    require(
        rows[name]["enabled"] is False
        and all(rows[n]["enabled"] == v["enabled"] for n, v in before.items()),
        "Public FOMOD changed activation",
    )
    update = (
        versions.update_chairrows(
            api, root, profile, rows[name], cli, arm, disarm, wait, context
        )
        if history
        else None
    )
    restored = wait(
        api(
            "/api/content-recovery",
            {
                "reviewId": f"review-{run}-872",
                "contextId": context({"itemId": rows[name]["id"]}),
                "action": "restoreNew",
                "backupId": job["backupId"],
                "confirm": "RESTORE",
            },
        )
    )
    require(
        not target.exists() and restored.get("refreshRequired") is False,
        "Public FOMOD undo failed",
    )
    return {
        "archive": filename,
        "sha256": digest,
        "archiveManifest": manifest["manifestDigest"],
        "managedReceipt": receipt["id"],
        "source": source,
        "target": name,
        "dialogs": dialogs,
        "installedFiles": files,
        "nativeVersion": rows[name].get("installedVersion"),
        "disabledAtInventory": True,
        "undoVerified": True,
        "game": "isolated native MO2 BasicGame fixture",
        "versionUpdate": update,
    }
