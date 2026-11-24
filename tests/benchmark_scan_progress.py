"""Compare the complete library DOM and scan progress cost in private Chromium sessions."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

from playwright.sync_api import sync_playwright
from test_frontend_responsiveness import catalog

ROOT = Path(__file__).resolve().parents[1]
PAGE = "mo2_update_arsenal/web/index.html"
PROBE = r"""
window.__scanBench={
 async setup(){
  const c=await (await fetch('/catalog')).json();
  DATA=c;LIVE_CATALOG=c;LIVE_CONNECTED=true;BRIDGE.active=true;PROGRESS_READY=true;
  STORE=structuredClone(BASE);STORE.prefs.collapsed=[];STORE.prefs.filter='all';STORE.prefs.query='';
  ITEM_MAP=new Map(c.items.map(m=>[m.id,m]));GROUP_MAP=new Map(c.groups.map(g=>[g.id,g]));
  CATALOG_RENDER_REVISION++;LOCAL_PLAN_CACHE=null;applyAppearance();groupProgress();renderChapters();
  renderWorkspace();renderList();renderWorkshopJourney();
  return {rows:document.querySelectorAll('#modList .mod-row').length,chapters:document.querySelectorAll('#modList .chapterbar').length};
 },
 async revise(){
  const target=DATA.items.find(m=>getSrc(m).type==='nexus'&&getSrc(m).verified);
  if(!target)throw Error('The revision control needs an eligible source');
  const items=DATA.items.map(m=>m===target?{...m,update:{source:'nexus',checkedAt:1700000000,
   latestVersion:'9.9',verdict:'candidate',reason:'Exact changed release for the benchmark'}}:m);
  const revised={...DATA,items};
  const nativeFetch=window.fetch;
  window.fetch=async(url,options)=>{
   if(url==='/api/game?compact=1')return Response.json({ok:true,profileId:DATA.profileId,score:0,feed:[]});
   if(url==='/api/update-plan')return Response.json({ok:true,...currentWorkshopPlan()});
   return nativeFetch(url,options);
  };
  try{
   const start=performance.now();
   await activateLiveCatalog(revised);
   const row=document.getElementById('row-'+target.id);
   if(row?.querySelector('.detected-status')?.textContent!=='Update available')throw Error('Final changed verdict was not rendered');
   const height=document.documentElement.getBoundingClientRect().height;
   await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   return {completedMs:performance.now()-start,changedVerdict:true,laidOut:height>0};
  }finally{window.fetch=nativeFetch}
 },
 sample(state){
  function run(){
   const start=performance.now();updateScanUI(state);
   const sync=performance.now()-start;
   document.documentElement.getBoundingClientRect();
   return {sync,layout:performance.now()-start};
  }
  const result=run();
  return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(result))));
 },
 async content(){
  const selectors=['#modList','#resultsMeta','#listFooter','#workshopJourney','#workspaceCheckLine',
   '#workspaceScanProgress','#liveScanProgress','#workspaceCheckDetail','#workspaceCheckStatus','#unifiedScan',
   '#workspaceCancel','#workspaceProfile','#liveQuick','#liveDeep','#liveLocal','#liveAll',
   '[id^="detected"]','#chapters','#activityPanel'];
  const out={};
  for(const selector of selectors){
   const html=Array.from(document.querySelectorAll(selector),n=>n.outerHTML).join('');
   const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(html));
   out[selector]=Array.from(new Uint8Array(bytes),x=>x.toString(16).padStart(2,'0')).join('');
  }
  return out;
 }
};
"""


def measure(browser, html, wire, samples):
    context = browser.new_context(viewport={"width": 1360, "height": 900}, service_workers="block")
    page = context.new_page()
    page.set_default_timeout(120000)
    marker = "window.__ARSENAL_QA__={"
    if html.count(marker) != 1:
        raise ValueError("Expected the packaged dashboard's QA entry point")
    document = html.replace(marker, PROBE + marker)
    def route(request):
        if request.request.url == "https://scan-benchmark.test/":
            request.fulfill(content_type="text/html; charset=utf-8", body=document)
        elif request.request.url == "https://scan-benchmark.test/catalog":
            request.fulfill(content_type="application/json", body=wire)
        else:
            request.abort()
    context.route("**/*", route)
    try:
        page.goto("https://scan-benchmark.test/", wait_until="domcontentloaded", timeout=120000)
        session=context.new_cdp_session(page)
        session.send("Performance.enable")
        def counters():
            metrics=session.send("Performance.getMetrics")["metrics"]
            return {m["name"]:m["value"] for m in metrics if m["name"] in ("TaskDuration","ScriptDuration","JSHeapUsedSize","Nodes")}
        shape = page.evaluate("() => __scanBench.setup()")
        if shape["rows"] != 11325:
            raise ValueError("The full 11,325-row library must remain mounted")
        state = {"mode":"all", "state":"running", "checked":0, "total":11325}
        def timed():
            start = time.perf_counter()
            sample = page.evaluate("s=>__scanBench.sample(s)", state)
            sample["twoFrames"] = (time.perf_counter() - start) * 1000
            return sample
        initial = timed()
        start_work=counters()
        warm = []
        for i in range(1, samples+1):
            state.update(checked=i*17, fromCache=i)
            warm.append(timed())
        end_work=counters()
        running = page.evaluate("() => __scanBench.content()")
        # Compare retained JS heap at the same boundary. Raw heap and DOM-node
        # counters include objects awaiting Chromium's opportunistic collection.
        session.send("HeapProfiler.collectGarbage")
        retained=counters()
        state.update(state="completed", checked=11325, queried=11274, fromCache=51)
        terminal = timed()
        completed = page.evaluate("() => __scanBench.content()")
        revision=page.evaluate("() => __scanBench.revise()")
        revised=page.evaluate("() => __scanBench.content()")
        def stats(key):
            values=sorted(v[key] for v in warm)
            return {"medianMs":statistics.median(values),"p95Ms":values[math.ceil(.95*len(values))-1],"maxMs":max(values)}
        return {"shape":shape,"samples":samples,"firstRunning":initial,"progress":{key:stats(key) for key in initial},
                "rendererCountersBefore":start_work,"rendererCountersAfter":end_work,
                "rendererCountersAfterCollection":retained,
                "completion":terminal,"changedCatalogCompletion":revision,"runningDOM":running,
                "completedDOM":completed,"revisedDOM":revised}
    finally:
        context.close()


def synthetic_catalog():
    data=catalog(count=11325)
    size=len(data["items"])
    groups=[]
    for number in range(834):
        start=number*size//834
        stop=(number+1)*size//834
        group={"id":f"chapter-{number}", "name":f"Benchmark Chapter {number}", "count":stop-start,
               "color":"mint", "startPriority":start}
        groups.append(group)
        for index in range(start,stop):
            mod=data["items"][index]
            mod.update(group=group["name"],groupId=group["id"])
            mod["update"]={"source":"nexus","checkedAt":1700000000,
                           "verdict":("current","candidate","review","ignored")[index%4]}
    data["groups"]=groups
    return data


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--baseline", required=True)
    cli.add_argument("--catalog", type=Path, help="Optional private native JSON import with 11,325 rows")
    cli.add_argument("--browser", type=Path)
    cli.add_argument("--samples", type=int, default=61)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.samples < 3:
        cli.error("Use at least three samples")
    data=json.loads(args.catalog.read_bytes()) if args.catalog else synthetic_catalog()
    data=data.get("catalog",data)
    wire=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode()
    baseline=subprocess.check_output(["git","show",args.baseline+":"+PAGE],cwd=ROOT).decode()
    candidate=(ROOT/PAGE).read_text("utf-8")
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True, executable_path=str(args.browser) if args.browser else None)
        try:
            report={"baselineCommit":args.baseline,"browserVersion":browser.version,
                    "catalogSource":"private native import" if args.catalog else "synthetic",
                    "equivalentInputSha256":hashlib.sha256(wire).hexdigest(),
                    "baselineDashboardSha256":hashlib.sha256(baseline.encode()).hexdigest(),
                    "candidateDashboardSha256":hashlib.sha256(candidate.encode()).hexdigest()}
            for key,source in (("baseline",baseline),("candidate",candidate)):
                report[key]=measure(browser,source,wire,args.samples)
        finally:
            browser.close()
    for key in ("shape","runningDOM","completedDOM","revisedDOM"):
        if report["baseline"][key]!=report["candidate"][key]:
            raise ValueError("Zero-loss comparison failed for "+key)
    report["completeDOMParity"]=True
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:v["progress"] for k,v in report.items() if k in ("baseline","candidate")}))


if __name__ == "__main__":
    main()
