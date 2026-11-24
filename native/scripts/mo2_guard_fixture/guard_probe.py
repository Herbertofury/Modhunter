"""Negative control for an explicitly marked disposable MO2 host. Never ship as a plugin."""
import hashlib
import json
import os
from pathlib import Path
import traceback
import uuid

import mobase
from PyQt6 import QtCore, QtWidgets


class Probe(mobase.IPlugin):
    def name(self):
        return "Modhunter protected install negative control"

    def author(self):
        return "Modhunter contributors"

    def description(self):
        return "Isolated native destination and activation test"

    def version(self):
        return mobase.VersionInfo(1, 0, 0)

    def settings(self):
        return []

    def init(self, organizer):
        setting = os.environ.get("MODHUNTER_DISPOSABLE_GUARD_PROBE")
        if not setting:
            return False
        self.root = Path(setting).resolve(strict=True)
        if Path(__file__).resolve().parent != self.root / "plugins":
            return False
        marker = json.loads((self.root / "guard-fixture.json").read_text("utf-8"))
        if (marker.get("purpose") != "isolated native protected install negative control"
                or Path(QtCore.QCoreApplication.applicationDirPath()).resolve() != self.root):
            return False
        token = uuid.UUID(marker["runId"]).hex
        self.suggested = "MODHUNTER_QA__suggested__" + token
        self.alternate = "MODHUNTER_QA__alternate__" + token
        self.existing = "MODHUNTER_QA__existing__" + token
        self.archive = self.root / "downloads" / (self.suggested + ".zip")
        self.expected_digest = marker["archiveSha256"]
        self.org = organizer
        self.events = []
        self.returned = ""
        self.merged = ""
        self.phase = "new"
        self.named = False
        self.finished = False
        return (organizer.modList().onModInstalled(self.installed)
                and organizer.onUserInterfaceInitialized(self.ready))

    def ready(self, window):
        self.window = window
        self.timer = QtCore.QTimer(window)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.dialog)
        self.timer.start()
        QtCore.QTimer.singleShot(400, self.install)
        QtCore.QTimer.singleShot(30000, lambda: self.finish(False, "fixture timed out"))

    def install(self):
        try:
            if self.org.profileName() != "Guard Fixture":
                raise ValueError("active profile is outside the isolated guard fixture")
            for api, subdir in ((self.org.modsPath, "mods"), (self.org.downloadsPath, "downloads"),
                                (self.org.profilePath, "profiles/Guard Fixture")):
                if Path(api()).resolve(strict=True) != self.root / subdir:
                    raise ValueError("native path differs from isolated guard fixture")
            for name in (self.suggested, self.alternate):
                if (self.root / "mods" / name).exists() or name in self.org.modList().allMods():
                    raise ValueError("fixture target already exists")
            if hashlib.sha256(self.archive.read_bytes()).hexdigest() != self.expected_digest:
                raise ValueError("fixture archive digest changed")
            result = self.org.installMod(str(self.archive), self.suggested)
            self.returned = str(result.name()) if result else ""
            if self.returned != self.alternate:
                raise ValueError("unexpected native return value")
            self.check()
        except Exception:
            self.finish(False, traceback.format_exc())

    def dialog(self):
        dialog = QtWidgets.QApplication.activeModalWidget()
        if dialog is None:
            return
        if dialog.objectName() == "QueryOverwriteDialog" and self.phase == "merge" and self.named:
            button = dialog.findChild(QtWidgets.QPushButton, "mergeBtn")
            backup = dialog.findChild(QtWidgets.QCheckBox, "backupBox")
            if button is not None and button.isEnabled():
                if backup is not None:
                    backup.setChecked(False)
                self.events.append("native overwrite dialog offered merge on existing fixture row")
                self.phase = "merging"
                button.click()
            return
        if dialog.objectName() != "SimpleInstallDialog" or self.named:
            return
        combo = dialog.findChild(QtWidgets.QComboBox, "nameCombo")
        ok = dialog.findChild(QtWidgets.QPushButton, "okBtn")
        if combo is None or ok is None or not combo.isEditable() or combo.currentText() != self.suggested:
            return
        target = self.alternate if self.phase == "new" else self.existing
        combo.setEditText(target)
        if combo.currentText() == target and ok.isEnabled():
            self.named = True
            self.events.append("native dialog accepted a different new name" if self.phase == "new"
                               else "native dialog accepted an existing fixture row")
            ok.click()

    def installed(self, mod):
        if self.finished or mod.name() != self.alternate:
            return
        try:
            if Path(mod.absolutePath()).resolve(strict=True) != self.root / "mods" / self.alternate:
                raise ValueError("native callback target differs from exact fixture path")
            if self.active():
                raise ValueError("expected stock new row to be disabled before observer")
            self.events.append("first native installed callback observed disabled")
            self.org.modList().setActive(self.alternate, True)
            if not self.active():
                raise ValueError("native enable request was rejected")
            self.events.append("native installed callback enabled the new row")
            self.check()
        except Exception:
            self.finish(False, traceback.format_exc())

    def active(self):
        return bool(int(self.org.modList().state(self.alternate)) & int(mobase.ModState.ACTIVE))

    def check(self):
        if self.phase == "new" and self.returned and len(self.events) == 3:
            passed = (self.active() and not (self.root / "mods" / self.suggested).exists()
                      and (self.root / "mods" / self.alternate / "smoke-fixture.txt").read_bytes() == b"guard fixture")
            if not passed:
                self.finish(False, "first native transaction state mismatch")
                return
            self.phase = "pending"
            QtCore.QTimer.singleShot(150, self.merge)

    def merge(self):
        try:
            target = self.root / "mods" / self.existing
            if (target.resolve(strict=True) != target or self.existing not in self.org.modList().allMods()
                    or (target / "smoke-fixture.txt").read_bytes() != b"before native merge"
                    or (target / "unrelated.txt").read_bytes() != b"keep fixture sibling"):
                raise ValueError("existing control target is not the marked dummy row")
            self.phase, self.named = "merge", False
            result = self.org.installMod(str(self.archive), self.suggested)
            self.merged = str(result.name()) if result else ""
            if (self.merged != self.existing or self.phase != "merging"
                    or (target / "smoke-fixture.txt").read_bytes() != b"guard fixture"
                    or (target / "unrelated.txt").read_bytes() != b"keep fixture sibling"):
                raise ValueError("existing fixture target merge outcome differed")
            self.events.append("native merge replaced the existing fixture member")
            self.finish(True, "")
        except Exception:
            self.finish(False, traceback.format_exc())

    def finish(self, ok, error):
        if self.finished:
            return
        self.finished = True
        self.timer.stop()
        (self.root / "guard-result.json").write_text(json.dumps({
            "ok": ok, "error": error, "events": self.events, "hostVersion": str(self.org.version()),
            "suggested": self.suggested, "returned": self.returned, "merged": self.merged,
            "scope": "Ordinary stock installation with a deliberate fixture-only observer"
        }, indent=2), "utf-8")
        # Avoid re-entering MO2's nested installer event loop.
        QtCore.QTimer.singleShot(500, self.window.close)


def createPlugin():
    return Probe()
