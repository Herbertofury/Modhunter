"""Diagnose full-library CSS work without changing release acceptance timings.

Every arm uses the same complete 11,207-record fixture and shipped source. The
optional CSS experiments exist only in fresh diagnostic page contexts. Ordinary
click timings and instrumented selector traces are separate runs: selector
statistics add overhead and are not performance acceptance measurements.

Chromium's SelectorStatisticsFlag and ElementRuleCollector gate SelectorStats on
disabled-by-default-blink.debug. DevTools uses the matching invalidation category.
Sources: https://chromium.googlesource.com/chromium/src/+/refs/heads/main/third_party/blink/renderer/core/css/selector_statistics_flag.cc
https://chromium.googlesource.com/chromium/src/+/refs/heads/main/third_party/blink/renderer/core/css/element_rule_collector.cc
https://developer.chrome.com/docs/devtools/performance/selector-stats
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time

import visual_release_qa as acceptance

ROOT = acceptance.ROOT
METRICS = ("ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "TaskDuration",
           "LayoutCount", "RecalcStyleCount", "Nodes", "JSHeapUsedSize")
CATEGORIES = ("devtools.timeline", "disabled-by-default-devtools.timeline",
              "disabled-by-default-blink.debug", "blink.user_timing")
TRACE_CONFIG = {"recordMode": "recordUntilFull", "traceBufferSizeInKb": 512 * 1024,
                "includedCategories": list(CATEGORIES), "excludedCategories": ["*"]}
RETAINED_TRACE_LIMIT = 256 * 1024 * 1024
ACTIONS = {"gallery": "#libraryGallery", "density": "#densityToggle", "list": "#libraryList"}

# These are approved, isolated experiments, not changes to the shipped cascade.
VARIANTS = {
    "control": "() => ({changedRules:0,description:'Unchanged shipped CSS'})",
    "no-zebra": """() => {
      let changedRules=0;
      const walk=sheet=>{for(let index=sheet.cssRules.length-1;index>=0;index--){
        const rule=sheet.cssRules[index];
        if(rule.selectorText==='.mod-row:nth-child(2n)'){sheet.deleteRule(index);changedRules++;}
        else if(rule.cssRules)walk(rule);
      }};
      for(const sheet of document.styleSheets)walk(sheet);
      if(changedRules!==1)throw Error('Expected exactly one legacy zebra rule, got '+changedRules);
      return {changedRules,description:'Remove only overridden .mod-row:nth-child(2n) rule'};
    }""",
    "svg-background": """() => {
      const style=document.createElement('style');style.dataset.diagnosticVariant='svg-background';
      style.textContent=`body[data-layout="compact"] .mod-art::before {
        mask:none!important;
        background:transparent url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238c9c92' stroke-width='1.25'%3E%3Crect x='3' y='3' width='18' height='18' rx='2'/%3E%3Cpath d='m3 16 5-5 4 4 3-3 6 6'/%3E%3Ccircle cx='16' cy='8' r='1.5'/%3E%3C/svg%3E") center/contain no-repeat!important;
      }`;
      document.head.append(style);
      return {changedRules:1,description:'Same placeholder drawing as SVG background; exact dark faint colour'};
    }""",
    "scoped-state": r"""() => {
      let changedRules=0;
      const changedSelectors=[];
      const walk=sheet=>{for(const rule of sheet.cssRules){
        if(rule.selectorText){
          const before=rule.selectorText;
          const after=before.replace(/body((?:\[[^\]]+\])*)/g,(whole,attributes)=>
            /data-(library-view|density|gallery-grouping)/.test(attributes)
              ?'section:where(#workbench)'+attributes:whole);
          if(before!==after){rule.selectorText=after;changedRules++;changedSelectors.push({before,after});}
        }
        if(rule.cssRules)walk(rule);
      }};
      for(const sheet of document.styleSheets)walk(sheet);
      const workbench=document.getElementById('workbench');
      if(!workbench||workbench.tagName!=='SECTION')throw Error('Expected section#workbench');
      const attributes=['data-layout','data-theme','data-library-view','data-density','data-gallery-grouping'];
      const mirror=()=>{for(const name of attributes){const value=document.body.getAttribute(name);
        if(value===null)workbench.removeAttribute(name);else if(workbench.getAttribute(name)!==value)workbench.setAttribute(name,value);}};
      mirror();
      const observer=new MutationObserver(mirror);
      observer.observe(document.body,{attributes:true,attributeFilter:attributes});
      window.__STYLE_DIAGNOSTIC_STATE_MIRROR__=observer;
      if(!changedRules)throw Error('No state selectors transformed');
      return {changedRules,changedSelectors,description:'Localize view/density/group selectors to the actual workbench; preserve specificity'};
    }""",
    "cssom-geometry": """() => {
      const list=document.getElementById('modList');
      if(!list)throw Error('Expected #modList');
      const original=CSSStyleDeclaration.prototype.setProperty;
      let columns=Number.parseFloat(list.style.getPropertyValue('--library-columns'))||6;
      let gap=Number.parseFloat(list.style.getPropertyValue('--library-gap-total'))||70;
      const style=document.createElement('style');style.dataset.diagnosticVariant='cssom-geometry';
      document.head.append(style);
      style.sheet.insertRule('body[data-layout="compact"][data-library-view="gallery"] #modList > .mod-row {flex-basis:calc((100% - 70px) / 6)}');
      const rule=style.sheet.cssRules[0];let previous='';
      const update=()=>{const value=`calc((100% - ${gap}px) / ${columns})`;
        if(value!==previous){original.call(rule.style,'flex-basis',value);previous=value;}};
      update();
      list.style.removeProperty('--library-columns');list.style.removeProperty('--library-gap-total');
      CSSStyleDeclaration.prototype.setProperty=function(name,value,priority){
        if(this===list.style&&(name==='--library-columns'||name==='--library-gap-total')){
          const number=Number.parseFloat(value);
          if(!Number.isFinite(number)||(name==='--library-columns'&&number<1))throw Error('Invalid gallery geometry');
          if(name==='--library-columns')columns=number;else gap=number;
          update();return;
        }
        return original.call(this,name,value,priority);
      };
      return {changedRules:1,description:'Preserve actual gallery sizing through one CSSOM flex-basis rule, avoiding two inherited custom-property writes'};
    }""",
    "persistent-flex": """() => {
      const style=document.createElement('style');style.dataset.diagnosticVariant='persistent-flex';
      style.textContent=`body[data-layout="compact"][data-library-view="list"] #modList{
        display:flex;flex-wrap:wrap;gap:0;align-items:flex-start;align-content:flex-start}
        body[data-layout="compact"][data-library-view="list"] #modList>:is(.mod-row,.chapterbar,.empty-state){
        flex:0 0 100%;width:100%}`;
      document.head.append(style);
      return {changedRules:2,description:'Keep the library a flex container in List and Gallery; List children remain full width with zero gap. Gallery declarations are unchanged.'};
    }""",
    "no-universal-scrollbar": r"""() => {
      let changedRules=0;const selectors=[];
      const walk=sheet=>{for(let index=sheet.cssRules.length-1;index>=0;index--){
        const rule=sheet.cssRules[index];
        if(rule.selectorText&&/^\*?::-webkit-scrollbar(?:-thumb|-track)?$/.test(rule.selectorText)){
          selectors.push(rule.selectorText);sheet.deleteRule(index);changedRules++;
        }else if(rule.cssRules)walk(rule);
      }};
      for(const sheet of document.styleSheets)walk(sheet);
      if(changedRules!==3)throw Error('Expected exactly three universal scrollbar rules, got '+changedRules);
      return {changedRules,selectors,description:'Remove only universal scrollbar rules; retain specific panel scrollbars'};
    }""",
    "overflow-clip": """() => {
      const style=document.createElement('style');style.dataset.diagnosticVariant='overflow-clip';
      style.textContent=`body[data-layout="compact"] .mod-art{overflow:clip!important}
        body[data-layout="compact"][data-library-view="gallery"] .mod-row{overflow:clip!important}`;
      document.head.append(style);
      return {changedRules:2,description:'Keep image/card cropping without scroll containers; preserve title ellipsis and scrollable panels'};
    }""",
    "constant-label-content": r"""() => {
      window.__STYLE_DIAGNOSTIC_AFTER_IMPORT__=()=>{
        delete window.__STYLE_DIAGNOSTIC_AFTER_IMPORT__;
        const rows=[...document.querySelectorAll('#modList > .mod-row')];
        if(rows.length!==11207)throw Error('Expected all 11,207 rows before label experiment');
        const providers=new Set(rows.map(row=>row.querySelector('.mod-art')?.getAttribute('data-provider-label')));
        const chapters=new Set(rows.map(row=>row.querySelector('.mod-primary')?.getAttribute('data-chapter-label')));
        if(providers.size!==1||chapters.size!==1||providers.has(null)||providers.has(undefined)||chapters.has(null)||chapters.has(undefined))
          throw Error('Constant-content experiment requires the actual single-provider/single-chapter fixture');
        const provider=[...providers][0],chapter=[...chapters][0];let providerRules=0,chapterRules=0;
        const walk=sheet=>{for(const rule of sheet.cssRules){
          if(rule.selectorText?.includes('[data-library-view="gallery"]')){
            const content=rule.style.getPropertyValue('content').trim(),priority=rule.style.getPropertyPriority('content');
            if(/\.mod-art::?after\b/.test(rule.selectorText)&&/^attr\(\s*data-provider-label\s*\)$/.test(content)){
              rule.style.setProperty('content',JSON.stringify(provider),priority);providerRules++;
            }
            if(/\.mod-primary::?before\b/.test(rule.selectorText)&&/^attr\(\s*data-chapter-label\s*\)$/.test(content)){
              rule.style.setProperty('content',JSON.stringify(chapter),priority);chapterRules++;
            }
          }
          if(rule.cssRules)walk(rule);
        }};
        for(const sheet of document.styleSheets)walk(sheet);
        if(providerRules!==1||chapterRules!==1)throw Error('Expected exactly one provider and one chapter content rule');
        return {changedRules:providerRules+chapterRules,verifiedRows:rows.length,provider,chapter,
          description:'Replace only gallery attr() labels with the verified identical uniform fixture strings; preserve all DOM and metadata'};
      };
      return {changedRules:0,deferredUntilImport:true,description:'Verify complete uniform fixture before changing two content declarations'};
    }""",
}

VARIANTS["persistent-flex-cssom"] = """() => {
  const flex=(%s)();
  const geometry=(%s)();
  return {changedRules:flex.changedRules+geometry.changedRules,experiments:[flex,geometry],
    description:'Combine independently specified persistent-flex and CSSOM geometry experiments'};
}""" % (VARIANTS["persistent-flex"], VARIANTS["cssom-geometry"])


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def source_status():
    return subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).splitlines()


def metric_snapshot(session):
    return {entry["name"]: entry["value"] for entry in session.send("Performance.getMetrics")["metrics"] if entry["name"] in METRICS}


def complete_geometry(page, count):
    state = page.evaluate("""() => {
      const rows=[...document.querySelectorAll('#modList .mod-row')];
      const rectangle=element=>{const box=element.getBoundingClientRect();
        return [box.left+scrollX,box.top+scrollY,box.width,box.height];};
      return {rows:rows.length,uniqueIds:new Set(rows.map(row=>row.dataset.id)).size,
        firstId:rows[0]?.dataset.id,lastId:rows.at(-1)?.dataset.id,
        allLaidOut:rows.every(row=>row.getClientRects().length>0),
        noContentVisibility:rows.every(row=>getComputedStyle(row).contentVisibility==='visible'),
        documentNodes:document.querySelectorAll('*').length,
        libraryNodes:document.querySelector('#modList').querySelectorAll('*').length,
        listGeometry:rectangle(document.getElementById('modList')),
        rowGeometry:rows.map(row=>[row.dataset.id,...rectangle(row)]),
        chapterGeometry:[...document.querySelectorAll('#modList > .chapterbar')]
          .map((header,index)=>[index,header.textContent.trim(),...rectangle(header)]),
        emptyGeometry:[...document.querySelectorAll('#modList > .empty-state')]
          .map((element,index)=>[index,...rectangle(element)]),
        view:document.body.dataset.libraryView,density:document.body.dataset.density,
        scrollX,scrollY,width:innerWidth,scrollWidth:document.documentElement.scrollWidth};
    }""")
    assert state["rows"] == state["uniqueIds"] == count, state
    assert state["firstId"] == "item-0" and state["lastId"] == f"item-{count-1}", state
    assert state["allLaidOut"] and state["noContentVisibility"], state
    assert state["scrollWidth"] <= state["width"] + 1, state
    return state


def computed_samples(page):
    return page.evaluate("""() => {
      const properties=['display','position','width','height','min-height','color','background-color',
        'border-top-width','border-top-color','border-bottom-width','border-bottom-color',
        'font-family','font-size','line-height','padding-top','padding-right','padding-bottom','padding-left',
        'grid-template-columns','grid-template-rows','flex-basis','flex-grow','flex-shrink','flex-wrap',
        'align-items','align-content','gap','white-space','overflow'];
      const selectors=['#workbench','.library-grouping','#modList','#modList .mod-row',
        '#modList .mod-primary','#modList .mod-title','#modList .mod-art',
        '#modList .mod-version','#modList .inspect-col','#modList .file-download-btn'];
      return Object.fromEntries(selectors.map(selector=>{const element=document.querySelector(selector);
        if(!element)return [selector,null];const style=getComputedStyle(element);
        return [selector,Object.fromEntries(properties.map(property=>[property,style.getPropertyValue(property)]))];}));
    }""")


def compare_geometry(reference, variant):
    """Compare every row/header rectangle, outside the interaction clock.

    Document coordinates keep actual layout comparisons independent of a
    Playwright control's viewport scroll. A tenth of a CSS pixel permits only
    layout-unit rounding, not a changed row height, offset or card column.
    """
    differences = []
    for key in ("rows", "uniqueIds", "firstId", "lastId"):
        if reference.get(key) != variant.get(key):
            differences.append({"field":key,"control":reference.get(key),"variant":variant.get(key)})
    for key in ("listGeometry", "rowGeometry", "chapterGeometry", "emptyGeometry"):
        before, after = reference.get(key, []), variant.get(key, [])
        if key == "listGeometry":
            before, after = [before], [after]
        if len(before) != len(after):
            differences.append({"field":key,"controlCount":len(before),"variantCount":len(after)})
        for index, (left, right) in enumerate(zip(before, after)):
            if len(left) != len(right) or any(
                abs(a-b) > .1 if isinstance(a, (int,float)) and isinstance(b, (int,float)) else a != b
                for a,b in zip(left,right)
            ):
                differences.append({"field":key,"index":index,"control":left,"variant":right})
    return {"comparedRows":len(reference.get("rowGeometry", [])),
            "comparedChapters":len(reference.get("chapterGeometry", [])),
            "toleranceCssPixels":.1,"differenceCount":len(differences),"differences":differences}


def pseudo_label_state(page):
    state = page.evaluate("""() => {
      const rows=[...document.querySelectorAll('#modList > .mod-row')],providers={},chapters={};
      let mismatches=0;const examples=[];
      for(const row of rows){
        const art=row.querySelector('.mod-art'),primary=row.querySelector('.mod-primary');
        const expectedProvider=JSON.stringify(art?.getAttribute('data-provider-label'));
        const expectedChapter=JSON.stringify(primary?.getAttribute('data-chapter-label'));
        const provider=art?getComputedStyle(art,'::after').content:null;
        const chapter=primary?getComputedStyle(primary,'::before').content:null;
        providers[provider]=(providers[provider]||0)+1;chapters[chapter]=(chapters[chapter]||0)+1;
        if(provider!==expectedProvider||chapter!==expectedChapter){mismatches++;
          if(examples.length<3)examples.push({id:row.dataset.id,provider,expectedProvider,chapter,expectedChapter});}
      }
      return {rows:rows.length,mismatches,providerDistribution:providers,chapterDistribution:chapters,examples};
    }""")
    assert state["rows"] == 11207 and not state["mismatches"], f"Gallery pseudo-labels differ from record metadata: {state}"
    return state


def start_root_observer(page):
    # Observe only presentation ancestors, not 200,000+ descendants. This tells
    # us whether a control rewrites unchanged state attributes or styles.
    page.evaluate("""() => {
      window.__STYLE_DIAGNOSTIC_MUTATIONS__=[];
      window.__STYLE_DIAGNOSTIC_OBSERVER__?.disconnect();
      const observer=new MutationObserver(records=>{
        for(const record of records){const target=record.target;
          window.__STYLE_DIAGNOSTIC_MUTATIONS__.push({target:target.id||target.tagName,
            attribute:record.attributeName,oldValue:record.oldValue,
            newValue:target.getAttribute(record.attributeName)});
        }
      });
      for(const target of new Set([document.documentElement,document.body,
        ...document.querySelectorAll('#modList,.shell,.maincol,.bodypad,.workbench')]))
        observer.observe(target,{attributes:true,attributeOldValue:true});
      window.__STYLE_DIAGNOSTIC_OBSERVER__=observer;
    }""")


def stop_root_observer(page):
    return page.evaluate("""() => {
      window.__STYLE_DIAGNOSTIC_OBSERVER__.disconnect();
      const records=window.__STYLE_DIAGNOSTIC_MUTATIONS__;
      return {count:records.length,unchangedWrites:records.filter(row=>row.oldValue===row.newValue).length,records};
    }""")


def summarize_trace(trace):
    """Keep selector timings, invalidation causes, and real style/layout events."""
    selectors = {}
    names = Counter()
    reasons = Counter()
    invalidation_samples = defaultdict(list)
    style_events = []
    selector_events = []
    for event in trace.get("traceEvents", []):
        name = event.get("name", "")
        names[name] += 1
        args = event.get("args", {})
        if name == "SelectorStats":
            timings = args.get("selector_stats", {}).get("selector_timings", [])
            selector_events.append({"ts":event.get("ts"),"pid":event.get("pid"),"tid":event.get("tid"),
                "selectors":len(timings),"elapsedUs":sum(row.get("elapsed (us)",0) for row in timings)})
            for row in timings:
                key = (row.get("selector", ""), row.get("style_sheet_id", ""))
                value = selectors.setdefault(key, {"selector": key[0], "styleSheetId": key[1],
                    "elapsedUs": 0, "matchAttempts": 0, "matches": 0, "fastRejects": 0, "events": 0})
                for dest, source in (("elapsedUs", "elapsed (us)"), ("matchAttempts", "match_attempts"),
                                     ("matches", "match_count"), ("fastRejects", "fast_reject_count")):
                    value[dest] += row.get(source, 0)
                value["events"] += 1
        if "Invalidation" in name:
            data = args.get("data", args)
            reason = str(data.get("reason", data.get("invalidationReason", "unspecified")))
            reasons[(name, reason)] += 1
            if len(invalidation_samples[name]) < 12:
                invalidation_samples[name].append({"ts": event.get("ts"), "args": args})
        if name in ("UpdateLayoutTree", "RecalculateStyles", "Layout"):
            style_events.append({"name": name, "durationUs": event.get("dur", 0),
                "ts": event.get("ts"), "pid": event.get("pid"), "tid": event.get("tid"), "args": args})
    ranked = sorted(selectors.values(), key=lambda row: row["elapsedUs"], reverse=True)
    for row in ranked:
        attempts = row["matchAttempts"]
        row["slowNonMatches"] = max(0, attempts - row["matches"] - row["fastRejects"])
        row["slowNonMatchPercent"] = 100 * row["slowNonMatches"] / attempts if attempts else 0
    return {"traceEvents": sum(names.values()), "selectorStatsEvents": names["SelectorStats"],
        "selectorCount": len(ranked), "selectorElapsedUs": sum(row["elapsedUs"] for row in ranked),
        "selectors": ranked, "selectorEvents": selector_events,
        "invalidationCounts": [{"event": key[0], "reason": key[1], "count": count} for key, count in reasons.most_common()],
        "invalidationSamples": dict(invalidation_samples),
        "styleLayoutEvents": style_events,
        "eventCounts": dict(names.most_common())}


def begin_trace(session):
    """Attach listeners before capture so early completion cannot be missed."""
    capture = {"finished":False,"completion":{},"events":[],"eventCounts":Counter(),
               "retainedBytes":0,"retentionOverflow":False}
    def on_data(batch):
        for event in batch.get("value", []):
            name = event.get("name", "")
            capture["eventCounts"][name] += 1
            # Retain every selector table, timed event and process/thread
            # identity. Count per-node instant/debug events without keeping
            # their large payloads. This filtering is explicit in the receipt.
            if name != "SelectorStats" and event.get("ph") not in ("X", "B", "E", "M"):
                continue
            size = len(json.dumps(event, separators=(",", ":")).encode("utf-8"))
            if capture["retainedBytes"] + size > RETAINED_TRACE_LIMIT:
                capture["retentionOverflow"] = True
                continue
            capture["retainedBytes"] += size
            capture["events"].append(event)
    def on_complete(event):
        capture["completion"] = event
        capture["finished"] = True
    session.on("Tracing.dataCollected", on_data)
    session.on("Tracing.tracingComplete", on_complete)
    capture["listeners"] = (on_data, on_complete)
    session.send("Tracing.start", {"traceConfig": TRACE_CONFIG, "transferMode": "ReportEvents"})
    return capture


def finish_trace(session, page, destination, capture):
    # ReportEvents provides the relevant records directly; it does not rely on
    # a potentially delayed trace-to-JSON stream handle. A distinct completion
    # flag also handles the valid empty terminal event in this transport mode.
    try:
        if not capture["finished"]:
            session.send("Tracing.end")
        deadline = time.monotonic() + 60
        while not capture["finished"] and time.monotonic() < deadline:
            page.wait_for_timeout(50)
    finally:
        on_data, on_complete = capture["listeners"]
        session.remove_listener("Tracing.dataCollected", on_data)
        session.remove_listener("Tracing.tracingComplete", on_complete)
        trace = {"traceEvents":capture["events"],"metadata":{
            "filteredDiagnosticTrace":True,"retained":"SelectorStats, timed events, thread/process metadata",
            "sourceEventCounts":dict(capture["eventCounts"]),"captureFinished":capture["finished"],
            "retentionOverflow":capture["retentionOverflow"],"retainedJsonBytes":capture["retainedBytes"],
            "completion":capture["completion"]}}
        with gzip.open(destination,"wt",encoding="utf-8") as target:
            json.dump(trace,target,separators=(",", ":"))
    assert capture["finished"], f"Trace did not complete; retained {len(capture['events'])} relevant events in {destination.name}"
    assert not capture["retentionOverflow"], "Relevant trace exceeded its bounded retention budget; diagnostic incomplete"
    return trace, {"file":destination.name,"sha256":hashlib.sha256(destination.read_bytes()).hexdigest(),
        "bytes":destination.stat().st_size,"dataLossOccurred":capture["completion"].get("dataLossOccurred",False),
        "filteredDiagnosticTrace":True,"sourceEvents":sum(capture["eventCounts"].values()),
        "sourceEventCounts":dict(capture["eventCounts"]),"retainedEvents":len(capture["events"]),
        "retainedJsonBytes":capture["retainedBytes"]}


def run_arm(browser, html, fixture, variant, actions, output, instrumented, checkpoint):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    # Profiling deliberately slows rule matching; this is a diagnostic deadline,
    # not an acceptance target or a relaxation of the ordinary browser suite.
    page.set_default_timeout(180000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    metrics = context.new_cdp_session(page)
    metrics.send("Performance.enable")
    tracing = browser.new_browser_cdp_session() if instrumented else None
    arm = {"variant": variant, "instrumented": instrumented, "ok": False, "actions": []}
    checkpoint(arm)
    try:
        page.set_content(html, wait_until="load")
        page.wait_for_function("!!window.__ARSENAL_QA__")
        arm["cssExperiment"] = page.evaluate(VARIANTS[variant])
        page.evaluate("fixture=>window.modhunterImportCatalog(fixture)", fixture)
        acceptance.settle(page)
        if page.evaluate("typeof window.__STYLE_DIAGNOSTIC_AFTER_IMPORT__==='function'"):
            arm["cssExperiment"] = page.evaluate("window.__STYLE_DIAGNOSTIC_AFTER_IMPORT__()")
            acceptance.settle(page)
        arm["initial"] = complete_geometry(page, len(fixture["items"]))
        arm["initialComputedStyles"] = computed_samples(page)
        checkpoint(arm)
        for action in actions:
            print(f"STYLE_PROFILE {variant} {'selector-trace' if instrumented else 'ordinary'} {action} starting", flush=True)
            result = {"action": action, "ok": False}
            arm["actions"].append(result)
            checkpoint(arm)
            if tracing:
                start_root_observer(page)
                # Per-node invalidation tracking flooded the first trace. Keep
                # aggregate selector statistics and bounded trace storage;
                # body/list mutations are captured by the small observer.
                capture = begin_trace(tracing)
            before = metric_snapshot(metrics)
            started = time.perf_counter()
            action_error = None
            try:
                page.locator(ACTIONS[action]).click()
                acceptance.settle(page)
            except Exception as error:
                action_error = error
            elapsed = (time.perf_counter() - started) * 1000
            after = metric_snapshot(metrics)
            result.update(wallMs=elapsed, metricDeltas={key: after[key] - before[key] for key in METRICS if key in before and key in after})
            if tracing:
                # End capture before complete-row assertions or JSON processing.
                trace, receipt = finish_trace(tracing, page, output / f"{variant}-{action}.trace.json.gz", capture)
                result["trace"] = receipt
                result["rootMutations"] = stop_root_observer(page)
                summary = summarize_trace(trace)
                summary_path = output / f"{variant}-{action}.selectors.json"
                write_json(summary_path, summary)
                result["selectorSummary"] = {"file": summary_path.name,
                    "selectorStatsEvents": summary["selectorStatsEvents"],
                    "selectorCount": summary["selectorCount"], "topSelectors": summary["selectors"][:20],
                    "invalidationCounts": summary["invalidationCounts"][:20]}
                assert summary["selectorStatsEvents"] and summary["selectorCount"], "Chromium emitted no selector statistics; diagnostic incomplete"
                assert not receipt["dataLossOccurred"], "Chromium dropped trace data; diagnostic incomplete"
            checkpoint(arm)
            if action_error:
                raise action_error
            result["completeLibrary"] = complete_geometry(page, len(fixture["items"]))
            result["computedStyles"] = computed_samples(page)
            if result["completeLibrary"]["view"] == "gallery":
                result["pseudoLabels"] = pseudo_label_state(page)
            assert not errors, errors
            result["ok"] = True
            checkpoint(arm)
            print(f"STYLE_PROFILE {variant} {action} wall={elapsed:.1f}ms style={result['metricDeltas'].get('RecalcStyleDuration', 0):.3f}s rows={result['completeLibrary']['rows']}", flush=True)
        arm["ok"] = True
    except Exception as error:
        arm["error"] = f"{type(error).__name__}: {error}"
        print(f"STYLE_PROFILE {variant} failed: {arm['error']}", flush=True)
    finally:
        arm["pageErrors"] = errors
        checkpoint(arm)
        if tracing:
            tracing.detach()
        context.close()
    return arm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "native/target/style-profile")
    parser.add_argument("--mods", type=int, default=11207)
    parser.add_argument("--variants", default="control")
    parser.add_argument("--trace-variants", default="control")
    parser.add_argument("--actions", default="gallery,density,list")
    args = parser.parse_args()
    variants = args.variants.split(",")
    trace_variants = [name for name in args.trace_variants.split(",") if name]
    actions = args.actions.split(",")
    if args.mods != 11207:
        parser.error("The focused diagnosis must use the same complete 11,207-record acceptance fixture")
    if any(name not in VARIANTS for name in variants + trace_variants) or "control" not in variants:
        parser.error(f"Variants must include control and be one of {', '.join(VARIANTS)}")
    if any(action not in ACTIONS for action in actions) or actions[0] != "gallery":
        parser.error("Actions must start with gallery and use gallery,density,list")
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = acceptance.dashboard.catalog(count=args.mods)
    fixture["offline"] = True
    html, assets = acceptance.compile_dashboard()
    proof = {"ok": False, "purpose": "Focused CSS diagnosis only; does not replace full release acceptance",
        "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sourceStatusAtStart": source_status(), "platform": platform.platform(), "assets": assets,
        "mods": args.mods, "fixtureSha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(),
        "method": "One uninstrumented diagnostic trial per fresh-page CSS arm. Separate selector traces add overhead; never compare their absolute times to ordinary trials or release benchmarks. All records remain laid out; no virtualization, skipped layout, or caps.",
        "tracingCategories": CATEGORIES, "traceConfig": TRACE_CONFIG, "ordinaryTrials": [], "selectorTraces": []}
    receipt = args.output / "style-profile.json"
    write_json(receipt, proof)
    assert not proof["sourceStatusAtStart"], "Focused CI profile requires clean committed source"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            launch["executable_path"] = executable
        browser = runtime.chromium.launch(**launch)
        proof["browserVersion"] = browser.version
        try:
            for instrumented, names in ((False, variants), (True, trace_variants)):
                field = "selectorTraces" if instrumented else "ordinaryTrials"
                for variant in names:
                    def checkpoint(arm):
                        existing = next((index for index, value in enumerate(proof[field]) if value["variant"] == arm["variant"]), None)
                        if existing is None:
                            proof[field].append(arm)
                        else:
                            proof[field][existing] = arm
                        write_json(receipt, proof)
                    run_arm(browser, html, fixture, variant, actions, args.output, instrumented, checkpoint)
            control = next(arm for arm in proof["ordinaryTrials"] if arm["variant"] == "control")
            reference_states = {"initial": control.get("initialComputedStyles", {})}
            reference_states.update({row["action"]: row.get("computedStyles", {}) for row in control["actions"]})
            reference_geometry = {"initial":control.get("initial", {})}
            reference_geometry.update({row["action"]:row.get("completeLibrary", {}) for row in control["actions"]})
            for arm in proof["ordinaryTrials"]:
                states = {"initial": arm.get("initialComputedStyles", {})}
                states.update({row["action"]: row.get("computedStyles", {}) for row in arm["actions"]})
                arm["computedStyleDifferencesFromControl"] = [
                    {"state": state, "selector": selector, "property": property,
                     "control": reference_states.get(state, {}).get(selector, {}).get(property), "variant": value}
                    for state, samples in states.items() for selector, properties in samples.items()
                    for property, value in (properties or {}).items()
                    if value != (reference_states.get(state, {}).get(selector) or {}).get(property)
                ]
                geometry = {"initial":arm.get("initial", {})}
                geometry.update({row["action"]:row.get("completeLibrary", {}) for row in arm["actions"]})
                arm["geometryComparisonsFromControl"] = {
                    state:compare_geometry(reference_geometry.get(state, {}), sample)
                    for state,sample in geometry.items()}
                if arm["variant"] in ("persistent-flex", "persistent-flex-cssom") and any(
                    comparison["differenceCount"] for comparison in arm["geometryComparisonsFromControl"].values()
                ):
                    arm["ok"] = False
                    arm["geometryError"] = "Persistent-flex experiment changed actual layout; inspect every-row geometry comparisons"
            proof["sourceStatusAtEnd"] = source_status()
            proof["ok"] = not proof["sourceStatusAtEnd"] and all(arm["ok"] for field in ("ordinaryTrials", "selectorTraces") for arm in proof[field])
        finally:
            browser.close()
            write_json(receipt, proof)
    if not proof["ok"]:
        raise AssertionError(f"Focused profile incomplete; inspect {receipt}")
    print(json.dumps({"ok": proof["ok"], "candidate": proof["candidateCommit"], "output": str(receipt)}, indent=2))


if __name__ == "__main__":
    main()
