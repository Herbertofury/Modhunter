"""Bounded Chromium tracing transport preflight; no production UI or benchmark.

Compare Playwright's public browser.start_tracing(page=...) helper with a CDP
session attached to that same page. Each transport gets a fresh browser and a
96-row deterministic DOM task. Full traces and explicit event/selector counts
are retained even when an arm fails. Zero events are never accepted.

Public API: https://playwright.dev/python/docs/api/class-browser#browser-start-tracing
Protocol: https://chromedevtools.github.io/devtools-protocol/tot/Tracing/
Selector category: Chromium third_party/blink/renderer/core/css/selector_statistics_flag.cc
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = (
    "devtools.timeline", "disabled-by-default-devtools.timeline",
    "disabled-by-default-blink.debug", "blink.user_timing",
)
ARMS = ("playwright-helper", "page-cdp")
ROWS = 96
PASSES = 8
BROWSER_LAUNCH_SECONDS = 30
ARM_TIMEOUT_SECONDS = 45
TRACE_STOP_SECONDS = 3
TRACE_EVENT_LIMIT = 100_000
TRACE_BYTE_LIMIT = 16 * 1024 * 1024
HTML = """<!doctype html><meta charset="utf-8"><title>Owned trace transport probe</title>
<style>
body{margin:0;font:14px sans-serif}#items{width:600px;display:flex;flex-wrap:wrap}
.entry{box-sizing:border-box;width:150px;height:28px;padding:4px;border:1px solid #444}
.entry strong{color:#234}.entry:nth-child(2n){background:#dde}
body.wide .entry{width:200px;height:34px;padding:7px}
body.wide .entry:nth-child(3n) strong{color:#962}.entry.active>strong{font-weight:400}
</style><main id="items"></main>"""
TASK = """async ({rows,passes}) => {
 const list=document.getElementById('items');
 performance.mark('modhunter-trace-probe-start');
 let checksum=0;
 for(let pass=0;pass<passes;pass++){
  document.body.classList.toggle('wide',pass%2===0);
  list.children[pass].classList.toggle('active');
  checksum+=list.getBoundingClientRect().height;
  checksum+=getComputedStyle(list.children[pass].firstElementChild).fontWeight==='400'?1:0;
  await new Promise(resolve=>requestAnimationFrame(resolve));
 }
 performance.mark('modhunter-trace-probe-end');
 performance.measure('modhunter-trace-probe-task','modhunter-trace-probe-start','modhunter-trace-probe-end');
 return {rows:list.children.length,passes,checksum,width:list.clientWidth};
}"""


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def trace_summary(trace):
    events = trace.get("traceEvents", [])
    names = Counter(event.get("name", "") for event in events)
    selectors = set()
    selector_rows = 0
    for event in events:
        if event.get("name") != "SelectorStats":
            continue
        timings = event.get("args", {}).get("selector_stats", {}).get("selector_timings", [])
        selector_rows += len(timings)
        selectors.update(row.get("selector", "") for row in timings if row.get("selector"))
    return {
        "traceEvents": len(events), "selectorStatsEvents": names["SelectorStats"],
        "selectorRows": selector_rows, "uniqueSelectors": len(selectors),
        "styleLayoutEvents": sum(names[name] for name in ("UpdateLayoutTree", "RecalculateStyles", "Layout")),
        "startMarkers": names["modhunter-trace-probe-start"],
        "endMarkers": names["modhunter-trace-probe-end"],
        "eventCounts": dict(names.most_common()),
    }


def trace_failures(summary):
    failures = []
    if not summary["traceEvents"]:
        failures.append("Tracing transport delivered zero events")
    if not summary["startMarkers"] or not summary["endMarkers"]:
        failures.append("Trace does not contain both owned workload markers")
    if not summary["styleLayoutEvents"]:
        failures.append("Trace has no real style/layout events for the forced-layout task")
    if not summary["selectorStatsEvents"] or not summary["uniqueSelectors"]:
        failures.append("Trace has no populated SelectorStats; do not start the large selector profile")
    return failures


def retain_trace(output, arm, trace):
    encoded = json.dumps(trace, separators=(",", ":")).encode("utf-8")
    destination = output / f"{arm}.trace.json.gz"
    destination.write_bytes(gzip.compress(encoded, mtime=0))
    return {"file": destination.name, "bytes": destination.stat().st_size,
            "uncompressedBytes": len(encoded),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def worker(arm, output, executable_path=None):
    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    report_path = output / f"{arm}.json"
    report = {"transport": arm, "ok": False, "phase": "start-playwright", "errors": [],
              "requestedExecutable": str(executable_path) if executable_path else None,
              "browserSelection": "explicit" if executable_path else "playwright-default",
              "browserLaunchTimeoutSeconds": BROWSER_LAUNCH_SECONDS,
              "startupDebug": "pw:browser", "phaseTimings": [],
              "rows": ROWS, "passes": PASSES, "categories": list(CATEGORIES)}

    def checkpoint(phase):
        report["phase"] = phase
        timing = {"phase": phase, "elapsedMs": round((time.monotonic() - started) * 1000, 1),
                  "atUnixMs": round(time.time() * 1000)}
        report["phaseTimings"].append(timing)
        write_json(report_path, report)
        print("TRACE_PHASE " + json.dumps({"transport": arm, **timing}), flush=True)

    trace = {"traceEvents": []}
    checkpoint("start-playwright")
    browser = None
    session = None
    events = []
    completed = {}
    state = {"finished": False, "overflow": False}
    try:
        with sync_playwright() as playwright:
            # Match the established full profiler's cold-start allowance. The
            # owned DOM task and trace-completion deadlines remain unchanged.
            launch_options = {"headless": True, "timeout": BROWSER_LAUNCH_SECONDS * 1000}
            if executable_path is not None:
                launch_options["executable_path"] = str(executable_path)
            checkpoint("launch")
            try:
                browser = playwright.chromium.launch(**launch_options)
            except Exception as error:
                # Persist the rejection before context-manager driver cleanup,
                # which can otherwise hide the cause until the worker is killed.
                report["errors"].append(f"{type(error).__name__}: {error}")
                checkpoint("launch-rejected")
                raise
            report["browserVersion"] = browser.version
            checkpoint("browser-launched")
            # Observe the launched process, including a wrapper's actual binary or
            # Playwright's default headless shell. chromium.executable_path can
            # instead name the full bundled browser, so is not launch evidence.
            identity_session = browser.new_browser_cdp_session()
            try:
                arguments = identity_session.send("Browser.getBrowserCommandLine")["arguments"]
                if not arguments or not arguments[0]:
                    raise RuntimeError("Chromium did not report its executable")
                report["resolvedExecutable"] = str(Path(arguments[0]).resolve())
                report["executableEvidence"] = "Browser.getBrowserCommandLine arguments[0]"
            finally:
                identity_session.detach()
            checkpoint("prepare-owned-page")
            context = browser.new_context(viewport={"width": 900, "height": 700}, reduced_motion="reduce")
            page = context.new_page()
            page.set_default_timeout(3_000)
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.set_content(HTML, wait_until="load")
            page.evaluate("rows=>{document.getElementById('items').innerHTML=Array.from({length:rows},(_,i)=>'<article class=\"entry\"><strong>Probe '+i+'</strong></article>').join('');document.body.offsetHeight}", ROWS)
            checkpoint("start-tracing")
            if arm == "playwright-helper":
                browser.start_tracing(page=page, screenshots=False, categories=list(CATEGORIES))
            else:
                session = context.new_cdp_session(page)

                def on_data(batch):
                    incoming = batch.get("value", [])
                    remaining = TRACE_EVENT_LIMIT - len(events)
                    events.extend(incoming[:remaining])
                    if len(incoming) > remaining:
                        state["overflow"] = True

                def on_complete(event):
                    completed.update(event)
                    state["finished"] = True

                session.on("Tracing.dataCollected", on_data)
                session.on("Tracing.tracingComplete", on_complete)
                session.send("Tracing.start", {"categories": ",".join(CATEGORIES),
                    "options": "record-until-full", "transferMode": "ReportEvents"})
            checkpoint("owned-dom-task")
            report["task"] = page.evaluate(TASK, {"rows": ROWS, "passes": PASSES})
            assert report["task"]["rows"] == ROWS and report["task"]["checksum"] > 0
            checkpoint("stop-tracing")
            if arm == "playwright-helper":
                raw = browser.stop_tracing()
                trace = json.loads(raw)
                report["transportCompleted"] = True
            else:
                session.send("Tracing.end")
                deadline = time.monotonic() + TRACE_STOP_SECONDS
                while not state["finished"] and time.monotonic() < deadline:
                    page.wait_for_timeout(20)
                trace = {"traceEvents": events, "metadata": {
                    "captureFinished": state["finished"], "completion": completed,
                    "retentionOverflow": state["overflow"]}}
                report["transportCompleted"] = state["finished"]
                if not state["finished"]:
                    report["errors"].append(f"Page CDP completion absent after {TRACE_STOP_SECONDS}s")
                if completed.get("dataLossOccurred"):
                    report["errors"].append("Chromium reported lost trace data")
                if state["overflow"]:
                    report["errors"].append("Tiny task exceeded the explicit trace retention bound")
            report["pageErrors"] = page_errors
            report["errors"].extend(page_errors)
            checkpoint("close-browser")
            browser.close()
            browser = None
            checkpoint("stop-playwright")
        checkpoint("complete")
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        if message not in report["errors"]:
            report["errors"].append(message)
        report["failurePhase"] = report["phase"]
        checkpoint("failed")
    finally:
        # Preserve partial events if transport end or browser cleanup failed.
        if not trace.get("traceEvents") and events:
            trace = {"traceEvents": events, "metadata": {"partial": True, **state}}
        report["trace"] = retain_trace(output, arm, trace)
        report["summary"] = trace_summary(trace)
        report["errors"].extend(trace_failures(report["summary"]))
        if report["trace"]["uncompressedBytes"] > TRACE_BYTE_LIMIT:
            report["errors"].append("Tiny trace exceeded the 16 MiB diagnostic bound")
        report["elapsedMs"] = round((time.monotonic() - started) * 1000, 1)
        report["ok"] = not report["errors"]
        write_json(report_path, report)
        print(json.dumps({"transport": arm, "ok": report["ok"], "summary": report["summary"], "errors": report["errors"]}), flush=True)
    return 0 if report["ok"] else 1


def worker_command(arm, output, executable_path=None):
    command = [sys.executable, str(Path(__file__).resolve()), "--output", str(output), "--worker", arm]
    if executable_path is not None:
        command.extend(["--executable-path", str(executable_path)])
    return command


def run_bounded_arm(arm, output, executable_path=None):
    report_path = output / f"{arm}.json"
    command = worker_command(arm, output, executable_path)
    with (output / f"{arm}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != "nt",
                                   env=dict(os.environ, DEBUG="pw:browser"))
        timed_out = False
        try:
            code = process.wait(timeout=ARM_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Kill only this owned worker and its owned browser process group.
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=log, stderr=subprocess.STDOUT, timeout=3, check=False)
            code = process.wait(timeout=3)
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {
        "transport": arm, "ok": False, "errors": ["Worker produced no checkpoint"]}
    if timed_out:
        report["ok"] = False
        report.setdefault("errors", []).append(f"Owned tracing worker exceeded {ARM_TIMEOUT_SECONDS}s at {report.get('phase', 'startup')}")
    if code != 0 and not report.get("errors"):
        report["ok"] = False
        report.setdefault("errors", []).append(f"Worker exited {code}")
    report["workerExitCode"] = code
    report["hardTimeoutSeconds"] = ARM_TIMEOUT_SECONDS
    write_json(report_path, report)
    return report


def self_test():
    trace = {"traceEvents": [
        {"name": "modhunter-trace-probe-start"}, {"name": "Layout"},
        {"name": "SelectorStats", "args": {"selector_stats": {"selector_timings": [
            {"selector": ".entry"}, {"selector": "body.wide .entry"}]}}},
        {"name": "modhunter-trace-probe-end"},
    ]}
    summary = trace_summary(trace)
    assert summary["traceEvents"] == 4 and summary["uniqueSelectors"] == 2
    assert not trace_failures(summary)
    assert len(trace_failures(trace_summary({"traceEvents": []}))) == 4
    del trace["traceEvents"][-1]
    assert any("markers" in error for error in trace_failures(trace_summary(trace)))
    executable = Path(sys.executable).resolve()
    assert worker_command(ARMS[0], ROOT, executable)[-2:] == ["--executable-path", str(executable)]
    assert "--executable-path" not in worker_command(ARMS[0], ROOT)
    print("Trace preflight summary, incomplete-trace rejection and executable propagation checks passed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "native/target/trace-probe")
    parser.add_argument("--executable-path", type=Path,
                        help="Use this Chromium executable; omit to keep Playwright's bundled-browser default")
    parser.add_argument("--transport", choices=ARMS,
                        help="Probe only this required transport; omit to compare both")
    parser.add_argument("--worker", choices=ARMS, help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    executable_path = args.executable_path.expanduser().resolve() if args.executable_path else None
    if executable_path is not None and not executable_path.is_file():
        parser.error(f"Chromium executable does not exist or is not a file: {executable_path}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return worker(args.worker, output, executable_path)
    report = {"schemaVersion": 1, "ok": False,
              "purpose": "Tiny tracing transport preflight, not a performance benchmark or release gate",
              "python": platform.python_version(), "playwright": importlib.metadata.version("playwright"),
              "requestedExecutable": str(executable_path) if executable_path else None,
              "browserSelection": "explicit" if executable_path else "playwright-default",
              "selectedTransports": [args.transport] if args.transport else list(ARMS),
              "categories": list(CATEGORIES), "rows": ROWS, "passes": PASSES,
              "browserLaunchTimeoutSeconds": BROWSER_LAUNCH_SECONDS, "startupDebug": "pw:browser",
              "workerDeadlineSeconds": ARM_TIMEOUT_SECONDS, "arms": []}
    for arm in report["selectedTransports"]:
        print(f"TRACE_PROBE {arm} starting (hard deadline {ARM_TIMEOUT_SECONDS}s)", flush=True)
        report["arms"].append(run_bounded_arm(arm, output, executable_path))
        write_json(output / "trace-probe.json", report)
    report["ok"] = all(arm["ok"] for arm in report["arms"])
    report["workingTransports"] = [arm["transport"] for arm in report["arms"] if arm["ok"]]
    write_json(output / "trace-probe.json", report)
    print(json.dumps({"ok": report["ok"], "workingTransports": report["workingTransports"],
        "arms": [{"transport": arm["transport"], "ok": arm["ok"], "summary": arm.get("summary"),
                  "browserVersion": arm.get("browserVersion"), "resolvedExecutable": arm.get("resolvedExecutable"),
                  "errors": arm.get("errors")} for arm in report["arms"]]}), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
