"""Prepare only: assemble a sidecar script from exact stock smoke scaffolding."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from fixture import PACKAGE_PIN, SIZES, TEST, VERSION, own_path, package_digest, require, write_new

HERE = Path(__file__).resolve().parent
SCRIPT_SHA256 = "320e09da7c61e590dce05dc255ebe66c8fa0862f236990445775f1e8cfd3bb45"
GAME_SHA256 = "a315d7589a084d18473dc82e079fd170b119ac71368bc5fb684d32fc6cca6c71"
INIT_SHA256 = "43dd1b849678e1d625113e621b585f22cf9f665324eeeb97c3184edcd54a4afb"


def checked(path: Path, digest: str) -> bytes:
    data = own_path(path, directory=False).read_bytes()
    require(hashlib.sha256(data).hexdigest() == digest, f"Unexpected exact ec9 source bytes: {path.name}")
    return data


def substitute(source: str, old: str, new: str) -> str:
    require(source.count(old) == 1, "Unexpected stock scaffold shape")
    return source.replace(old, new)


def replace_section(source: str, start: str, end: str, inserted: str) -> str:
    require(source.count(start) == 1 and source.count(end) == 1, "Unexpected stock section anchors")
    lo, hi = source.index(start), source.index(end)
    require(lo < hi, "Unexpected stock section order")
    return source[:lo] + inserted + source[hi:]


def render(source: str, rows: int) -> str:
    require(rows in SIZES, "Unknown fixture size")
    source = replace_section(source, '<#\n.SYNOPSIS', '[CmdletBinding()]',
        '<#\n.SYNOPSIS\nDisposable stock-only Modhunter scale observer. Not the tiny install/restore smoke.\n#>\n')
    source = substitute(source,
        "if (!$IsWindows) { throw 'The native MO2 smoke requires Windows; it cannot pass through a mock on another OS.' }",
        "if (!$IsWindows) { throw 'Stock MO2 scale requires Windows; no simulated pass is accepted.' }\n"
        "if ($RustCliPath -or $DesktopExecutable) { throw 'Stock scale observer does not run CLI/desktop/install acceptance.' }")
    source = substitute(source, "test = 'mo2-native-fixture-smoke-orchestrator'", f"test = '{TEST}-orchestrator'")
    source = substitute(source, "    phase = 'preflight'", f"    phase = 'preflight'\n    source = '{VERSION}'\n    expectedFixtureRows = {rows}")
    source = substitute(source, "native/evidence/windows-mo2-smoke", "native/evidence/windows-mo2-stock-scale")
    source = substitute(source, "    $Summary.phase = 'extract-portable-release'",
        "    if ($Summary.release.sha256 -cne 'e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815') { "
        "throw 'MO2 archive differs from the exact previously verified Test 2 stock receipt.' }\n"
        "    $Summary.release.integrity = 'Pinned official URL and size; SHA256 matches independently retained Test 2 stock smoke bytes'\n"
        "    $Summary.phase = 'extract-portable-release'")
    source = replace_section(source, "    foreach ($Mod in @(", "    foreach ($ProfileFile in @(",
        "    $FixturePython = Get-Command 'python.exe' -ErrorAction Stop\n"
        "    & $FixturePython.Source (Join-Path $PSScriptRoot 'seed.py') $InstanceRoot " + str(rows) + "\n"
        "    if ($LASTEXITCODE -ne 0) { throw 'Disposable stock scale fixture generation failed.' }\n")
    source = substitute(source, "@('__init__.py', 'runner.py', 'audit_probe.py', 'content_probe.py')",
        "@('__init__.py', 'runner.py', 'fixture.py', 'trace.py')")
    source = substitute(source, "    $Process = [Diagnostics.Process]::Start($StartInfo)",
        "    $LaunchClock = [Diagnostics.Stopwatch]::StartNew()\n    $Process = [Diagnostics.Process]::Start($StartInfo)")
    source = replace_section(source, "    $RequiredChecks = @(", "    foreach ($RequiredCheck in $RequiredChecks)",
        "    $Summary['launchToObservedReceiptMs'] = [Math]::Round($LaunchClock.Elapsed.TotalMilliseconds, 2)\n"
        f"    if ($Receipt.test -cne '{TEST}' -or $Receipt.source -cne '{VERSION}' "
        f"-or $Receipt.inventory.fixtureRows -ne {rows} -or $Receipt.runtime.platform -cne 'win32') {{\n"
        "        throw 'Receipt is not this exact Windows stock scale session.'\n    }\n"
        "    $RequiredChecks = @('stock_isolated_fixture', 'stock_large_snapshot', 'stock_ui_heartbeat', 'stock_startup_trace')\n")
    return source


def stage(repo: Path, output: Path, rows: int) -> None:
    require(rows in SIZES, "Unknown fixture size")
    require(not output.exists() and not output.is_symlink(), "Stage destination must be new")
    require(not output.resolve().is_relative_to(repo.resolve()), "Refuse writes in the source checkout")
    root = repo / "native" / "scripts"
    base = root / "mo2_smoke_plugin"
    source = checked(root / "mo2_native_smoke.ps1", SCRIPT_SHA256).decode("utf-8")
    game = checked(base / "game_modhunter_smoke.py", GAME_SHA256)
    init = checked(base / "modhunter_native_smoke" / "__init__.py", INIT_SHA256)
    require(package_digest(repo / "mo2_update_arsenal") == PACKAGE_PIN, "Not the unchanged released Test 2 package")
    script = render(source, rows)
    output.mkdir()
    write_new(output / "stock_scale.ps1", script)
    write_new(output / "seed.py", "import sys\nfrom pathlib import Path\n"
              "from mo2_smoke_plugin.modhunter_native_smoke.fixture import seed\n"
              "seed(Path(sys.argv[1]), int(sys.argv[2]))\n")
    fixture = output / "mo2_smoke_plugin"
    observer = fixture / "modhunter_native_smoke"
    observer.mkdir(parents=True)
    (fixture / "game_modhunter_smoke.py").write_bytes(game)
    (observer / "__init__.py").write_bytes(init)
    for name in ("fixture.py", "runner.py", "trace.py"):
        (observer / name).write_bytes((HERE / name).read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, choices=SIZES, default=SIZES[0])
    args = parser.parse_args()
    stage(args.repository, args.output, args.rows)


if __name__ == "__main__":
    main()
