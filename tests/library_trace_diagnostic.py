"""Attribute unchanged full-library rendering work; traced times are not benchmarks."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import visual_release_qa as engine  # noqa: E402

RELEASED = "f96b86969ec9f9c7ba6928284f16223b673d33c0"
UNCHANGED = "3eae6a4f59b3b471d9a899ac253d9ba175a880b1"
MODS = 11207
# Same categories used by DevTools for selector statistics and invalidation
# attribution. No screenshot/paint picture or CPU profiler sampling is needed.
CATEGORIES = [
    "blink.console", "blink.user_timing", "loading", "devtools.timeline",
    "disabled-by-default-devtools.timeline", "disabled-by-default-devtools.timeline.frame",
    "disabled-by-default-devtools.timeline.stack", "disabled-by-default-blink.debug",
    "disabled-by-default-devtools.timeline.invalidationTracking", "v8", "v8.execute", "cppgc",
]
REFERENCES = [
    "https://developer.chrome.com/docs/devtools/performance/selector-stats",
    "https://github.com/ChromeDevTools/devtools-frontend/blob/main/front_end/models/trace/types/TraceEvents.ts",
    "https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/Tracing.pdl",
]
IMPORT = """async fixture=>{window.modhunterImportCatalog(fixture);
 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));}"""
STATE = """() => {
 const rows=[...document.querySelectorAll('#modList .mod-row')];
 return {rows:rows.length,ids:rows.map(row=>row.dataset.id),
  allLaidOut:rows.every(row=>row.getClientRects().length>0),
  noContentVisibility:rows.every(row=>getComputedStyle(row).contentVisibility==='visible'),
  sameNodes:!window.__TRACE_ROWS__||window.__TRACE_ROWS__.every(row=>document.getElementById(row.id)===row),
  libraryNodes:document.getElementById('modList').querySelectorAll('*').length,
  workspace:document.body.dataset.workspace,view:document.body.dataset.libraryView,
  density:document.body.dataset.density,viewport:{width:innerWidth,height:innerHeight},
  libraryViewport:document.getElementById('libraryViewport')?.getBoundingClientRect().toJSON(),
  first:rows[0]?.getBoundingClientRect().toJSON(),last:rows.at(-1)?.getBoundingClientRect().toJSON()};
}"""


def sha(data):
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def write_json(path, value):
    data = json.dumps(value, indent=2).encode("utf-8")
    path.write_bytes(data)
    return {"file": path.name, "bytes": len(data), "sha256": sha(data)}


def summarize_trace(trace):
    """Retain every aggregate and every recalc/layout record; raw events stay separate."""
    events = trace["traceEvents"]
    named = Counter(event.get("name", "") for event in events)
    recalcs = [event for event in events if event.get("name") == "UpdateLayoutTree"]
    layouts = [event for event in events if event.get("name") == "Layout"]
    stats = [event for event in events if event.get("name") == "SelectorStats"]
    selectors = {}
    for event in stats:
        for timing in event.get("args", {}).get("selector_stats", {}).get("selector_timings", []):
            key = (timing["style_sheet_id"], timing["selector"])
            entry = selectors.setdefault(key, {"styleSheetId": key[0], "selector": key[1],
                "elapsedUs": 0, "matchAttempts": 0, "matchCount": 0, "fastRejectCount": 0, "invalidationCount": 0})
            for source, dest in (("elapsed (us)", "elapsedUs"), ("match_attempts", "matchAttempts"),
                                 ("match_count", "matchCount"), ("fast_reject_count", "fastRejectCount"),
                                 ("invalidation_count", "invalidationCount")):
                entry[dest] += timing.get(source, 0)
    invalidations = Counter()
    for event in events:
        if "InvalidationTracking" not in event.get("name", ""):
            continue
        data = event.get("args", {}).get("data", {})
        # Do not collapse different reasons, attributes, classes or initiating
        # stacks. Backend node IDs remain available in the complete raw trace.
        detail = {key: data[key] for key in ("reason", "nodeName", "changedAttribute", "changedClass",
                  "changedId", "subtree", "extraData", "stackTrace", "selectors") if key in data}
        invalidations[(event["name"], json.dumps(detail, sort_keys=True))] += 1
    return {"eventCount": len(events), "eventCounts": dict(named),
        "recalculations": recalcs, "layouts": layouts, "selectorStatsEvents": stats,
        "selectors": sorted(selectors.values(), key=lambda row: row["elapsedUs"], reverse=True),
        "invalidations": [{"event": name, "count": count, **json.loads(detail)}
                          for (name, detail), count in invalidations.most_common()],
        "attributionOnly": True}


def capture_trace(browser_cdp, page, action, output, label):
    complete = []
    def ended(event):
        complete.append(event)
    browser_cdp.on("Tracing.tracingComplete", ended)
    error = None
    try:
        browser_cdp.send("Tracing.start", {"transferMode": "ReturnAsStream", "streamFormat": "json",
            "streamCompression": "gzip", "traceConfig": {"recordMode": "recordAsMuchAsPossible",
                "traceBufferSizeInKb": 262144, "includedCategories": CATEGORIES, "excludedCategories": ["*"]}})
        started = time.perf_counter()
        try:
            page.evaluate("label=>performance.mark(label+'-start')", label)
            action()
            page.evaluate("label=>performance.mark(label+'-end')", label)
        except BaseException as caught:
            error = caught
        finally:
            instrumented_ms = (time.perf_counter() - started) * 1000
            browser_cdp.send("Tracing.end")
        deadline = time.monotonic() + 60
        while not complete and time.monotonic() < deadline:
            page.wait_for_timeout(50)  # Pump CDP completion; outside measured app work.
        assert complete and complete[0].get("stream"), "Chromium did not deliver its complete trace stream"
        metadata = complete[0]
        stream = metadata["stream"]
        path = output / (label + ".trace.json.gz")
        try:
            with path.open("wb") as target:
                while True:
                    part = browser_cdp.send("IO.read", {"handle": stream, "size": 1048576})
                    target.write(base64.b64decode(part["data"]) if part.get("base64Encoded") else part["data"].encode("utf-8"))
                    if part["eof"]:
                        break
        finally:
            browser_cdp.send("IO.close", {"handle": stream})
        raw = path.read_bytes()
        trace = json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)
        report = {"rawTrace": {"file": path.name, "bytes": len(raw), "sha256": sha(raw)},
            "tracingComplete": metadata, "instrumentedActionMs": instrumented_ms,
            "summary": summarize_trace(trace), "actionError": str(error) if error else None}
        write_json(output / (label + ".trace-summary.json"), report)
        assert metadata["dataLossOccurred"] is False, "Chromium reports lost trace data"
        assert error is None, str(error)
        for required in ("SelectorStats", "UpdateLayoutTree", "Layout"):
            assert report["summary"]["eventCounts"].get(required, 0) > 0, "Missing actual " + required + " events"
        assert report["summary"]["selectors"], "SelectorStats contained no selector timings"
        return report
    finally:
        browser_cdp.remove_listener("Tracing.tracingComplete", ended)


def map_stylesheets(cdp, headers, output, label):
    mappings = []
    for index, header in enumerate(headers.values()):
        entry = {"header": header}
        try:
            text = cdp.send("CSS.getStyleSheetText", {"styleSheetId": header["styleSheetId"]})["text"]
            path = output / f"{label}.stylesheet-{index}.css"
            path.write_text(text, encoding="utf-8")
            entry["source"] = {"file": path.name, "bytes": len(text.encode("utf-8")), "sha256": sha(text)}
        except Exception as error:
            # Removed stylesheets can be absent after the action. Keep their
            # exact header and explicit unresolved status instead of guessing.
            entry["unresolved"] = str(error)
        mappings.append(entry)
    return mappings


def run_probe(browser, browser_cdp, html, fixture, output, label, phase):
    from playwright.sync_api import expect
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(60000)
    cdp = context.new_cdp_session(page)
    headers, network, requests, errors = {}, [], {}, []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cdp.on("CSS.styleSheetAdded", lambda event: headers.update({event["header"]["styleSheetId"]: event["header"]}))
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    clock = time.perf_counter()
    def began(request):
        row = {"url": request.url, "method": request.method, "resourceType": request.resource_type,
               "startMs": (time.perf_counter() - clock) * 1000, "status": "pending"}
        requests[id(request)] = row
        network.append(row)
    def finished(request):
        row = requests[id(request)]
        row.update(status="finished", endMs=(time.perf_counter() - clock) * 1000)
    def responded(response):
        requests[id(response.request)]["httpStatus"] = response.status
    def failed(request):
        requests[id(request)].update(status="failed", failure=request.failure,
                                     endMs=(time.perf_counter() - clock) * 1000)
    page.on("request", began)
    page.on("response", responded)
    page.on("requestfinished", finished)
    page.on("requestfailed", failed)
    report = {"label": label, "phase": phase, "network": network, "pageErrors": errors,
              "scope": "Instrumented attribution only; startup/preparation/trace export are separate from app action"}
    try:
        print("TRACE " + label + " page-startup-start (untraced)", flush=True)
        page.set_content(html, wait_until="load")
        assert page.evaluate("!!window.__ARSENAL_QA__")
        report["pageStartupMs"] = (time.perf_counter() - clock) * 1000
        report["networkPendingAfterStartup"] = [row["url"] for row in network if row["status"] == "pending"]
        print("TRACE " + label + " page-startup-complete " + json.dumps({key: report[key] for key in (
            "pageStartupMs", "networkPendingAfterStartup")}), flush=True)
        prepared = time.perf_counter()
        if phase == "import":
            action = lambda: page.evaluate(IMPORT, fixture)
        else:
            # Identical preparation/order to the existing benchmark before its
            # Updates -> Library interval. The production source is untouched.
            page.evaluate(IMPORT, fixture)
            page.evaluate("window.__TRACE_ROWS__=[...document.querySelectorAll('#modList .mod-row')]")
            page.locator("#search").fill(f"Unique Fixture {MODS-1}")
            expect(page.locator("#modList .mod-row")).to_have_count(1, timeout=60000)
            assert page.locator("#modList .mod-row").get_attribute("data-id") == f"item-{MODS-1}"
            page.locator("#search").fill("")
            expect(page.locator("#modList .mod-row")).to_have_count(MODS, timeout=60000)
            page.locator("#sortSelect").select_option("name"); engine.settle(page)
            page.locator("#sortSelect").select_option("priority"); engine.settle(page)
            page.locator('.workspace-tabs [data-workspace="updates"]').click(); engine.settle(page)
            assert page.locator("body").get_attribute("data-workspace") == "updates"
            def action():
                page.locator('.workspace-tabs [data-workspace="mods"]').click()
                engine.settle(page)
        report["preparationMs"] = (time.perf_counter() - prepared) * 1000
        report["networkPendingBeforeTrace"] = [row["url"] for row in network if row["status"] == "pending"]
        print("TRACE " + label + " action-start " + json.dumps({key: report[key] for key in (
            "pageStartupMs", "preparationMs", "networkPendingBeforeTrace")}), flush=True)
        report["trace"] = capture_trace(browser_cdp, page, action, output, label)
        report["state"] = page.evaluate(STATE)
        state = report["state"]
        assert state["rows"] == MODS and state["ids"] == [f"item-{i}" for i in range(MODS)], state
        assert state["allLaidOut"] and state["noContentVisibility"] and state["sameNodes"], state
        assert state["workspace"] == "mods" and state["view"] == "list", state
        report["stylesheets"] = map_stylesheets(cdp, headers, output, label)
        resolved = {row["header"]["styleSheetId"] for row in report["stylesheets"] if "source" in row}
        used = {row["styleSheetId"] for row in report["trace"]["summary"]["selectors"]}
        # Chromium's IdentifiersFactory::IdForCSSStyleSheet(nullptr) explicitly
        # names built-in browser rules "ua-style-sheet". They have no authored
        # stylesheet source; every other used ID must resolve to exact text.
        report["browserStyleSheetIds"] = sorted(used & {"ua-style-sheet"})
        report["unmappedSelectorStyleSheetIds"] = sorted(used - resolved - {"ua-style-sheet"})
        assert not report["unmappedSelectorStyleSheetIds"], report["unmappedSelectorStyleSheetIds"]
        snapshot = cdp.send("DOMSnapshot.captureSnapshot", {"computedStyles": []})
        snapshot_path = output / (label + ".dom-snapshot.json.gz")
        snapshot_path.write_bytes(gzip.compress(json.dumps(snapshot).encode("utf-8"), mtime=0))
        report["domSnapshot"] = {"file": snapshot_path.name, "sha256": sha(snapshot_path.read_bytes()),
                                 "documents": len(snapshot["documents"])}
        image = page.screenshot(path=str(output / (label + ".png")))
        report["screenshot"] = {"file": label + ".png", "sha256": sha(image)}
        assert not errors, errors
        report["ok"] = True
        print("TRACE " + label + " complete: full rows, actual selectors/layout, zero trace loss", flush=True)
        return report
    except BaseException as error:
        report["ok"] = False
        report["error"] = str(error)
        if "pageStartupMs" not in report:
            report["failedStartupMs"] = (time.perf_counter() - clock) * 1000
        raise
    finally:
        if "stylesheets" not in report:
            report["stylesheets"] = map_stylesheets(cdp, headers, output, label)
        write_json(output / (label + ".json"), report)
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    refs = {"v0121": RELEASED, "current013": UNCHANGED}
    compiled = {name: engine.compile_dashboard(ref) for name, ref in refs.items()}
    identities = {name: {"sourceCommit": refs[name], "sourceSha256": sources, "compiledHtmlSha256": sha(html)}
                  for name, (html, sources) in compiled.items()}
    fixture = engine.dashboard.catalog(count=MODS)
    fixture["offline"] = True
    fixture_sha = sha(json.dumps(fixture, sort_keys=True))
    if args.check_inputs:
        print(json.dumps({"browserExecuted": False, "variants": identities, "fixtureSha256": fixture_sha,
                          "mods": MODS, "traceCategories": CATEGORIES}, indent=2))
        return
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    status = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert not status, "Trace diagnostic source must be committed and clean"
    proof = {"schema": 1, "kind": "library-style-trace-diagnostic", "ok": False,
        "scope": "Instrumented attribution only; no benchmark medians, candidate or production changes",
        "proofCommit": git("rev-parse", "HEAD"), "sourceStatusAtStart": status, "platform": platform.platform(),
        "variants": identities, "fixtureSha256": fixture_sha, "mods": MODS, "references": REFERENCES,
        "traceCategories": CATEGORIES, "traces": {},
        "engine": {"path": "tests/visual_release_qa.py", "sha256": sha(Path(engine.__file__).read_text("utf-8"))},
        "diagnosticSourceSha256": {path: sha((ROOT / path).read_bytes()) for path in (
            "tests/library_trace_diagnostic.py", "tests/test_library_trace_diagnostic.py",
            "tests/test_frontend_responsiveness.py", ".github/workflows/library-trace-diagnostic.yml")}}
    def save():
        write_json(output / "library-trace-diagnostic.json", proof)
    for name, (html, sources) in compiled.items():
        (output / (name + ".html")).write_text(html, encoding="utf-8")
        for path in sources:
            target = output / "source" / name / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(engine.read(path, refs[name]), encoding="utf-8")
    save()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            launch["executable_path"] = executable
        print("TRACE exact source identity " + json.dumps({name: {"sourceCommit": value["sourceCommit"],
            "compiledHtmlSha256": value["compiledHtmlSha256"]} for name, value in identities.items()}), flush=True)
        print("TRACE browser-launch-start (untraced)", flush=True)
        started = time.perf_counter()
        browser = runtime.chromium.launch(**launch)
        proof["browserLaunchMs"] = (time.perf_counter() - started) * 1000
        proof["browserExecutable"] = executable or "Playwright bundled Chromium"
        cdp = browser.new_browser_cdp_session()
        proof["browser"] = cdp.send("Browser.getVersion")
        print("TRACE browser-launch-complete " + json.dumps({"browserLaunchMs": proof["browserLaunchMs"],
            "browser": proof["browser"]}), flush=True)
        proof["availableTraceCategories"] = cdp.send("Tracing.getCategories")["categories"]
        save()
        try:
            for phase in ("import", "library-return"):
                for name in ("v0121", "current013"):
                    label = name + "-" + phase
                    proof["activeTrace"] = label
                    save()
                    proof["traces"][label] = run_probe(browser, cdp, compiled[name][0], fixture, output, label, phase)
                    save()
            assert len(proof["traces"]) == 4 and all(row["ok"] for row in proof["traces"].values())
            assert git("rev-parse", "HEAD") == proof["proofCommit"]
            assert not git("status", "--porcelain", "--untracked-files=all")
            proof.pop("activeTrace", None)
            proof["ok"] = True
        except BaseException as error:
            proof["error"] = str(error)
            raise
        finally:
            browser.close()
            proof["sourceStatusAtEnd"] = git("status", "--porcelain", "--untracked-files=all").splitlines()
            if proof["sourceStatusAtEnd"]:
                proof["ok"] = False
            save()
    print(json.dumps({"ok": proof["ok"], "receipt": str(output / "library-trace-diagnostic.json"),
                      "traceCount": len(proof["traces"]), "attributionOnly": True}, indent=2))


if __name__ == "__main__":
    main()
