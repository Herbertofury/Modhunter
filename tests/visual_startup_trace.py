"""Bounded startup-file tracing of the actual compiled Modhunter dashboard.

This is a delivery/diagnostic probe, not release performance acceptance. Its
default fixture contains 32 complete records; --mods 1000 supports a separate
scaling diagnosis. No record is capped, virtualized or omitted within a run.
Chromium owns the 30-second capture and writes its actual trace file. The script
never calls CDP Tracing.start/end or adopts the startup session.

Primary documentation:
https://www.chromium.org/developers/how-tos/trace-event-profiling-tool/recording-tracing-runs/
https://chromium.googlesource.com/chromium/src/+/a0e9c1e80c1bd2ab242a969fd460af4a07cae15e/components/tracing/common/tracing_switches.cc
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

import visual_release_qa as acceptance

ROOT = acceptance.ROOT
EXPECTED_BROWSER_VERSION = "157.0.8103.0"
STARTUP_CAPTURE_SECONDS = 30
BROWSER_LAUNCH_SECONDS = 30
FILE_FLUSH_SECONDS = 10
WORKER_SECONDS = 65
TERMINATE_GRACE_SECONDS = 5
KILL_GRACE_SECONDS = 3
CATEGORIES = ("devtools.timeline", "blink.user_timing")
REPORT_NAME = "startup-trace.json"
READY_JS = "() => typeof window.modhunterImportCatalog==='function' && !!window.__ARSENAL_QA__"
IMPORT_JS = "fixture => window.modhunterImportCatalog(fixture)"
MARK_JS = "name => performance.mark(name)"
LIBRARY_GEOMETRY_JS = """() => {
 const rows=[...document.querySelectorAll('#modList > .mod-row')];
 return {view:document.body.dataset.libraryView,density:document.body.dataset.density,
  catalogCount:window.__ARSENAL_QA__.getCatalog().items.length,
  ids:rows.map(row=>row.dataset.id),
  rectangles:rows.map(row=>{const box=row.getBoundingClientRect();return [row.dataset.id,
   box.left+scrollX,box.top+scrollY,box.width,box.height];}),
  allLaidOut:rows.every(row=>{const box=row.getBoundingClientRect();return (
   row.getClientRects().length>0&&box.width>0&&box.height>0);}),
  allVisibleStyles:rows.every(row=>{const style=getComputedStyle(row);return (
   style.display!=='none'&&style.visibility==='visible'&&style.contentVisibility==='visible');}),
  documentElements:document.querySelectorAll('*').length,
  width:innerWidth,scrollWidth:document.documentElement.scrollWidth,scrollX,scrollY};
}"""


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_status() -> list[str]:
    return subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True
    ).splitlines()


def complete_library(page, count: int, expected_view: str) -> dict:
    state = page.evaluate(LIBRARY_GEOMETRY_JS)
    expected = [f"item-{index}" for index in range(count)]
    assert state["catalogCount"] == count and state["ids"] == expected, "Complete ordered fixture differs from rendered records"
    assert state["view"] == expected_view, f"Expected actual {expected_view} view"
    assert state["allLaidOut"] and state["allVisibleStyles"], "Some matching records lack real visible layout"
    assert state["scrollWidth"] <= state["width"] + 1, "Library overflows the viewport horizontally"
    state["rows"] = len(state["ids"])
    state["orderedIdsSha256"] = hashlib.sha256(json.dumps(state["ids"]).encode()).hexdigest()
    return state


def trace_summary(trace: dict | list, marks: dict) -> dict:
    events = trace.get("traceEvents", []) if isinstance(trace, dict) else trace
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("Startup output is not a trace-event JSON document")
    names = Counter(event.get("name", "") for event in events)
    starts = [event for event in events if event.get("name") == marks["start"]]
    ends = [event for event in events if event.get("name") == marks["end"]]
    pairs = [(start, end) for start in starts for end in ends
             if start.get("pid") == end.get("pid") and start.get("tid") == end.get("tid")
             and isinstance(start.get("ts"), (int, float)) and isinstance(end.get("ts"), (int, float))
             and start["ts"] < end["ts"]]
    result = {"traceEvents": len(events), "startMarkers": len(starts), "endMarkers": len(ends),
              "eventCounts": dict(names.most_common()), "ownedWindow": None,
              "styleEventsInOwnedWindow": 0, "layoutEventsInOwnedWindow": 0,
              "timedEventTotals": [], "timedEvents": [],
              "timingScope": "Actual trace events between owned markers. Durations are inclusive and grouped by process/thread/name; nested or parallel durations are not summed into wall time or attributed to an unmeasured cause.",
              "lossEvidence": "Startup JSON has no CDP completion acknowledgement; full losslessness is not asserted."}
    if not pairs:
        return result
    if len(pairs) != 1:
        raise ValueError("Owned startup trace markers do not identify one unambiguous renderer window")
    start, end = pairs[0]
    result["ownedWindow"] = {"pid": start["pid"], "tid": start["tid"],
                             "startUs": start["ts"], "endUs": end["ts"],
                             "durationMs": (end["ts"] - start["ts"]) / 1000}
    totals = {}
    for event in events:
        timestamp = event.get("ts")
        if not isinstance(timestamp, (int, float)) or not start["ts"] <= timestamp <= end["ts"]:
            continue
        same_thread = event.get("pid") == start["pid"] and event.get("tid") == start["tid"]
        name = event.get("name", "")
        if same_thread and name in ("UpdateLayoutTree", "RecalculateStyles"):
            result["styleEventsInOwnedWindow"] += 1
        if same_thread and name == "Layout":
            result["layoutEventsInOwnedWindow"] += 1
        if event.get("ph") not in ("X", "B", "E"):
            continue
        result["timedEvents"].append({"name": name, "pid": event.get("pid"), "tid": event.get("tid"),
            "timestampUs": timestamp, "phase": event.get("ph"), "durationUs": event.get("dur"),
            "threadDurationUs": event.get("tdur"), "category": event.get("cat"),
            "mainRendererThread": same_thread})
        duration = event.get("dur")
        if isinstance(duration, (int, float)):
            key = (event.get("pid"), event.get("tid"), name)
            value = totals.setdefault(key, {"pid": key[0], "tid": key[1], "name": name,
                "mainRendererThread": same_thread, "count": 0, "inclusiveDurationUs": 0,
                "maximumDurationUs": 0})
            value["count"] += 1
            value["inclusiveDurationUs"] += duration
            value["maximumDurationUs"] = max(value["maximumDurationUs"], duration)
    result["timedEventTotals"] = sorted(totals.values(), key=lambda value: value["inclusiveDurationUs"], reverse=True)
    return result


def read_trace(path: Path) -> dict | list:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError("Startup trace JSON has an unsupported root value")
    return value


def worker(args) -> int:
    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    output = args.output
    report_path = output / REPORT_NAME
    trace_path = output / ("startup-" + args.run_id + ".trace.json")
    marks = {kind: f"modhunter-startup-file-{args.run_id}-gallery-{kind}" for kind in ("start", "end")}
    flags = ["--trace-startup=-*," + ",".join(CATEGORIES),
             f"--trace-startup-duration={STARTUP_CAPTURE_SECONDS}",
             "--trace-startup-file=" + str(trace_path), "--trace-startup-format=json",
             "--trace-startup-owner=controller", "--trace-startup-record-mode=record-until-full"]
    report = {"schemaVersion": 1, "ok": False, "phase": "prepare", "errors": [], "phaseTimings": [],
              "purpose": "Bounded startup-file diagnostic; not full-library release acceptance",
              "mods": args.mods, "marks": marks, "startupFlags": flags, "categories": list(CATEGORIES),
              "startupCaptureSeconds": STARTUP_CAPTURE_SECONDS, "browserLaunchTimeoutSeconds": BROWSER_LAUNCH_SECONDS,
              "fileFlushSeconds": FILE_FLUSH_SECONDS, "requestedExecutable": str(args.executable_path),
              "expectedBrowserVersion": args.expected_version, "browserSelection": "explicit or discovered system executable; no bundled fallback",
              "platform": platform.platform(), "trace": {"file": trace_path.name, "exists": False},
              "method": "Compile actual source assets in memory, import every record, mark one real Gallery click, verify full record order/layout, and parse Chromium's own file. Startup time consumes the capture window; missing markers remain incomplete evidence."}

    def checkpoint(phase):
        report["phase"] = phase
        timing = {"phase": phase, "elapsedMs": round((time.monotonic() - started) * 1000, 1)}
        report["phaseTimings"].append(timing)
        write_json(report_path, report)
        print("STARTUP_TRACE " + json.dumps(timing), flush=True)

    browser = None
    parsed_trace = None
    page_errors = []
    checkpoint("prepare")
    try:
        report["candidateCommit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        report["sourceStatusAtStart"] = source_status()
        assert not report["sourceStatusAtStart"], "Startup diagnostic requires clean committed source"
        fixture = acceptance.dashboard.catalog(profile="startup-file-diagnostic", count=args.mods)
        fixture.update(offline=True, profileName=f"Startup trace diagnostic · {args.mods} complete fixture records")
        report["fixtureSha256"] = hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest()
        html, report["assets"] = acceptance.compile_dashboard()
        with sync_playwright() as runtime:
            try:
                checkpoint("launch-browser")
                launch_started = time.monotonic()
                # The two existing Linux harness flags are unchanged. No new
                # sandbox, GPU, renderer or automation force flags are added.
                browser = runtime.chromium.launch(executable_path=str(args.executable_path), headless=True,
                    timeout=BROWSER_LAUNCH_SECONDS * 1000,
                    args=["--no-sandbox", "--disable-dev-shm-usage", *flags])
                report["browserVersion"] = browser.version
                identity = browser.new_browser_cdp_session()
                try:
                    report["browserProtocolIdentity"] = identity.send("Browser.getVersion")
                    arguments = identity.send("Browser.getBrowserCommandLine")["arguments"]
                    try:
                        processes = identity.send("SystemInfo.getProcessInfo")["processInfo"]
                        browser_processes = [entry for entry in processes if entry.get("type") == "browser"]
                        if len(browser_processes) == 1:
                            report["browserPid"] = browser_processes[0]["id"]
                    except Exception as error:
                        report["browserPidReadError"] = f"{type(error).__name__}: {error}"
                finally:
                    identity.detach()
                assert arguments and arguments[0], "Browser did not report its actual executable"
                report["browserArguments"] = arguments
                report["resolvedExecutable"] = str(Path(arguments[0]).resolve())
                report["executableEvidence"] = "Actual Browser.getBrowserCommandLine arguments[0] plus Browser.getVersion"
                assert report["browserVersion"] == args.expected_version, "Actual browser differs from the explicitly expected version"
                assert all(flag in arguments for flag in flags), "Actual browser command line omitted a required startup trace flag"
                checkpoint("prepare-actual-dashboard")
                context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                page = context.new_page()
                page.set_default_timeout(10_000)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.set_content(html, wait_until="load")
                page.wait_for_function(READY_JS)
                page.evaluate(IMPORT_JS, fixture)
                acceptance.settle(page)
                report["before"] = complete_library(page, args.mods, "list")
                checkpoint("actual-gallery-click")
                page.evaluate(MARK_JS, marks["start"])
                action_started = time.monotonic()
                page.locator("#libraryGallery").click()
                acceptance.settle(page)
                report["instrumentedGalleryWallMs"] = (time.monotonic() - action_started) * 1000
                page.evaluate(MARK_JS, marks["end"])
                report["after"] = complete_library(page, args.mods, "gallery")
                assert not page_errors, page_errors
                checkpoint("wait-for-owned-trace-file")
                deadline = launch_started + STARTUP_CAPTURE_SECONDS + FILE_FLUSH_SECONDS
                last_signature = None
                while time.monotonic() < deadline:
                    if trace_path.is_file():
                        stat = trace_path.stat()
                        signature = (stat.st_size, stat.st_mtime_ns)
                        if signature != last_signature:
                            last_signature = signature
                            try:
                                parsed_trace = read_trace(trace_path)
                                break
                            except (OSError, UnicodeError, ValueError) as error:
                                report["lastFileReadError"] = f"{type(error).__name__}: {error}"
                    time.sleep(0.1)
                report["fileParsedBeforeGracefulClose"] = parsed_trace is not None
            except Exception as error:
                # Preserve the real rejection before browser/driver cleanup,
                # which can itself reach the external process deadline.
                report["failurePhase"] = report["phase"]
                report["errors"].append(f"{type(error).__name__}: {error}")
                checkpoint("incomplete-before-close")
                raise
            finally:
                if browser is not None:
                    checkpoint("close-browser-gracefully")
                    browser.close()
                    browser = None
                    checkpoint("browser-closed")
        checkpoint("validate-actual-trace-file")
        # Summarize the final closed-browser file, not an earlier polling read.
        parsed_trace = read_trace(trace_path)
        report["traceSummary"] = trace_summary(parsed_trace, marks)
        summary = report["traceSummary"]
        assert summary["traceEvents"], "Startup file contains no trace events"
        assert summary["ownedWindow"], "Startup file lacks both owned Gallery markers on one renderer thread"
        assert summary["styleEventsInOwnedWindow"] and summary["layoutEventsInOwnedWindow"], "Owned Gallery window lacks actual style and layout events"
        report["sourceStatusAtEnd"] = source_status()
        assert report["sourceStatusAtEnd"] == report["sourceStatusAtStart"], "Diagnostic changed repository source"
        checkpoint("complete")
    except Exception as error:
        report.setdefault("failurePhase", report["phase"])
        message = f"{type(error).__name__}: {error}"
        if message not in report["errors"]:
            report["errors"].append(message)
        checkpoint("incomplete")
    finally:
        report["pageErrors"] = page_errors
        report["errors"].extend(error for error in page_errors if error not in report["errors"])
        if trace_path.is_file():
            raw = trace_path.read_bytes()
            report["trace"].update(exists=True, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        report["elapsedMs"] = round((time.monotonic() - started) * 1000, 1)
        report["ok"] = report["phase"] == "complete" and not report["errors"]
        write_json(report_path, report)
    return 0 if report["ok"] else 1


def verified_browser_process(pid: int, trace_argument: str, started_ticks=None):
    """Verify a candidate PID without matching other browsers by name."""
    if pid <= 1:
        raise ValueError("Unsafe candidate process id")
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        arguments = [os.fsdecode(value) for value in command if value]
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        if fields[0] == "Z":
            return None
        birth = fields[19]  # /proc field 22; fields here begin with field 3.
        if trace_argument not in arguments or any(value.startswith("--type=") for value in arguments):
            raise ValueError("PID does not carry this run's exact browser trace-file argument")
        if started_ticks is not None and birth != started_ticks:
            raise ValueError("Browser PID was reused")
        pgid = os.getpgid(pid)
        if pgid != pid:
            raise ValueError("Browser process group is not the verified browser PID")
        return {"pid": pid, "pgid": pgid, "startedTicks": birth,
                "traceArgumentVerified": True, "executable": arguments[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def cleanup_owned_processes(process, report, log_path: Path, trace_argument: str) -> dict:
    """Bound cleanup to this worker and its separately detached Chromium group.

    The pw:browser launch log supplies the PID even if launch never returned a
    CDP session. Each candidate must still carry this run's unique trace-file
    argument and lead its own process group before any browser signal is sent.
    """
    result = {"browserCandidates": [], "browsers": [], "rejections": []}
    candidates = {int(match) for match in re.findall(r"<launched>\s+pid=(\d+)", log_path.read_text(errors="replace"))}
    if isinstance(report.get("browserPid"), (int, float)):
        candidates.add(int(report["browserPid"]))
    result["browserCandidates"] = sorted(candidates)
    for pid in sorted(candidates):
        try:
            verified = verified_browser_process(pid, trace_argument)
            if verified is not None:
                result["browsers"].append(verified)
        except (OSError, ValueError) as error:
            result["rejections"].append({"pid": pid, "reason": str(error)})

    def signal_browser(entry, signum):
        try:
            current = verified_browser_process(entry["pid"], trace_argument, entry["startedTicks"])
            if current is None:
                return
            os.killpg(current["pgid"], signum)
            entry["lastSignal"] = signal.Signals(signum).name
        except ProcessLookupError:
            pass
        except (OSError, ValueError) as error:
            result["rejections"].append({"pid": entry["pid"], "reason": str(error)})

    def worker_signal(signum):
        if process.poll() is not None:
            return
        try:
            if os.getpgid(process.pid) != process.pid:
                raise ValueError("Owned worker is no longer its original process-group leader")
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        except (OSError, ValueError) as error:
            result["rejections"].append({"pid": process.pid, "reason": str(error)})

    def still_alive(entry):
        try:
            return verified_browser_process(entry["pid"], trace_argument, entry["startedTicks"]) is not None
        except (OSError, ValueError):
            return False  # Refuse to signal a changed/unverifiable process.

    for entry in result["browsers"]:
        signal_browser(entry, signal.SIGTERM)
    worker_signal(signal.SIGTERM)
    deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline and (process.poll() is None or any(still_alive(entry) for entry in result["browsers"])):
        time.sleep(0.05)
    for entry in result["browsers"]:
        signal_browser(entry, signal.SIGKILL)
    worker_signal(signal.SIGKILL)
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while time.monotonic() < deadline and (process.poll() is None or any(still_alive(entry) for entry in result["browsers"])):
        time.sleep(0.05)
    result["workerExited"] = process.poll() is not None
    result["remainingVerifiedBrowserPids"] = [entry["pid"] for entry in result["browsers"] if still_alive(entry)]
    result["complete"] = result["workerExited"] and not result["remainingVerifiedBrowserPids"] and not result["rejections"]
    return result


def supervise(args) -> int:
    output = args.output
    report_path = output / REPORT_NAME
    run_id = uuid.uuid4().hex
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--run-id", run_id,
               "--output", str(output), "--mods", str(args.mods),
               "--executable-path", str(args.executable_path), "--expected-version", args.expected_version]
    write_json(report_path, {"ok": False, "phase": "starting-owned-worker", "errors": [],
                            "mods": args.mods, "runId": run_id})
    timed_out = False
    cleanup = None
    log_path = output / "startup-trace.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env=dict(os.environ, DEBUG="pw:browser"))
        try:
            code = process.wait(timeout=WORKER_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            partial = json.loads(report_path.read_text("utf-8"))
            trace_argument = "--trace-startup-file=" + str(output / ("startup-" + run_id + ".trace.json"))
            cleanup = cleanup_owned_processes(process, partial, log_path, trace_argument)
            code = process.poll()
        if code not in (0, None) and cleanup is None:
            partial = json.loads(report_path.read_text("utf-8"))
            trace_argument = "--trace-startup-file=" + str(output / ("startup-" + run_id + ".trace.json"))
            cleanup = cleanup_owned_processes(process, partial, log_path, trace_argument)
    report = json.loads(report_path.read_text("utf-8"))
    report.update(workerExitCode=code, workerDeadlineSeconds=WORKER_SECONDS,
                  terminateGraceSeconds=TERMINATE_GRACE_SECONDS, killGraceSeconds=KILL_GRACE_SECONDS,
                  maximumExternalSeconds=WORKER_SECONDS + TERMINATE_GRACE_SECONDS + KILL_GRACE_SECONDS,
                  externallyTimedOut=timed_out)
    if cleanup is not None:
        report["ownedProcessCleanup"] = cleanup
    if timed_out or code != 0:
        report["ok"] = False
        report.setdefault("errors", []).append(
            f"Owned startup worker exceeded its {WORKER_SECONDS}s deadline at {report.get('phase')}"
            if timed_out else f"Owned startup worker exited {code}")
    write_json(report_path, report)
    print(json.dumps({"ok": report["ok"], "mods": args.mods, "phase": report.get("phase"),
        "browserVersion": report.get("browserVersion"), "trace": report.get("trace"),
        "errors": report.get("errors"), "summary": {key: report.get("traceSummary", {}).get(key)
        for key in ("traceEvents", "startMarkers", "endMarkers", "styleEventsInOwnedWindow", "layoutEventsInOwnedWindow")}}), flush=True)
    return 0 if report["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "native/target/startup-trace")
    parser.add_argument("--mods", type=int, default=32, help="Complete diagnostic fixture size; default 32 is the delivery proof")
    parser.add_argument("--executable-path", type=Path, help="Actual system Chromium executable or wrapper")
    parser.add_argument("--expected-version", default=EXPECTED_BROWSER_VERSION)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "linux" or not Path("/proc/self/cmdline").is_file():
        parser.error("This bounded diagnostic requires Linux /proc for exact owned-browser cleanup")
    if args.mods < 1:
        parser.error("The diagnostic fixture must contain at least one complete record")
    executable = args.executable_path or shutil.which("chromium") or shutil.which("chromium-browser")
    if not executable:
        parser.error("A system Chromium executable is required; supply --executable-path")
    args.executable_path = Path(executable).expanduser().resolve()
    if not args.executable_path.is_file():
        parser.error("The selected Chromium executable is not a file")
    args.output = args.output.expanduser().resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        if not args.run_id or len(args.run_id) != 32 or any(character not in "0123456789abcdef" for character in args.run_id):
            parser.error("Worker requires its owned run identifier")
        return worker(args)
    return supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
