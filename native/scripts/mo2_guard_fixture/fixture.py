"""Prepare the owned control files, or verify a completed disposable beta-12 experiment."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import stat
import struct
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
HOST = "10bb9f91d565d1a26424ab816b303a014573dd53a7f318a0a9e88cf7c282b45f"
QUICK = "808db649ca9eb63fa8dde4af18193724b2ad1d36bc82c1c58f6f80e406a1d0d1"
PURPOSE = "isolated native protected install negative control"
EVENTS = [
    "native dialog accepted a different new name",
    "first native installed callback observed disabled",
    "native installed callback enabled the new row",
    "native dialog accepted an existing fixture row",
    "native overwrite dialog offered merge on existing fixture row",
    "native merge replaced the existing fixture member",
]


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_host(root):
    for relative in ("", "plugins", "profiles", "profiles/Guard Fixture", "mods", "downloads",
                     "ModOrganizer.exe", "plugins/installer_quick.dll", "ModOrganizer.ini", "portable.txt"):
        path = root / relative
        require(path.exists() and path.resolve() == path and not
                getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                "Missing or linked fixture component: " + relative)
    require(digest(root / "ModOrganizer.exe") == HOST and digest(root / "plugins/installer_quick.dll") == QUICK,
            "Expected both pinned beta-12 binaries")
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(root / "ModOrganizer.ini", encoding="utf-8-sig")
    require(ini["General"]["gameName"] == "Modhunter CI Fixture", "Expected the isolated test-game plugin")
    require(ini["General"]["selected_profile"] == "@ByteArray(Guard Fixture)", "Expected the isolated profile")
    require(Path(ini["Settings"]["base_directory"]).resolve() == root, "Unexpected base directory")
    for key, directory in (("mod_directory", "mods"), ("profiles_directory", "profiles"),
                           ("download_directory", "downloads"), ("overwrite_directory", "overwrite")):
        require(ini["Settings"][key] == "%BASE_DIR%/" + directory, "Unexpected location for " + key)


def prepare(root):
    validate_host(root)
    profile = root / "profiles/Guard Fixture/modlist.txt"
    require(not any((root / "mods").iterdir()) and not any((root / "downloads").iterdir())
            and (not profile.exists() or not profile.read_bytes()), "Expected empty disposable native data")
    token = uuid.uuid4()
    suggested = "MODHUNTER_QA__suggested__" + token.hex
    existing = "MODHUNTER_QA__existing__" + token.hex
    marker = root / "guard-fixture.json"
    require(not marker.exists() and not (root / "guard-result.json").exists(), "Fixture has already been used")
    with (root / "plugins/guard_probe.py").open("xb") as handle:
        handle.write((HERE / "guard_probe.py").read_bytes())
    archive = root / "downloads" / (suggested + ".zip")
    with zipfile.ZipFile(archive, "x") as mod:
        mod.writestr("smoke-fixture.txt", b"guard fixture")
        mod.writestr("textures/modhunter-smoke.txt", b"guard fixture")
        mod.writestr("ModhunterQANew.esp", struct.pack("<4sIIIIHH4sHfII", b"TES4", 18, 0, 0, 0, 44, 0,
                                                      b"HEDR", 12, 1.7, 0, 0x800))
    control = root / "mods" / existing
    control.mkdir()
    (control / "smoke-fixture.txt").write_bytes(b"before native merge")
    (control / "unrelated.txt").write_bytes(b"keep fixture sibling")
    (control / "meta.ini").write_bytes(b"[General]\nversion=1.0\n")
    profile.write_bytes(("-" + existing + "\n").encode())
    marker.write_text(json.dumps({"purpose": PURPOSE, "runId": str(token), "archiveSha256": digest(archive)}), "utf-8")
    return {"prepared": True, "root": str(root)}


def verify(root):
    validate_host(root)
    marker = json.loads((root / "guard-fixture.json").read_text("utf-8"))
    require(marker["purpose"] == PURPOSE, "Unexpected fixture marker")
    token = uuid.UUID(marker["runId"]).hex
    suggested, alternate, existing = ["MODHUNTER_QA__" + part + "__" + token for part in ("suggested", "alternate", "existing")]
    receipt = json.loads((root / "guard-result.json").read_text("utf-8"))
    require(receipt.get("ok") is True and receipt.get("events") == EVENTS
            and (receipt.get("suggested"), receipt.get("returned"), receipt.get("merged")) == (suggested, alternate, existing),
            "Unexpected native receipt or missing observation")
    require(digest(root / "plugins/guard_probe.py") == digest(HERE / "guard_probe.py"), "Observer source differs")
    require(digest(root / "downloads" / (suggested + ".zip")) == marker["archiveSha256"], "Archive changed")
    require({p.name for p in (root / "mods").iterdir()} == {alternate, existing}, "Final mod membership differs")
    profile = (root / "profiles/Guard Fixture/modlist.txt").read_text("utf-8-sig").splitlines()
    require([row for row in profile if row and not row.startswith("#")] == ["+" + alternate, "-" + existing],
            "Native profile did not persist the activated row and preserve the previous row's state")
    with zipfile.ZipFile(root / "downloads" / (suggested + ".zip")) as archive:
        for relative in ("smoke-fixture.txt", "ModhunterQANew.esp", "textures/modhunter-smoke.txt"):
            expected = archive.read(relative)
            for target in (alternate, existing):
                require((root / "mods" / target / relative).read_bytes() == expected, "Native member differs")
    require((root / "mods" / existing / "unrelated.txt").read_bytes() == b"keep fixture sibling",
            "Unexpected change to unrelated fixture member")
    return {"verified": True, "hostVersion": receipt["hostVersion"], "mo2Sha256": HOST, "quickInstallerSha256": QUICK,
            "observerSha256": digest(HERE / "guard_probe.py"), "nativeReceiptSha256": digest(root / "guard-result.json"),
            "events": EVENTS, "activatedAlternatePersisted": True, "originalSuggestionAbsent": True,
            "existingMemberReplaced": True, "unrelatedMemberPreserved": True, "archiveMembersComparedPerTarget": 3}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    print(json.dumps((prepare if args.action == "prepare" else verify)(args.fixture.resolve(strict=True)), indent=2))
