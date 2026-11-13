"""Compare actual Chromium rendering over the same complete synthetic mod list.

Only app.js differs between variants; all other assets and fixture records are
identical. Measures favorite-click and ticket-render CPU/layout time, DOM churn
and retained focus. It does not measure MO2, Nexus, downloads or GUI startup.
Requires Playwright plus a working Chromium installation. No browser is skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_frontend_responsiveness import build_page, catalog  # noqa: E402

DEFAULT_BASELINE = "63e5df3e3ef218ae72ce58b5e4502165c40ca604"
APP_PATH = "mo2_update_arsenal/web/app.js"
INSTRUMENT = """
window.__RENDER_BENCHMARK__={ticket:(itemId,status)=>{
 WORKSHOP_TICKETS={'benchmark':{itemId,status,installedVersion:'1.0',targetVersion:'2.0'}};
 renderWorkshop();
}};
"""
WORKLOAD = r"""({kind,itemId,status})=>{
 const host=document.getElementById(kind==='favorite'?'modList':'workshopResults');
 const selector=kind==='favorite'?'.mod-row':'.workshop-item';
 const before=[...host.querySelectorAll(selector)],identities=new Set(before);
 const link=before[0].querySelector(kind==='favorite'?'.source-title':'.workshop-item-title');
 link.focus();
 const observer=new MutationObserver(()=>{});observer.observe(host,{childList:true});
 const button=kind==='favorite'?document.querySelector('#row-'+itemId+' [data-act="favorite"]'):null;
 const wasFavorite=button?.classList.contains('active');
 const start=performance.now();
 if(button)button.click();else __RENDER_BENCHMARK__.ticket(itemId,status);
 const rendered=performance.now();
 void host.offsetHeight;
 const laidOut=performance.now();
 const changes=observer.takeRecords();observer.disconnect();
 const after=[...host.querySelectorAll(selector)];
 return {
  handlerMs:rendered-start,handlerAndLayoutMs:laidOut-start,
  mutationRecords:changes.length,
  addedElements:changes.reduce((sum,r)=>sum+[...r.addedNodes].filter(n=>n.nodeType===1).length,0),
  removedElements:changes.reduce((sum,r)=>sum+[...r.removedNodes].filter(n=>n.nodeType===1).length,0),
  rowsBefore:before.length,rowsAfter:after.length,retainedRows:after.filter(n=>identities.has(n)).length,
  unrelatedFocusPreserved:document.activeElement===link,
  actionVerified:kind==='favorite'
   ? document.querySelector('#row-'+itemId+' .favorite-btn').classList.contains('active')!==wasFavorite
   : document.querySelector('[data-workshop-id="'+itemId+'"] .workshop-ticket-note')?.textContent.includes(status.toUpperCase())===true
 };
}"""


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def instrumented_page(source: str, current: str, shared_page: str) -> str:
    prepared = source.replace("/*__POWERSHELL__*/", json.dumps(""))
    prepared, count = re.subn(r"\}\)\(\);\s*$", lambda _: INSTRUMENT + "\n})();", prepared)
    if count != 1:
        raise ValueError("Cannot locate the actual app closure for equal benchmark instrumentation")
    needle = current.replace("/*__POWERSHELL__*/", json.dumps(""))
    if shared_page.count(needle) != 1:
        raise ValueError("Cannot locate the exact app source in the in-memory dashboard")
    return shared_page.replace(needle, prepared, 1)


def summarize(samples: list[dict]) -> dict:
    return {"samples": samples,
            "medianHandlerMs": statistics.median(s["handlerMs"] for s in samples),
            "medianHandlerAndLayoutMs": statistics.median(s["handlerAndLayoutMs"] for s in samples),
            "medianAddedElements": statistics.median(s["addedElements"] for s in samples),
            "medianRemovedElements": statistics.median(s["removedElements"] for s in samples),
            "medianRetainedRows": statistics.median(s["retainedRows"] for s in samples),
            "allActionsVerified": all(s["actionVerified"] for s in samples),
            "allUnrelatedFocusPreserved": all(s["unrelatedFocusPreserved"] for s in samples)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=DEFAULT_BASELINE)
    parser.add_argument("--baseline-app", type=Path, help="Use an already obtained baseline app.js without git retrieval")
    parser.add_argument("--mods", type=int, default=2500)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 10 <= args.mods <= 10000 or not 3 <= args.rounds <= 25:
        parser.error("--mods must be 10..10000 and --rounds must be 3..25")
    from playwright.sync_api import sync_playwright

    current = (ROOT / APP_PATH).read_text("utf-8")
    baseline = args.baseline_app.read_text("utf-8") if args.baseline_app else subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:{APP_PATH}"], cwd=ROOT, text=True)
    shared = build_page()
    fixture = catalog(count=args.mods)
    fixture["offline"] = True
    sources = {"baseline": baseline, "candidate": current}
    samples = {name: {kind: [] for kind in ("favorite", "ticket")} for name in sources}
    result = {"schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
              "method": "Same complete synthetic catalog and shared dashboard assets; only app.js differs. Two warmups per workload, then alternating variant order. CPU handler and forced-layout time exclude page startup, transport and fixture import.",
              "platform": platform.platform(), "mods": args.mods, "rounds": args.rounds,
              "candidateCommit": git("rev-parse", "HEAD"),
              "baselineCommit": None if args.baseline_app else git("rev-parse", f"{args.baseline_ref}^{{commit}}"),
              "fixtureSha256": digest(json.dumps(fixture, sort_keys=True)),
              "variants": {name: {"appSha256": digest(source)} for name, source in sources.items()}}
    with sync_playwright() as runtime:
        browser_args = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            browser_args["executable_path"] = executable
        browser = runtime.chromium.launch(**browser_args)
        result["browserVersion"] = browser.version
        pages, contexts, errors = {}, [], []
        try:
            for name, source in sources.items():
                context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                contexts.append(context)
                page = context.new_page()
                page.on("pageerror", lambda error, variant=name: errors.append(f"{variant}: {error}"))
                html = instrumented_page(source, current, shared)
                result["variants"][name]["dashboardSha256"] = digest(html)
                page.set_content(html, wait_until="domcontentloaded")
                page.evaluate("c=>window.modhunterImportCatalog(c)", fixture)
                page.wait_for_function("n=>document.querySelectorAll('#workshopResults .workshop-item').length===n", arg=args.mods)
                pages[name] = page
            for kind in ("favorite", "ticket"):
                for iteration in range(args.rounds + 2):
                    order = ("baseline", "candidate") if iteration % 2 == 0 else ("candidate", "baseline")
                    for name in order:
                        sample = pages[name].evaluate(WORKLOAD, {"kind": kind, "itemId": f"item-{args.mods-1}",
                            "status": "downloading" if iteration % 2 == 0 else "complete"})
                        if sample["rowsBefore"] != args.mods or sample["rowsAfter"] != args.mods or not sample["actionVerified"]:
                            raise AssertionError(f"{name}/{kind} lost records or did not perform the action: {sample}")
                        if name == "candidate" and (sample["retainedRows"] != args.mods-1 or not sample["unrelatedFocusPreserved"]):
                            raise AssertionError(f"Candidate did not preserve unrelated rows/focus: {sample}")
                        if iteration >= 2:
                            samples[name][kind].append(sample)
            if errors:
                raise AssertionError("Browser errors: " + "; ".join(errors))
        finally:
            for context in contexts:
                context.close()
            browser.close()
    for name in sources:
        result["variants"][name]["workloads"] = {kind: summarize(values) for kind, values in samples[name].items()}
    result["comparison"] = {}
    for kind in ("favorite", "ticket"):
        old = result["variants"]["baseline"]["workloads"][kind]
        new = result["variants"]["candidate"]["workloads"][kind]
        result["comparison"][kind] = {"handlerSpeedup": old["medianHandlerMs"] / max(new["medianHandlerMs"], 0.001),
            "handlerAndLayoutSpeedup": old["medianHandlerAndLayoutMs"] / max(new["medianHandlerAndLayoutMs"], 0.001)}
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, "utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
