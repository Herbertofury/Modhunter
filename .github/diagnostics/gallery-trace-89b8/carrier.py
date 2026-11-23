"""Detached diagnostic on one immutable on-disk CI artifact, never release evidence."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import zipfile

REPO = "Herbertofury/Modhunter"
BRANCH = "refs/heads/diagnostics/gallery-trace-89b8"
HEAD = "89b8ef3f62fc097e94f5da9c2647c5ff6dac27c8"
TREE = "2f8ebe3590efe3f8908bcb17edb024a16be2e825"
RUN = 45130741512
ART = 14798661378
ARCHIVE_HASH = "b0c1884be0ece8a637531a629318ccdcbaf35a7c5db80deb84939055f45cf350"
INNER_HASH = "14d22ec5b51ee5a81e358b82d4b0e7d1c4a46235b8c6faa6c750ba3016b1b74f"
EXE_HASH = "c18168112a58065ace55902907868bcc91ac9fba4268d6e7bee7df17cd652d04"
FIXTURE_HASH = "afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0"
BASE_HASH = "3eb7df125e5ae7c63f9587ac0c2e3008c94a1e8157be8a7a49f0be983e948787"
OVERLAY_HASH = "fedd033c558a99dea9973c6dd75fc396710f6877076155ebfe1e7e94f432c350"
SIDECAR_HASH = "5800c471a5f3bad4d7619bd8b73fbb8166e09c786628583027afe77413e689e7"
LAUNCHER_HASH = "e83d6e420117122e8ce0e809b4573f4e623bc974b13a38e933cd5fdd14c2bcb9"
BINDER_HASH = "1ac9e84ca4ab00f55bea96f6b129f60e82ba207170e8cc7fc7f18d021e1858bc"
BRANCH_PATHS = {
    ".github/diagnostics/gallery-trace-89b8/carrier.py",
    ".github/diagnostics/gallery-trace-89b8/gallery_trace_sidecar.cjs",
    ".github/diagnostics/gallery-trace-89b8/measure_native_library.cjs",
    ".github/workflows/gallery-trace-89b8.yml",
}
CHECKS = (
    "packaged_offline_dashboard", "owned_webview_profile", "owned_native_foreground",
    "visible_native_document", "fresh_list_compact", "import_complete", "exact_last_item_search",
    "search_restore_survivor", "listDensity_complete", "listDensityReset_complete",
    "gallery_complete", "galleryDensity_complete", "list_complete", "finalDensityReset_complete",
    "no_renderer_errors", "unchanged_executable",
)
INTERACTIONS = ("listDensity", "listDensityReset", "gallery", "galleryDensity", "list", "finalDensityReset")
TIMINGS = (*INTERACTIONS, "render", "lastItemSearch", "searchRestore")
STATS = ("ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "TaskDuration", "LayoutCount", "RecalcStyleCount", "Nodes")
PHASES = {"layout", "style", "paint", "tasks", "events", "animationFrames", "functions", "microtasks", "garbageCollection"}
SELECTOR_GROUPS = {"providerLiteral", "chapterLiteral", "providerPseudoShape", "chapterPseudoShape", "other"}
SELECTOR_METRICS = {"rows", "attempts", "matches", "fastRejects", "accumulatedMatchUs"}
SUMMARY_FIELDS = {
    "schemaVersion", "driverWallMs", "clockUncertaintyMs", "phases", "rendererTaskCoverageMs",
    "knownEventCoverageMs", "driverStartToUniqueClickMs", "clickEndToDriverDoneMs", "selectorStats",
    "unattributed", "proofKind", "fixtureSha256", "executableSha256", "overlayScriptSha256",
}
# A pristine harness, D identical checked overlay with CDP absent, B overlay
# with trace. Fixed, balanced order chosen before any Windows samples.
STAGES = {
    "aa": ("A", "D", "D", "A", "D", "A", "A", "D"),
    "ab": ("B", "A", "A", "B", "A", "B", "B", "A"),
}


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()


def source_identity(root, carrier):
    require(git(root, "rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [HEAD, TREE], "wrong_frozen_source")
    require(not git(root, "status", "--porcelain", "--untracked-files=all"), "frozen_source_dirty")
    require(git(carrier, "rev-list", "--parents", "-n", "1", "HEAD").split() == [os.environ["GITHUB_SHA"], HEAD],
            "unexpected_carrier_parent")
    changed = git(carrier, "diff", "--name-status", HEAD, "HEAD").splitlines()
    require(set(changed) == {f"A\t{p}" for p in BRANCH_PATHS}, "unexpected_carrier_paths")
    require(not git(carrier, "status", "--porcelain", "--untracked-files=all"), "carrier_source_dirty")


def artifact_metadata(metadata):
    w = metadata.get("workflow_run") or {}
    require(metadata.get("id") == ART and metadata.get("name") == "Modhunter-Rust-Windows"
            and metadata.get("expired") is False and metadata.get("size_in_bytes") == 24283594
            and metadata.get("digest") == "sha256:" + ARCHIVE_HASH and w.get("id") == RUN
            and w.get("repository_id") == w.get("head_repository_id") == 1454632389
            and w.get("head_sha") == HEAD and w.get("head_branch") == "upgrade/modhunter-mo2-refresh-fast",
            "artifact_metadata_mismatch")


def member(archive, name, limit):
    matches = [entry for entry in archive.infolist() if entry.filename == name]
    require(len(matches) == 1 and 0 < matches[0].file_size <= limit and not matches[0].is_dir(),
            "bound_member_absent_or_invalid")
    return archive.read(matches[0])  # exact named entry only; CRC checked by ZipFile


def jsonmember(archive, name):
    return json.loads(member(archive, name, 128 * 1024).decode("utf-8-sig"))


def materialize(blob):
    require(len(blob) == 24283594 and digest(blob) == ARCHIVE_HASH, "outer_archive_mismatch")
    with zipfile.ZipFile(io.BytesIO(blob)) as outer:
        native = jsonmember(outer, "native-library-performance/native-library-performance.json")
        smoke = jsonmember(outer, "native-smoke/native-smoke.json")
        inner = member(outer, "Modhunter-Rust-Windows.zip", 18 * 1024 * 1024)
    require(len(inner) == 17111860 and digest(inner) == INNER_HASH, "portable_archive_mismatch")
    require(all(r["ok"] is True and r["candidateCommit"] == HEAD and r["sourceDirty"] is False
                and r["executableSha256"] == EXE_HASH for r in (native, smoke)), "old_native_binding_mismatch")
    require(native["fixtureSha256"] == FIXTURE_HASH and native["scriptSha256"] == BASE_HASH
            and native["launcherSha256"] == LAUNCHER_HASH and native["timingsMs"]["gallery"] > 5000,
            "old_gate_receipt_mismatch")
    with zipfile.ZipFile(io.BytesIO(inner)) as portable:
        build = jsonmember(portable, "BUILD_INFO.json")
        require(build == {"commit": HEAD, "workflow": f"https://github.com/{REPO}/actions/runs/{RUN}",
                          "version": "0.12.5"}, "portable_build_mismatch")
        exe = member(portable, "Modhunter.exe", 28 * 1024 * 1024)
    require(len(exe) == 26703360 and exe[:2] == b"MZ" and digest(exe) == EXE_HASH, "exe_mismatch")
    return exe, native


def fetch():
    api = f"repos/{REPO}/actions/artifacts/{ART}"
    def get(path):
        result = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True, check=False, timeout=100)
        require(result.returncode == 0, "artifact_read_denied_or_unavailable")
        return result.stdout
    artifact_metadata(json.loads(get(api)))
    return materialize(get(api + "/zip"))


def patched_launcher(original):
    require(digest(original) == LAUNCHER_HASH, "launcher_preimage_mismatch")
    text = original.decode("utf-8")
    old = '$root = (Resolve-Path -LiteralPath "$PSScriptRoot/../..").Path'
    require(text.count(old) == 1 and text.count('"$PSScriptRoot/bind_native_proof.py"') == 2, "launcher_shape_changed")
    # Keep the exact same launcher for all arms, but locate the source binder
    # and working directory in the separate pristine checkout. The adjacent
    # worker is still selected by the original PSScriptRoot references.
    text = text.replace(old, '$root = (Resolve-Path -LiteralPath $env:MODHUNTER_GALLERY_SOURCE).Path')
    text = text.replace('"$PSScriptRoot/bind_native_proof.py"', '"$root/native/scripts/bind_native_proof.py"')
    return text.encode("utf-8")


def reveal_staged_arms(root, temp):
    native = root / "native" / "scripts"
    carrier = Path(__file__).resolve().parent
    baseline = (native / "measure_native_library.cjs").read_bytes()
    overlay = (carrier / "measure_native_library.cjs").read_bytes()
    sidecar = (carrier / "gallery_trace_sidecar.cjs").read_bytes()
    require(digest(baseline) == BASE_HASH and digest(overlay) == OVERLAY_HASH and digest(sidecar) == SIDECAR_HASH
            and digest((native / "bind_native_proof.py").read_bytes()) == BINDER_HASH, "harness_binding_mismatch")
    launcher = patched_launcher((native / "benchmark_native_library.ps1").read_bytes())
    for name, worker in (("A", baseline), ("overlay", overlay)):
        folder = temp / name
        folder.mkdir()
        (folder / "measure_native_library.cjs").write_bytes(worker)
        (folder / "benchmark_native_library.ps1").write_bytes(launcher)
        if name == "overlay":
            (folder / "gallery_trace_sidecar.cjs").write_bytes(sidecar)
    return digest(launcher)


def read_json(path):
    return json.loads(path.read_text("utf-8-sig"))


def folded_geometry(report):
    # Compare exact initial viewport/theme/focus and all card/transition
    # receipts without treating renderer task counters or time as geometry.
    baseline = {
        "engine": {k: report["engine"][k] for k in ("protocolVersion", "product", "revision", "jsVersion")},
        "initial": report["initial"],
        "import": report["importGeometry"],
        "restore": report["restoreGeometry"],
        "steps": {i: {k: report["interactions"][i][k] for k in (
            "completeLibrary", "geometryBefore", "geometryAfter", "frameEndpoint")} for i in INTERACTIONS},
    }
    return digest(json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode())


def validate_native(report, harness_hash, launcher_hash):
    require(report.get("ok") is True and report.get("nativeWebView2") is True
            and report.get("candidateCommit") == HEAD and report.get("sourceDirty") is False
            and report.get("executableSha256") == EXE_HASH and report.get("fixtureSha256") == FIXTURE_HASH
            and report.get("scriptSha256") == harness_hash and report.get("launcherSha256") == launcher_hash
            and report.get("records") == 11207 and not report.get("errors"), "native_arm_binding_failed")
    require(report.get("checks") == [{"name": n, "passed": True} for n in CHECKS], "native_arm_check_missing")
    isolation = report.get("isolation", {})
    require(isolation.get("ok") is True and isolation.get("policyValuesRemoved") == 2
            and not isolation.get("cleanupErrors") and bool(isolation.get("restoredFolders"))
            and all(x.get("originalExisted") is False and x.get("manifestMatched") is True
                    for x in isolation["restoredFolders"]), "owned_isolation_failed")
    views = [report["importGeometry"], report["restoreGeometry"]]
    views += [report["interactions"][i]["completeLibrary"] for i in INTERACTIONS]
    require(all(v.get("rows") == 11207 and v.get("completeOrderedIds") is True and v.get("allLaidOut") is True
                and v.get("noContentVisibility") is True for v in views), "complete_card_geometry_failed")
    require(all(report["interactions"][i]["frameEndpoint"] == {
        "structuralBatchActive": False, "transitionProperty": "background", "transitionDuration": "0.12s"}
        for i in INTERACTIONS), "two_frame_transition_failed")
    timings = report["timingsMs"]
    require(set(timings) == set(TIMINGS) and all(finite(v) and v >= 0 for v in timings.values()), "invalid_native_timing")
    deltas = report["interactions"]["gallery"]["metricDeltas"]
    require(all(k in deltas and finite(deltas[k]) for k in STATS), "missing_cdp_diagnostics")
    return {
        "geometrySha256": folded_geometry(report),
        "engineIdentity": engine_identity(report),
        "timingsMs": {k: round(v, 3) for k, v in timings.items()},
        "galleryMetricDeltas": {k: round(deltas[k], 6) for k in STATS},
    }


def engine_identity(report):
    e = report["engine"]
    keys = {"product": r"Edg/[0-9]+(?:\.[0-9]+){3}", "revision": r"@[0-9a-f]{40}"}
    out = {k: e[k] for k in keys}
    require(all(isinstance(out[k], str) and re.fullmatch(p, out[k]) for k, p in keys.items()), "engine_metadata_unusable")
    return out


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def validate_summary(summary, paired_sample):
    require(set(summary) == SUMMARY_FIELDS and summary["schemaVersion"] == 1
            and summary["proofKind"] == "diagnostic_only" and summary["fixtureSha256"] == FIXTURE_HASH
            and summary["executableSha256"] == EXE_HASH and summary["overlayScriptSha256"] == OVERLAY_HASH,
            "trace_summary_binding_failed")
    require(finite(summary["driverWallMs"]) and abs(summary["driverWallMs"] - paired_sample["timingsMs"]["gallery"]) <= .01,
            "trace_different_wall_endpoint")
    require(finite(summary["clockUncertaintyMs"]) and 0 <= summary["clockUncertaintyMs"] <= 25, "trace_clock_unusable")
    # Publishing is a whitelist. The helper itself never persists raw events;
    # reject arbitrary nested strings, paths/URLs, extra keys or selector names.
    require(set(summary["phases"]) <= PHASES,
            "trace_phase_schema")
    compact = {k: summary[k] for k in ("driverWallMs", "clockUncertaintyMs", "knownEventCoverageMs")}
    require(all(finite(v) for v in compact.values()), "trace_number_schema")
    for k in ("rendererTaskCoverageMs", "driverStartToUniqueClickMs", "clickEndToDriverDoneMs"):
        v = summary[k]
        require(v is None or isinstance(v, list) and len(v) == 2 and all(finite(n) for n in v), "trace_interval_schema")
        compact[k] = v
    for phase in summary["phases"].values():
        require(set(phase) == {"events", "observedUnionMs"} and all(finite(v) for v in phase.values()), "trace_phase_metric_schema")
    stats = summary["selectorStats"]
    require(set(stats) == {"available", "events", "invalidRows", "groups"} and type(stats["available"]) is bool
            and all(finite(stats[k]) for k in ("events", "invalidRows")), "trace_selector_schema")
    if stats["available"]:
        require(set(stats["groups"]) == SELECTOR_GROUPS, "trace_selector_group_schema")
        for group in stats["groups"].values():
            require(set(group) <= SELECTOR_METRICS and all(finite(v) for v in group.values()), "trace_selector_metric_schema")
    else:
        require(stats["groups"] is None, "trace_unavailable_not_empty")
    unattributed = summary["unattributed"]
    require(set(unattributed) == {"gridIntrinsicVsOtherLayout", "finalFrameOwnership", "uncapturedOrExternalMs", "unbalancedEvents"}
            and all(unattributed[k] is None for k in ("gridIntrinsicVsOtherLayout", "finalFrameOwnership", "uncapturedOrExternalMs"))
            and finite(unattributed["unbalancedEvents"]), "trace_unattributed_schema")
    return dict(compact, phases=summary["phases"], selectorStats=stats, unattributed=unattributed)


def compare(samples, other):
    pairs = []
    require(len(samples) == 8, "incomplete_pairs")
    for idx in range(0, 8, 2):
        pair = {p["arm"]: p["timingsMs"]["gallery"] for p in samples[idx:idx+2]}
        require(set(pair) == {"A", other}, "wrong_pairing")
        a, b = pair["A"], pair[other]
        pairs.append({"pairedDeltaMs": round(b - a, 3), "boundMs": round(max(150, .05 * a), 3)})
    med = round(statistics.median(p["pairedDeltaMs"] for p in pairs), 3)
    med_bound = round(statistics.median(p["boundMs"] for p in pairs), 3)
    return {"pairs": pairs, "medianPairedDeltaMs": med, "medianBoundMs": med_bound,
            "withinPredeclaredBound": abs(med) <= med_bound and all(abs(p["pairedDeltaMs"]) <= p["boundMs"] for p in pairs)}


def clean_profile():
    command = ("$roots=@([Environment]::GetFolderPath('ApplicationData'),"
               "[Environment]::GetFolderPath('LocalApplicationData')); "
               "if ($roots.Count -ne 2 -or ($roots | Where-Object { !$_.Trim() -or "
               "(Test-Path -LiteralPath (Join-Path $_ 'com.modhunter.desktop')) })) {exit 2}")
    check = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    require(check.returncode == 0, "existing_app_profile_refused")


def run_arm(temp, root, exe, launcher_hash, seq, arm):
    clean_profile()
    require(digest(exe.read_bytes()) == EXE_HASH, "native_exe_changed")
    work = temp / f"run-{seq:02d}"
    require(not work.exists(), "arm_output_exists")
    folder = temp / ("A" if arm == "A" else "overlay")
    launcher = folder / "benchmark_native_library.ps1"
    harness_hash = BASE_HASH if arm == "A" else OVERLAY_HASH
    require(digest(launcher.read_bytes()) == launcher_hash
            and digest((folder / "measure_native_library.cjs").read_bytes()) == harness_hash,
            "staged_harness_changed")
    if arm != "A":
        require(digest((folder / "gallery_trace_sidecar.cjs").read_bytes()) == SIDECAR_HASH, "staged_sidecar_changed")
    env = os.environ.copy()
    require(not any(env.get(k) for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")),
            "measurement_must_not_inherit_artifact_token")
    env["MODHUNTER_GALLERY_SOURCE"] = str(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("MODHUNTER_GALLERY_TRACE", None)
    if arm == "B":
        env["MODHUNTER_GALLERY_TRACE"] = "owned-diagnostic-v1"
    # Do not put paths/profile metadata/raw reports in GitHub console. The
    # exact existing launcher owns its worker+cleanup and its 540 s deadline.
    completed = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(launcher),
                                "-Executable", str(exe), "-Output", str(work)], cwd=root, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    receipt = work / "native-library-performance.json"
    if completed.returncode:
        # Exact closed-set diagnostics, not error details. Only consider the
        # owned small checkpoint and never leak paths, text or selector data.
        if receipt.is_file() and receipt.stat().st_size <= 256 * 1024:
            errors = read_json(receipt).get("errors", [])
            for marker, code in (
                ("Required trace categories unavailable", "tracing_categories_unavailable"),
                ("Trace stream unsupported or incomplete", "trace_stream_loss_or_unsupported"),
                ("Trace summary input exceeds", "trace_stream_limit"),
                ("clock uncertainty", "trace_clock_unavailable"),
                ("Unique renderer timing marks", "trace_marks_unavailable"),
            ):
                if any(isinstance(e, str) and marker in e for e in errors):
                    raise RuntimeError(code)
        raise RuntimeError("native_arm_failed")
    require(receipt.stat().st_size <= 256 * 1024, "oversized_native_receipt")
    sample = dict(validate_native(read_json(receipt), harness_hash, launcher_hash), arm=arm, ordinal=seq)
    trace = work / "gallery-trace-sidecar.json"
    if arm == "B":
        require(trace.stat().st_size <= 24 * 1024, "oversized_trace_summary")
        sample["trace"] = validate_summary(read_json(trace), sample)
    else:
        require(not trace.exists(), "unexpected_trace_in_plain_arm")
    require(digest(exe.read_bytes()) == EXE_HASH, "native_exe_changed")
    clean_profile()
    return sample


def store_report(path, report):
    blob = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode()
    require(len(blob) <= 128 * 1024, "aggregate_artifact_too_large")
    scratch = path.with_name("report.tmp")
    scratch.write_bytes(blob)
    scratch.replace(path)


def guard():
    require(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Windows" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and os.environ.get("GITHUB_REPOSITORY") == REPO and os.environ.get("GITHUB_REF") == BRANCH
            and os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
            "not_owned_first_attempt")
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    root, carrier = workspace / "exact", workspace / "carrier"
    source_identity(root, carrier)
    return root, carrier, Path(os.environ["RUNNER_TEMP"]) / "modhunter-gallery-frozen"


def main():
    root, carrier, temp = guard()
    require(len(sys.argv) == 2 and sys.argv[1] in {"stage", "measure"}, "unknown_subcommand")
    exe = temp / "Modhunter.exe"
    if sys.argv[1] == "stage":
        temp.mkdir()
        body, previous = fetch()
        exe.write_bytes(body)
        (temp / "old-binding.json").write_text(json.dumps({
            "engineIdentity": engine_identity(previous), "historicalGalleryMs": round(previous["timingsMs"]["gallery"], 3),
        }), encoding="utf-8")
        reveal_staged_arms(root, temp)
        print(f"Frozen artifact {ART}, source {HEAD}, executable {EXE_HASH}")
        return
    launcher_hash = digest(patched_launcher((root / "native/scripts/benchmark_native_library.ps1").read_bytes()))
    report = {
        "schemaVersion": 1, "proofKind": "diagnostic_only_not_release_acceptance", "status": "incomplete",
        "sourceCommit": HEAD, "sourceTree": TREE, "originalRun": RUN, "inputArtifactId": ART,
        "executableSha256": EXE_HASH, "fixtureSha256": FIXTURE_HASH, "isolatedLauncherSha256": launcher_hash,
        "windowsArtifactSha256": ARCHIVE_HASH, "baselineScriptSha256": BASE_HASH,
        "overlayScriptSha256": OVERLAY_HASH, "traceSidecarSha256": SIDECAR_HASH,
        "carrierCommit": os.environ["GITHUB_SHA"], "previous": read_json(temp / "old-binding.json"),
        "samples": {"aa": [], "ab": []},
    }
    start = time.monotonic()
    receipt = temp / "report.json"
    store_report(receipt, report)
    for phase, order in STAGES.items():
        for arm in order:
            seq = sum(map(len, report["samples"].values())) + 1
            if time.monotonic() - start > 20 * 60:
                report["status"] = "owned_runtime_budget_exhausted"
                store_report(receipt, report)
                return
            sample = None
            try:
                sample = run_arm(temp, root, exe, launcher_hash, seq, arm)
                source_identity(root, carrier)
                groups = report["samples"]
                first = (groups["aa"] or [sample])[0]
                require(sample["geometrySha256"] == first["geometrySha256"] and
                        sample["engineIdentity"] == first["engineIdentity"], "render_geometry_or_engine_changed")
            except (RuntimeError, OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
                report["status"] = "arm_rejected_or_trace_unavailable"
                report["rejected"] = {"ordinal": seq, "arm": arm, "phase": phase}
                if sample is not None:
                    report["rejected"]["incomparableBoundSample"] = sample
                # Own fixed failure codes only, never third-party exception text.
                code = error.args[0] if type(error) is RuntimeError and error.args else ""
                if isinstance(code, str) and code.isascii() and code.replace("_", "").isalpha() and len(code) <= 64:
                    report["rejected"]["reason"] = code
                store_report(receipt, report)
                return
            report["samples"][phase].append(sample)
            store_report(receipt, report)
            print(f"Diagnostic sample {seq:02d} ({phase}/{arm}) bound; Gallery {sample['timingsMs']['gallery']} ms, not release evidence")
        report[phase] = compare(report["samples"][phase], "D" if phase == "aa" else "B")
        if phase == "aa" and not report[phase]["withinPredeclaredBound"]:
            report["status"] = "inconclusive_plain_harness_drift_no_trace_run"
            store_report(receipt, report)
            return
    report["status"] = ("paired_diagnostic_complete" if report["ab"]["withinPredeclaredBound"]
                        else "inconclusive_trace_perturbation_no_timing_attribution")
    report["selectorStatsAvailableInEveryTrace"] = all(
        s["trace"]["selectorStats"]["available"] for s in report["samples"]["ab"] if s["arm"] == "B")
    report["sameHistoricalEngineProductAndRevision"] = report["previous"]["engineIdentity"] == report["samples"]["aa"][0]["engineIdentity"]
    store_report(receipt, report)
    print(report["status"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Frozen Gallery carrier rejected its fixed input, environment or staged source.", file=sys.stderr)
        sys.exit(1)
