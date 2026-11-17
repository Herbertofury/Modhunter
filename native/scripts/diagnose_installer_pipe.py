"""Diagnose PS5.1 preview output with and without a console; never install.

The package assembler and plugin source are real, source-bound bytes. Native
helpers, archive tools and generated dependency notices are explicitly inert
fixtures. These disposable bundles are not release resources or native-runtime
acceptance. Only the script's mandatory -Preview path is invoked.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "b79ca8a9c54240e502cb4f8c0db458a12a65eef1"
INSTALLER = "native/Install_MO2_Plugin.ps1"
ASSEMBLER = "native/scripts/prepare_mo2_installer.py"
HOST = "native/crates/modhunter-desktop/src/instances_windows.rs"
WORKFLOW = ".github/workflows/installer-pipe-diagnostic.yml"
CREATE_NO_WINDOW = 0x08000000
INVALID_HANDLE_HRESULT = -2147024890
ENCODING_LINE = "if ($Json) { [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) }\n"
RECEIPT_LINE = "if ($Json) { [Console]::Out.WriteLine(($report | ConvertTo-Json -Depth 5 -Compress)) }"
PIPE_RECEIPT = """if ($Json) {
    $receiptBytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($report | ConvertTo-Json -Depth 5 -Compress) + [Environment]::NewLine)
    $receiptOutput = [Console]::OpenStandardOutput()
    $receiptOutput.Write($receiptBytes, 0, $receiptBytes.Length)
    $receiptOutput.Flush()
}"""
RUNTIME_PROBE = r"""$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ModhunterPipeConsole {
    [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
    [DllImport("kernel32.dll")] public static extern uint GetConsoleOutputCP();
}
'@
$facts = [ordered]@{
    psVersion = $PSVersionTable.PSVersion.ToString()
    edition = $PSEdition
    clrVersion = [Environment]::Version.ToString()
    process64Bit = [Environment]::Is64BitProcess
    scriptPath = $PSCommandPath
    scriptRoot = $PSScriptRoot
    consoleHandle = [ModhunterPipeConsole]::GetConsoleWindow().ToInt64().ToString()
    initialCodePage = [ModhunterPipeConsole]::GetConsoleOutputCP()
    setterSucceeded = $false
    exceptions = @()
}
try {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    $facts.setterSucceeded = $true
} catch {
    $exception = $_.Exception
    while ($null -ne $exception) {
        $facts.exceptions += @{ type = $exception.GetType().FullName; hresult = $exception.HResult; message = $exception.Message }
        $exception = $exception.InnerException
    }
}
$bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($facts | ConvertTo-Json -Depth 6 -Compress) + [Environment]::NewLine)
$stream = [Console]::OpenStandardOutput()
$stream.Write($bytes, 0, $bytes.Length)
$stream.Flush()
"""


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, encoding="utf-8").strip()


def source_bytes(relative: str, ref: str = BASELINE) -> bytes:
    return subprocess.check_output(["git", "show", f"{ref}:{relative}"], cwd=ROOT)


def load_assembler():
    spec = importlib.util.spec_from_file_location("installer_pipe_assembler", ROOT / ASSEMBLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def patched_installer(original: bytes) -> tuple[bytes, dict]:
    source = original.decode("utf-8")
    assert source.count(ENCODING_LINE) == 1 and source.count(RECEIPT_LINE) == 1
    candidate = source.replace(ENCODING_LINE, "", 1).replace(RECEIPT_LINE, PIPE_RECEIPT, 1)
    # Restore the removed setup exactly at its original predecessor, rather
    # than accepting an arbitrary source diff as an output-only experiment.
    predecessor = "Set-StrictMode -Version Latest\n"
    assert source.count(predecessor) == 1 and candidate.count(PIPE_RECEIPT) == 1
    restored = candidate.replace(PIPE_RECEIPT, RECEIPT_LINE, 1).replace(
        predecessor, predecessor + ENCODING_LINE, 1)
    assert restored == source
    return candidate.encode("utf-8"), {
        "originalSha256": digest(original), "candidateSha256": digest(candidate.encode("utf-8")),
        "removedSetup": ENCODING_LINE, "originalReceipt": RECEIPT_LINE,
        "candidateReceipt": PIPE_RECEIPT, "reversible": True,
        "scope": "Remove console code-page setup and emit the same JSON as explicit UTF-8 bytes; no other script change",
    }


def tree(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        details = path.stat(follow_symlinks=False)
        assert not path.is_symlink() and not getattr(details, "st_file_attributes", 0) & 0x400
        result[path.relative_to(root).as_posix()] = "<directory>" if path.is_dir() else digest(path.read_bytes())
    return result


def build_fixture(owned: Path, commit: str, run_url: str) -> tuple[Path, Path, dict, dict]:
    module = load_assembler()
    mirror = owned / "disposable-source-mirror"
    files = module.runtime_sources(ROOT)
    inert_sources = {"native/resources/THIRD_PARTY_NOTICES.json": json.dumps({
        "diagnosticFixture": True, "scope": "Inert generated notices; this is not a release bundle"}).encode("utf-8")}
    selected = set(files) | set(module.ROOT_FILES.values()) | {
        "native/Cargo.toml", "native/crates/modhunter-desktop/tauri.conf.json"}
    input_hashes = {}
    for relative in sorted(selected):
        body = inert_sources.get(relative)
        if body is None:
            body = (ROOT / relative).read_bytes()
        target = mirror / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        input_hashes[relative] = {"sha256": digest(body), "inertFixture": relative in inert_sources}
    for name in module.ARCHIVE_FILES:
        relative = "native/resources/archive-tools/" + name
        body = ("NON_EXECUTABLE_INSTALLER_PIPE_DIAGNOSTIC_ONLY: " + name + "\n").encode("ascii")
        target = mirror / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        input_hashes[relative] = {"sha256": digest(body), "inertFixture": True}
    helpers = {}
    for name in ("modhunter-cli.exe", "modhunter-qt-bridge.dll"):
        helpers[name] = mirror / "inert-native-inputs" / name
        helpers[name].parent.mkdir(exist_ok=True)
        body = ("NON_EXECUTABLE_INSTALLER_PIPE_DIAGNOSTIC_ONLY: " + name + "\n").encode("ascii")
        helpers[name].write_bytes(body)
        input_hashes["inert-native-inputs/" + name] = {"sha256": digest(body), "inertFixture": True}
    bodies = module.assemble(mirror, helpers["modhunter-cli.exe"], helpers["modhunter-qt-bridge.dll"],
                             commit, run_url, files)
    assert bodies["Install_MO2_Plugin.ps1"] == (ROOT / INSTALLER).read_bytes()
    baseline = owned / "unchanged-preview-package"
    candidate = owned / "pipe-output-preview-package"
    module.write_bundle(baseline, bodies)
    replacement, patch = patched_installer(bodies["Install_MO2_Plugin.ps1"])
    modified = {name: body for name, body in bodies.items() if name != "SHA256SUMS.txt"}
    modified["Install_MO2_Plugin.ps1"] = replacement
    modified["SHA256SUMS.txt"] = module.manifest_bytes(modified)
    assert {name for name in bodies if bodies[name] != modified[name]} == {
        "Install_MO2_Plugin.ps1", "SHA256SUMS.txt"}
    module.write_bundle(candidate, modified)
    facts = {
        "scope": "Exact current assembler and plugin bytes with explicitly inert native/archive/notice inputs; preview only, never execute or distribute",
        "compiledNativeHelpers": False, "installableRelease": False,
        "inputHashes": input_hashes, "unchanged": {name: digest(body) for name, body in bodies.items()},
        "candidate": {name: digest(body) for name, body in modified.items()},
        "identity": json.loads(bodies["BUILD_INFO.json"]),
    }
    return baseline, candidate, facts, patch


@contextmanager
def read_locks(package: Path):
    """Match the native host's GENERIC_READ / FILE_SHARE_READ bundle locks."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles = []
    try:
        for path in sorted(package.rglob("*")):
            if not path.is_file():
                continue
            handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            if not kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


def write_json(path: Path, value: dict) -> None:
    pending = path.with_suffix(".json.tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "native/target/installer-pipe-diagnostic")
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    commit = git("rev-parse", "HEAD")
    pinned = {name: digest((ROOT / name).read_bytes()) for name in (INSTALLER, ASSEMBLER, HOST)}
    for name in pinned:
        assert (ROOT / name).read_bytes() == source_bytes(name), f"Production baseline changed: {name}"
    run_url = "https://github.com/Herbertofury/Modhunter/actions/runs/" + os.environ.get("GITHUB_RUN_ID", "1")
    if args.check_inputs:
        with tempfile.TemporaryDirectory(prefix="modhunter-pipe-input-check-") as temporary:
            _, _, facts, patch = build_fixture(Path(temporary), commit, run_url)
            print(json.dumps({"ok": True, "windowsExecuted": False, "sourceCommit": commit,
                              "pinnedSourceSha256": pinned, "fixture": facts, "patch": patch}, indent=2))
        return 0
    assert (sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Windows" and os.environ.get("RUNNER_TEMP")), \
        "This probe only runs on an owned disposable Windows CI runner"
    assert os.environ.get("GITHUB_SHA") == commit
    assert not git("status", "--porcelain", "--untracked-files=all"), "Require clean diagnostic source"
    output = args.output.resolve()
    assert not output.exists() or not any(output.iterdir()), "Refusing an existing diagnostic result"
    output.mkdir(parents=True, exist_ok=True)
    powershell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    assert powershell.is_file()
    proof = {"schemaVersion": 1, "kind": "installer-pipe-diagnostic", "ok": False,
             "sourceCommit": commit, "productionBaseline": BASELINE, "sourceDirty": False,
             "scope": "Actual Windows PowerShell 5.1 stdout/console diagnosis; disposable inert preview fixtures only",
             "actualInstallExecuted": False, "nativeMO2": False, "releaseAcceptance": False,
             "productionSourceSha256": pinned,
             "diagnosticSourceSha256": {name: digest((ROOT / name).read_bytes()) for name in (
                 "native/scripts/diagnose_installer_pipe.py", WORKFLOW)},
             "powershell": str(powershell), "cases": [], "errors": []}
    receipt = output / "installer-pipe-diagnostic.json"
    write_json(receipt, proof)

    def execute(label: str, script: Path, arguments: list[str], hidden: bool, cwd: Path) -> dict:
        command = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(script), *arguments]
        flags = CREATE_NO_WINDOW if hidden else 0
        started = time.perf_counter()
        child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        timed_out = False
        try:
            stdout, stderr = child.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            # Every installer child is forced to Preview. This only terminates
            # this owned diagnostic child, never a transaction or unrelated PID.
            timed_out = True
            child.kill()
            stdout, stderr = child.communicate(timeout=10)
        (output / f"{label}.stdout.bin").write_bytes(stdout)
        (output / f"{label}.stderr.bin").write_bytes(stderr)
        (output / f"{label}.stderr.txt").write_text(stderr.decode("utf-8", errors="replace"), encoding="utf-8")
        try:
            parsed = json.loads(stdout.decode("utf-8-sig"))
            json_error = None
        except (UnicodeError, ValueError) as error:
            parsed, json_error = None, str(error)
        result = {"name": label, "command": command, "fileArgument": str(script),
                  "expectedPSScriptRootFromFileArgument": str(script.parent), "workingDirectory": str(cwd),
                  "creationFlags": flags, "createNoWindow": hidden, "processId": child.pid,
                  "exitCode": child.returncode, "timedOut": timed_out,
                  "elapsedMs": (time.perf_counter() - started) * 1000,
                  "stdoutBytes": len(stdout), "stderrBytes": len(stderr),
                  "stdoutSha256": digest(stdout), "stderrSha256": digest(stderr),
                  "stdoutBase64": base64.b64encode(stdout).decode("ascii"),
                  "stderrBase64": base64.b64encode(stderr).decode("ascii"),
                  "json": parsed, "jsonError": json_error}
        proof["cases"].append(result)
        write_json(receipt, proof)
        assert not timed_out and len(stdout) <= 262144 and len(stderr) <= 262144, label
        return result

    try:
        with tempfile.TemporaryDirectory(prefix="modhunter-pipe-probe-", dir=os.environ["RUNNER_TEMP"]) as temporary:
            owned = Path(temporary)
            probe = owned / "runtime-facts.ps1"
            probe.write_text(RUNTIME_PROBE, encoding="utf-8")
            shutil.copyfile(probe, output / probe.name)
            runtime = {}
            for hidden in (False, True):
                name = "runtime-hidden" if hidden else "runtime-console"
                result = execute(name, probe, [], hidden, owned)
                assert result["exitCode"] == 0 and result["stderrBytes"] == 0 and isinstance(result["json"], dict)
                facts = result["json"]
                assert facts["psVersion"].startswith("5.1.") and facts["edition"] == "Desktop" and facts["process64Bit"]
                assert facts["scriptPath"] == str(probe) and facts["scriptRoot"] == str(owned)
                runtime[hidden] = facts
            proof["runtime"] = {"console": runtime[False], "hidden": runtime[True]}
            target = owned / "MO2 日本語 [preview]"
            target.mkdir()
            marker = bytearray(512)
            marker[:2] = b"MZ"
            struct.pack_into("<I", marker, 60, 128)
            marker[128:132] = b"PE\0\0"
            struct.pack_into("<H", marker, 132, 0x8664)
            (target / "ModOrganizer.exe").write_bytes(marker)
            before_target = tree(target)
            basic = ["-MO2Directory", str(target), "-Json", "-Preview"]
            invalid_console = execute("unchanged-invalid-console", ROOT / INSTALLER, basic, False, owned)
            invalid_hidden = execute("unchanged-invalid-hidden", ROOT / INSTALLER, basic, True, owned)
            assert invalid_console["exitCode"] == 2 and invalid_console["stderrBytes"] == 0
            assert invalid_console["json"]["code"] == "invalid_request" and invalid_console["json"]["ok"] is False
            assert invalid_hidden["exitCode"] != 0 and invalid_hidden["json"] is None
            assert b"OutputEncoding" in base64.b64decode(invalid_hidden["stderrBase64"])
            assert runtime[False]["setterSucceeded"] and not runtime[True]["setterSucceeded"]
            assert runtime[True]["consoleHandle"] == "0" and runtime[True]["initialCodePage"] == 0
            assert any(item["hresult"] == INVALID_HANDLE_HRESULT for item in runtime[True]["exceptions"])
            assert tree(target) == before_target
            proof["unchangedConsoleFailureConfirmed"] = True
            write_json(receipt, proof)

            baseline, candidate, fixture, patch = build_fixture(owned, commit, run_url)
            proof["fixture"], proof["candidatePatch"] = fixture, patch
            for package, prefix in ((baseline, "unchanged"), (candidate, "candidate")):
                for name in ("Install_MO2_Plugin.ps1", "SHA256SUMS.txt", "BUILD_INFO.json"):
                    shutil.copyfile(package / name, output / f"{prefix}-{name}")
            valid = {}
            for package_name, package in (("unchanged", baseline), ("candidate", candidate)):
                package_before = tree(package)
                identity = fixture["identity"]
                arguments = basic + ["-ExpectedVersion", identity["version"], "-ExpectedCommit", identity["commit"],
                                     "-ExpectedManifestSha256", digest((package / "SHA256SUMS.txt").read_bytes())]
                assert "-Preview" in arguments and "-ExpectedInstalledFingerprint" not in arguments
                with read_locks(package):
                    for hidden in (False, True):
                        label = package_name + ("-preview-hidden" if hidden else "-preview-console")
                        valid[package_name, hidden] = execute(label, package / "Install_MO2_Plugin.ps1", arguments, hidden, owned)
                        assert tree(package) == package_before and tree(target) == before_target
            reference = valid["unchanged", False]
            assert reference["exitCode"] == 0 and reference["stderrBytes"] == 0
            expected = reference["json"]
            assert (expected["schemaVersion"] == 1 and expected["ok"] is True and expected["status"] == "preview"
                    and expected["code"] == "ready" and expected["targetDirectory"] == str(target)
                    and re.fullmatch("[a-f0-9]{64}", expected["installedFingerprint"]))
            failed = valid["unchanged", True]
            assert failed["exitCode"] != 0 and failed["json"] is None
            assert b"OutputEncoding" in base64.b64decode(failed["stderrBase64"])
            for hidden in (False, True):
                result = valid["candidate", hidden]
                assert result["exitCode"] == 0 and result["stderrBytes"] == 0 and result["json"] == expected
                stdout = base64.b64decode(result["stdoutBase64"])
                assert stdout.startswith(b"{") and str(target).encode("utf-8").replace(b"\\", b"\\\\") in stdout
                stdout.decode("utf-8", errors="strict")
            proof["candidatePreservesCompleteReceipt"] = True
            proof["targetsUnchanged"] = True
            proof["packagesUnchangedDuringPreview"] = True
            proof["consoleIndependentUtf8PreviewConfirmed"] = True
            proof["ok"] = True
    except BaseException as error:
        proof["errors"].append({"type": type(error).__name__, "message": str(error)})
    finally:
        proof["sourceStatusAtEnd"] = git("status", "--porcelain", "--untracked-files=all").splitlines()
        if git("rev-parse", "HEAD") != commit or proof["sourceStatusAtEnd"]:
            proof["ok"] = False
            proof["errors"].append({"type": "SourceChanged", "message": "Diagnostic source changed during the probe"})
        write_json(receipt, proof)
    print(json.dumps({"ok": proof["ok"], "receipt": str(receipt), "cases": len(proof["cases"]),
                      "errors": proof["errors"]}, indent=2))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
