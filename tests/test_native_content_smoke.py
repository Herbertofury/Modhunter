"""The Windows probe can accept only its exact, currently owned fixture dialogs."""
import ast
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class Combo:
    def __init__(self, name): self.name = name
    def currentText(self): return self.name


class Button:
    def __init__(self): self.clicked = False
    def isEnabled(self): return True
    def click(self): self.clicked = True


class Checkbox:
    def __init__(self): self.checked = True
    def setChecked(self, value): self.checked = value


class Dialog:
    def __init__(self, name, controls): self.name, self.controls = name, controls
    def objectName(self): return self.name
    def findChild(self, kind, name): return self.controls.get(name)


class Progress: pass


class FixtureEntry:
    def __init__(self, name): self._name = name
    def name(self): return self._name


class FixtureTree(FixtureEntry):
    def __init__(self, name, entries):
        super().__init__(name)
        self.entries = entries
    def __iter__(self): return iter(self.entries)


class FixtureDataCheckerRegistrationTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "native/scripts/mo2_smoke_plugin/game_modhunter_smoke.py"
        parsed = ast.parse(path.read_text())
        module = ast.fix_missing_locations(ast.Module(
            body=[node for node in parsed.body if isinstance(node, ast.ClassDef)], type_ignores=[]))
        class Feature:
            INVALID, VALID = 0, 1
        class Game:
            def init(self, organizer):
                self.organizer = organizer
                return organizer.accept_init
            def _register_feature(self, feature):
                self.organizer.features.append(feature)
                return self.organizer.accept_feature
        scope = {"mobase": SimpleNamespace(ModDataChecker=Feature, IFileTree=FixtureTree), "BasicGame": Game}
        exec(compile(module, str(path), "exec"), scope)
        self.checker = scope["ModhunterSmokeDataChecker"]()
        self.game = scope["ModhunterSmokeGame"]()
        self.entries = [FixtureEntry("smoke-fixture.txt"), FixtureEntry("ModhunterSmoke.esp"),
                        FixtureTree("textures", [FixtureEntry("modhunter-smoke.txt")])]

    def test_registers_the_game_feature_required_by_real_simple_installer(self):
        organizer = SimpleNamespace(accept_init=True, accept_feature=True, features=[])
        self.assertTrue(self.game.init(organizer))
        self.assertEqual(len(organizer.features), 1)
        self.assertEqual(organizer.features[0].dataLooksValid(FixtureTree("", self.entries)), 1)
        organizer.accept_feature = False
        self.assertFalse(self.game.init(organizer))

    def test_content_definition_rejects_unknown_missing_or_directory_aliased_files(self):
        for entries in (self.entries[:-1], self.entries + [FixtureEntry("other.esp")],
                        [FixtureTree("smoke-fixture.txt", []), *self.entries[1:]],
                        [*self.entries[:2], FixtureEntry("textures")],
                        [*self.entries[:2], FixtureTree("textures", [FixtureEntry("other.dds")])],
                        [*self.entries[:2], FixtureTree("textures", [FixtureEntry("modhunter-smoke.txt"), FixtureEntry("other.dds")])],
                        [*self.entries[:2], FixtureTree("textures", [FixtureTree("modhunter-smoke.txt", [])])]):
            with self.subTest(entries=[entry.name() for entry in entries]):
                self.assertEqual(self.checker.dataLooksValid(FixtureTree("", entries)), 0)


class NativeInstallerSmokeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "mods" / "Smoke Disabled"
        self.target.mkdir(parents=True)
        path = Path(__file__).resolve().parents[1] / "native/scripts/mo2_smoke_plugin/modhunter_native_smoke/runner.py"
        tree = ast.parse(path.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NativeSmokePlugin")
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {"_arm_fixture_installer", "_disarm_fixture_installer", "_accept_fixture_installer"}]
        module = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
        scope = {"Path": Path, "os": os, "time": time, "PROFILE_NAME": "Modhunter Smoke",
                 "QtWidgets": SimpleNamespace(QComboBox=Combo, QPushButton=Button, QCheckBox=Checkbox, QProgressDialog=Progress)}
        exec(compile(module, str(path), "exec"), scope)
        self.accept = scope["_accept_fixture_installer"]
        self.arm = scope["_arm_fixture_installer"]
        self.disarm = scope["_disarm_fixture_installer"]
        self.content = SimpleNamespace(_lock=threading.RLock(), _lease="review-1-1", _jobs={"review-1-1": {
            "downloadId": "download-fixture", "profileId": "profile-fixture", "modName": "Smoke Disabled",
            "state": "installing", "nativeStarted": True, "backupId": "backup-fixture"}})
        self.plugin = SimpleNamespace(_installer_probe=None, _installer_probe_lock=threading.Lock(),
            _on_qt_thread=lambda: True, server=SimpleNamespace(content=self.content),
            org=SimpleNamespace(profileName=lambda: "Modhunter Smoke", basePath=lambda: str(self.root), modsPath=lambda: str(self.root / "mods"),
                modList=lambda: SimpleNamespace(getMod=lambda name: SimpleNamespace(absolutePath=lambda: str(self.target)))))
        self.env = patch.dict(os.environ, {"MODHUNTER_NATIVE_SMOKE_INSTANCE": str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.intent = {"reviewId": "review-1-1", "downloadId": "download-fixture", "profileId": "profile-fixture",
            "modName": "Smoke Disabled", "modRoot": str(self.target), "deadline": time.monotonic() + 10}

    def test_exact_name_then_merge_is_the_only_accepted_sequence(self):
        self.arm(self.plugin, self.intent)
        merge = Button()
        overwrite = Dialog("QueryOverwriteDialog", {"mergeBtn": merge, "backupBox": Checkbox()})
        self.assertFalse(self.accept(self.plugin, overwrite))
        ok = Button()
        self.assertTrue(self.accept(self.plugin, Dialog("SimpleInstallDialog", {"nameCombo": Combo("Smoke Disabled"), "okBtn": ok})))
        self.assertTrue(ok.clicked)
        self.assertTrue(self.accept(self.plugin, overwrite))
        self.assertTrue(merge.clicked)
        events = self.disarm(self.plugin)
        self.assertEqual([e["dialog"] for e in events], ["SimpleInstallDialog", "QueryOverwriteDialog"])
        self.assertFalse(self.accept(self.plugin, overwrite))

    def test_wrong_name_ambient_dialog_and_missing_backup_never_click(self):
        self.arm(self.plugin, self.intent)
        button = Button()
        self.assertFalse(self.accept(self.plugin, Dialog("SimpleInstallDialog", {"nameCombo": Combo("Another Mod"), "okBtn": button})))
        self.assertFalse(self.accept(self.plugin, Dialog("UnexpectedWarning", {"okBtn": button})))
        self.content._jobs["review-1-1"]["backupId"] = ""
        self.assertFalse(self.accept(self.plugin, Dialog("SimpleInstallDialog", {"nameCombo": Combo("Smoke Disabled"), "okBtn": button})))
        self.assertFalse(button.clicked)

    def test_wrong_live_lease_or_expired_probe_cannot_accept_dialog(self):
        self.arm(self.plugin, self.intent)
        button = Button()
        dialog = Dialog("SimpleInstallDialog", {"nameCombo": Combo("Smoke Disabled"), "okBtn": button})
        self.content._lease = "review-other"
        self.assertFalse(self.accept(self.plugin, dialog))
        self.content._lease = "review-1-1"
        self.plugin._installer_probe["deadline"] = time.monotonic() - 1
        self.assertFalse(self.accept(self.plugin, dialog))
        self.assertFalse(button.clicked)
