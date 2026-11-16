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
import tempfile
import time

import visual_release_qa as acceptance

ROOT = acceptance.ROOT
METRICS = ("ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "TaskDuration",
           "LayoutCount", "RecalcStyleCount", "Nodes", "JSHeapUsedSize")
CATEGORIES = ("devtools.timeline", "disabled-by-default-devtools.timeline",
              "disabled-by-default-blink.debug", "blink.user_timing")
TRACE_MODES = {"selectors": CATEGORIES, "timeline": ("devtools.timeline", "blink.user_timing")}
MPC_HISTOGRAM = "Blink.Style.MatchedPropertiesCache.BucketSize"
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
    "stable-descendant-display": """() => {
      const style=document.createElement('style');style.dataset.diagnosticVariant='stable-descendant-display';
      const nonce=document.querySelector('style[nonce]')?.nonce;if(nonce)style.nonce=nonce;
      style.textContent=`
        body[data-layout="compact"][data-library-view="list"] #modList > .mod-row > .mod-art{display:flex}
        body[data-layout="compact"][data-library-view="list"] #modList > .mod-row .mod-title{display:flow-root!important}
        @media(min-width:761px){body[data-layout="compact"][data-library-view="list"] #modList > .mod-row > .mod-version{display:flex;flex-direction:column;align-items:stretch;gap:0}}`;
      document.head.append(style);
      return {changedRules:3,description:'Keep three row-descendant display types stable; preserve List dimensions and Gallery CSS without changing the outer library layout'};
    }""",
    "isolated-cards": """() => {
      const style=document.createElement('style');style.dataset.diagnosticVariant='isolated-cards';
      const nonce=document.querySelector('style[nonce]')?.nonce;if(nonce)style.nonce=nonce;
      style.textContent='body[data-layout="compact"] #modList > .mod-row{isolation:isolate}';
      document.head.append(style);
      return {changedRules:1,description:'Give every complete library row a local stacking context; retain all records, geometry, overflow and controls'};
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

VARIANTS["stable-isolated"] = """() => {
  const display=(%s)(),isolation=(%s)();
  return {changedRules:display.changedRules+isolation.changedRules,experiments:[display,isolation],
    description:'Combine stable descendant displays and isolated complete rows; no other CSS change'};
}""" % (VARIANTS["stable-descendant-display"], VARIANTS["isolated-cards"])

LIFECYCLE_VARIANTS = {"stable-descendant-display","isolated-cards","stable-isolated"}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def source_status():
    return subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).splitlines()


def metric_snapshot(session):
    return {entry["name"]: entry["value"] for entry in session.send("Performance.getMetrics")["metrics"] if entry["name"] in METRICS}


def trace_parameters(mode):
    return {"categories":",".join(TRACE_MODES[mode]),"options":"record-until-full","transferMode":"ReportEvents"}


def cache_histogram_snapshot(session):
    """Optional real browser telemetry; absence must never become a zero sample.

    Browser.getHistogram's absolute count/sum/buckets are documented in the
    Chromium Browser protocol. Explicit snapshots avoid resetting another
    client's delta state. Calls stay outside every interaction clock.
    """
    try:
        histogram = session.send("Browser.getHistogram", {"name":MPC_HISTOGRAM,"delta":False})["histogram"]
        assert histogram["name"] == MPC_HISTOGRAM and all(key in histogram for key in ("count","sum","buckets"))
        return {"available":True,"histogram":histogram,"method":"Browser.getHistogram absolute snapshot"}
    except Exception as error:
        return {"available":False,"name":MPC_HISTOGRAM,"reason":f"{type(error).__name__}: {error}"}


def cache_histogram_delta(before, after):
    if not before.get("available") or not after.get("available"):
        return {"available":False,"reason":"Both absolute histogram snapshots were not available"}
    left,right = before["histogram"],after["histogram"]
    prior = {(bucket["low"],bucket["high"]):bucket["count"] for bucket in left["buckets"]}
    current = {(bucket["low"],bucket["high"]):bucket["count"] for bucket in right["buckets"]}
    buckets = [{"low":low,"high":high,"count":current.get((low,high),0)-prior.get((low,high),0)}
               for low,high in sorted(set(prior)|set(current))]
    count,total = right["count"]-left["count"],right["sum"]-left["sum"]
    if count<0 or total<0 or any(bucket["count"]<0 for bucket in buckets):
        return {"available":False,"reason":"Histogram reset or changed bucket accounting between snapshots"}
    return {"available":True,"name":MPC_HISTOGRAM,"count":count,"sum":total,
            "mean":total/count if count else None,"buckets":[bucket for bucket in buckets if bucket["count"]],
            "bucketBounds":"low inclusive; high exclusive","method":"Arithmetic difference of actual absolute snapshots"}


def browser_identity(browser):
    session = browser.new_browser_cdp_session()
    try:
        arguments = session.send("Browser.getBrowserCommandLine")["arguments"]
        assert arguments and arguments[0], "Chromium did not report its actual executable"
        return {"browserVersion":browser.version,"resolvedExecutable":str(Path(arguments[0]).resolve()),
                "executableEvidence":"Browser.getBrowserCommandLine arguments[0]"}
    finally:
        session.detach()


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
        'grid-template-columns','grid-template-rows','flex-basis','flex-grow','flex-shrink','flex-wrap','flex-direction',
        'align-items','align-content','gap','white-space','overflow','isolation'];
      const selectors=['#workbench','.library-grouping','#modList','#modList .mod-row',
        '#modList .mod-primary','#modList .mod-title','#modList .mod-art',
        '#modList .mod-version','#modList .inspect-col','#modList .file-download-btn'];
      return Object.fromEntries(selectors.map(selector=>{const element=document.querySelector(selector);
        if(!element)return [selector,null];const style=getComputedStyle(element);
        return [selector,Object.fromEntries(properties.map(property=>[property,style.getPropertyValue(property)]))];}));
    }""")


def deep_row_geometry(page, session):
    """Read every affected descendant, text range and actual pseudo layout box.

    The complete arrays are transient comparison input, not an unbounded JSON
    report. DOMSnapshot supplies real pseudo-element layout bounds that normal
    DOM rectangle APIs cannot expose. All reads occur outside action clocks.
    """
    rows = page.evaluate("""() => {
      const selectors={art:'.mod-art',title:'.mod-title',glyph:'.source-open-glyph',version:'.mod-version',
        newer:'.newer-version',priority:'.mod-priority',assessment:'.mod-assessment',source:'.mod-source',actions:'.inspect-col'};
      const pseudos=[['artBefore','art','::before'],['artAfter','art','::after'],
        ['primaryBefore','primary','::before'],['priorityBefore','priority','::before'],['versionBefore','version','::before']];
      const pseudoProperties=['display','content','position','left','top','right','bottom','width','height',
        'font','margin-top','margin-right','margin-bottom','margin-left','padding-top','padding-right','padding-bottom','padding-left'];
      const targetProperties=['display','flex-direction','align-items','gap','isolation','white-space','overflow','font','color'];
      return [...document.querySelectorAll('#modList > .mod-row')].map(row=>{
        const origin=row.getBoundingClientRect();
        const box=b=>[b.left-origin.left,b.top-origin.top,b.width,b.height];
        const elements=Object.fromEntries(Object.entries(selectors).map(([key,selector])=>[key,row.querySelector(selector)]));
        elements.primary=row.querySelector('.mod-primary');
        const rectangles=Object.fromEntries(Object.entries(selectors).map(([key])=>[key,elements[key]?box(elements[key].getBoundingClientRect()):null]));
        const ranges=element=>{if(!element)return [];const result=[];
          const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);let node;
          while(node=walker.nextNode()){if(!node.textContent.trim())continue;
            const range=document.createRange();range.selectNodeContents(node);
            result.push(...[...range.getClientRects()].map(box));}return result;};
        const installed=[];
        if(elements.version)for(const node of elements.version.childNodes)if(node.nodeType===Node.TEXT_NODE&&node.textContent.trim()){
          const range=document.createRange();range.selectNodeContents(node);installed.push(...[...range.getClientRects()].map(box));}
        const pseudoStyles=Object.fromEntries(pseudos.map(([key,owner,pseudo])=>{
          const style=getComputedStyle(elements[owner],pseudo);
          return [key,Object.fromEntries(pseudoProperties.map(name=>[name,style.getPropertyValue(name)]))];}));
        const computed=Object.fromEntries([['row',row],['art',elements.art],['title',elements.title],['version',elements.version]].map(([key,element])=>{
          const style=getComputedStyle(element);return [key,Object.fromEntries(targetProperties.map(name=>[name,style.getPropertyValue(name)]))];}));
        return {id:row.dataset.id,rectangles,textRanges:{title:ranges(elements.title),installed},pseudoStyles,computed,
          semantics:{text:row.textContent,labels:[...row.querySelectorAll('[aria-label]')].map(e=>[e.getAttribute('data-act'),e.getAttribute('aria-label')]),
            provider:elements.art.getAttribute('data-provider-label'),chapter:elements.primary.getAttribute('data-chapter-label')}};
      });
    }""")
    snapshot = session.send("DOMSnapshot.captureSnapshot", {"computedStyles":[],"includeDOMRects":True})
    strings = snapshot["strings"]
    document = snapshot["documents"][0]
    nodes,layout = document["nodes"],document["layout"]
    parents = nodes["parentIndex"]
    attributes = nodes.get("attributes",[])
    classes,identifiers,record_ids = {},{},{}
    for index,flattened in enumerate(attributes):
        for offset in range(0,len(flattened),2):
            key,value = strings[flattened[offset]],strings[flattened[offset+1]]
            if key=="class":classes[index]=set(value.split())
            elif key=="id":identifiers[index]=value
            elif key=="data-id":record_ids[index]=value
    roots = [index for index,value in identifiers.items() if value=="modList"]
    assert len(roots)==1, "DOMSnapshot did not contain the exact library root"
    root = roots[0]
    row_nodes = {index:record_ids[index] for index,value in classes.items()
                 if "mod-row" in value and parents[index]==root and index in record_ids}
    assert len(row_nodes)==len(rows), "DOMSnapshot lost complete library rows"
    bounds = {index:layout["bounds"][position] for position,index in enumerate(layout["nodeIndex"])}
    owners = {"mod-art":{"before":"artBefore","after":"artAfter"},
              "mod-primary":{"before":"primaryBefore"},"mod-priority":{"before":"priorityBefore"},
              "mod-version":{"before":"versionBefore"}}
    pseudo_bounds = {row["id"]:{key:None for key in row["pseudoStyles"]} for row in rows}
    rare = nodes.get("pseudoType",{"index":[],"value":[]})
    for node_index,type_index in zip(rare["index"],rare["value"]):
        pseudo_type = strings[type_index]
        owner = parents[node_index]
        key = next((mapping[pseudo_type] for name,mapping in owners.items()
                    if name in classes.get(owner,set()) and pseudo_type in mapping),None)
        if key is None:continue
        ancestor = owner
        while ancestor>=0 and ancestor not in row_nodes and ancestor!=root:ancestor=parents[ancestor]
        if ancestor not in row_nodes:continue
        identifier = row_nodes[ancestor]
        if node_index in bounds:
            box,origin = bounds[node_index],bounds[ancestor]
            pseudo_bounds[identifier][key]=[box[0]-origin[0],box[1]-origin[1],box[2],box[3]]
    for row in rows:
        row["pseudoBounds"]=pseudo_bounds[row["id"]]
        for key,style in row["pseudoStyles"].items():
            if style["display"]!="none" and style["content"] not in ("none","normal"):
                assert row["pseudoBounds"][key] is not None, f"Missing actual visible pseudo bounds for {row['id']} {key}"
    return rows


def allowed_lifecycle_style(variant, state, target, property, before, after):
    if variant not in LIFECYCLE_VARIANTS:return False
    if variant in ("isolated-cards","stable-isolated") and target=="row" and property=="isolation":
        return before=="auto" and after=="isolate"
    if variant not in ("stable-descendant-display","stable-isolated") or state not in ("initial","list"):return False
    allowed = {("art","display"):("block","flex"),("title","display"):("block","flow-root"),
               ("version","display"):("block","flex"),("version","flex-direction"):("row","column"),
               ("version","align-items"):("normal","stretch"),("version","gap"):("normal","0px")}
    return allowed.get((target,property))==(before,after)


def compare_deep_rows(reference, rows, variant, state):
    differences=[];total=0;allowed=Counter()
    def mismatch(path,left,right):
        nonlocal total
        total+=1
        if len(differences)<20:differences.append({"path":path,"control":left,"variant":right})
    def compare(left,right,path):
        if isinstance(left,(int,float)) and isinstance(right,(int,float)):
            if abs(left-right)>.1:mismatch(path,left,right)
        elif isinstance(left,dict) and isinstance(right,dict):
            if left.keys()!=right.keys():mismatch(path+".keys",list(left),list(right))
            for key in left.keys()&right.keys():compare(left[key],right[key],path+"."+key)
        elif isinstance(left,list) and isinstance(right,list):
            if len(left)!=len(right):mismatch(path+".length",len(left),len(right))
            for index,(a,b) in enumerate(zip(left,right)):compare(a,b,f"{path}[{index}]")
        elif left!=right:mismatch(path,left,right)
    if len(reference)!=len(rows):mismatch("rows",len(reference),len(rows))
    for index,(before,after) in enumerate(zip(reference,rows)):
        prefix=f"row[{index}]({after['id']})"
        compare(before["id"],after["id"],prefix+".id")
        for key in ("rectangles","textRanges","pseudoBounds","pseudoStyles","semantics"):
            compare(before[key],after[key],prefix+"."+key)
        for target,properties in before["computed"].items():
            for property,value in properties.items():
                actual=after["computed"][target][property]
                if value==actual:continue
                if allowed_lifecycle_style(variant,state,target,property,value,actual):allowed[(target,property,value,actual)]+=1
                else:mismatch(prefix+f".computed.{target}.{property}",value,actual)
    return {"comparedRows":len(reference),"toleranceCssPixels":.1,"differenceCount":total,
            "examples":differences,"examplesAreBoundedOnly":True,
            "allowedComputedDifferences":[{"target":key[0],"property":key[1],"control":key[2],"variant":key[3],"rows":count} for key,count in allowed.items()]}


def deep_geometry_receipt(rows):
    encoded=json.dumps(rows,separators=(",",":"),sort_keys=True).encode()
    distributions={}
    for field in ("rectangles","textRanges","pseudoBounds"):
        for key in rows[0][field] if rows else []:
            values=Counter(json.dumps(row[field][key],separators=(",",":")) for row in rows)
            distributions[field+"."+key]={"distinct":len(values),
                "sha256":hashlib.sha256(json.dumps(sorted(values.items()),separators=(",",":")).encode()).hexdigest()}
    return {"rows":len(rows),"firstId":rows[0]["id"] if rows else None,"lastId":rows[-1]["id"] if rows else None,
            "sha256":hashlib.sha256(encoded).hexdigest(),"transientSerializedBytes":len(encoded),"distributions":distributions,
            "scope":"Every row descendant/text/pseudo box, computed pseudo style and accessible label; complete arrays compared transiently, no record sampling"}


def record_deep_geometry(page, session, variant, state, reference_directory):
    """Keep one complete state in memory at a time and publish compact proof."""
    rows = deep_row_geometry(page,session)
    result = deep_geometry_receipt(rows)
    path = reference_directory / (state+".json.gz")
    if variant=="control":
        with gzip.open(path,"wt",encoding="utf-8") as stream:
            json.dump(rows,stream,separators=(",",":"))
        result["comparison"] = {"comparedRows":len(rows),"differenceCount":0,
            "examples":[],"allowedComputedDifferences":[],"reference":True}
    else:
        assert path.is_file(), "Complete control geometry was not recorded for "+state
        with gzip.open(path,"rt",encoding="utf-8") as stream:
            reference=json.load(stream)
        result["comparison"] = compare_deep_rows(reference,rows,variant,state)
    return result


def compare_geometry(reference, variant):
    """Compare every row/header rectangle, outside the interaction clock.

    Document coordinates keep actual layout comparisons independent of a
    Playwright control's viewport scroll. A tenth of a CSS pixel permits only
    layout-unit rounding, not a changed row height, offset or card column.
    """
    differences = []
    difference_count = 0
    def add_difference(value):
        nonlocal difference_count
        difference_count+=1
        if len(differences)<20:differences.append(value)
    for key in ("rows", "uniqueIds", "firstId", "lastId"):
        if reference.get(key) != variant.get(key):
            add_difference({"field":key,"control":reference.get(key),"variant":variant.get(key)})
    for key in ("listGeometry", "rowGeometry", "chapterGeometry", "emptyGeometry"):
        before, after = reference.get(key, []), variant.get(key, [])
        if key == "listGeometry":
            before, after = [before], [after]
        if len(before) != len(after):
            add_difference({"field":key,"controlCount":len(before),"variantCount":len(after)})
        for index, (left, right) in enumerate(zip(before, after)):
            if len(left) != len(right) or any(
                abs(a-b) > .1 if isinstance(a, (int,float)) and isinstance(b, (int,float)) else a != b
                for a,b in zip(left,right)
            ):
                add_difference({"field":key,"index":index,"control":left,"variant":right})
    return {"comparedRows":len(reference.get("rowGeometry", [])),
            "comparedChapters":len(reference.get("chapterGeometry", [])),
            "toleranceCssPixels":.1,"differenceCount":difference_count,"differences":differences,
            "examplesAreBoundedOnly":True}


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


def visible_record_hit_tests(page, selector):
    result=page.locator(selector).evaluate("""row=>{
      const names=['.mod-art','.mod-priority','.mod-title','.file-download-btn','.favorite-btn','.inspect-btn'];
      return {id:row.dataset.id,tests:names.flatMap(selector=>{const element=row.querySelector(selector);
        if(!element)return [];const box=element.getBoundingClientRect();
        if(!box.width||!box.height||box.top<0||box.bottom>innerHeight||box.left<0||box.right>innerWidth)return [];
        const hit=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);
        return [{selector,box:box.toJSON(),hitClass:hit?.className||'',hitRow:hit?.closest('.mod-row')?.dataset.id,
          receivesPointer:element===hit||element.contains(hit),pointerEvents:getComputedStyle(element).pointerEvents}];})};
    }""")
    assert result["tests"], "No actual visible card controls were hit-tested"
    for test in result["tests"]:
        assert test["hitRow"]==result["id"], f"Card stacking sent a pointer into another surface: {test}"
        if test["selector"]!=".mod-priority":
            assert test["receivesPointer"], f"Visible card control is occluded: {test}"
    return result


def capture_lifecycle_frames(browser, html, variant, fixture, output):
    """Inspect real interactions separately from the complete-library clocks.

    Frozen public project icons retain their actual project identity. The second
    catalog retains real source identities and explicitly labels one synthetic
    long filename. No screenshot or interaction substitutes for full-row gates.
    """
    destination=output/"frames"/variant
    destination.mkdir(parents=True,exist_ok=True)
    result={"ok":False,"screenshots":[],"hitTests":[],"scope":"Actual compiled application with deterministic API fixtures; separate from timed 11,207-record trials"}
    context=browser.new_context(viewport={"width":1440,"height":1000},reduced_motion="reduce")
    page=context.new_page();errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    def capture(name):
        acceptance.settle(page)
        raw=page.screenshot(path=str(destination/name))
        result["screenshots"].append({"file":str((destination/name).relative_to(output)),"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)})
    try:
        context.route("http://style-records.modhunter.invalid/",lambda request:request.fulfill(content_type="text/html",body=acceptance.fixture_page(html,fixture)))
        page.goto("http://style-records.modhunter.invalid/",wait_until="load")
        page.wait_for_function("document.getElementById('livePill').classList.contains('connected')")
        page.wait_for_function("__RESP__.counts['GET progress']>=1")
        page.wait_for_function("n=>document.querySelectorAll('#modList > .mod-row').length===n",arg=len(fixture["items"]))
        page.evaluate(VARIANTS[variant]);acceptance.settle(page)
        acceptance.scroll_dashboard_to_top(page)
        acceptance.assert_library_geometry(page,"diagnostic-list/"+variant)
        capture("long-name-list.png")
        page.locator("#libraryGallery").click();acceptance.settle(page)
        acceptance.scroll_dashboard_to_top(page)
        result["galleryGeometry"]=acceptance.assert_flat_gallery_geometry(page,len(fixture["items"]),"diagnostic-gallery/"+variant)
        result["actionGeometry"]=acceptance.assert_nexus_actions(page,"diagnostic-gallery/"+variant)
        row=page.locator('#modList .mod-row').filter(has=page.locator('.file-download-btn',has_text='Review')).first
        row.scroll_into_view_if_needed();acceptance.settle(page)
        row_id=row.get_attribute("id")
        result["hitTests"].append(visible_record_hit_tests(page,"#"+row_id))
        capture("long-name-gallery.png")
        inspect=row.locator('[data-act="inspect"]')
        inspect.focus();page.keyboard.press("j");acceptance.settle(page)
        selected=page.locator("#modList .mod-row.aether-target")
        assert selected.count()==1,"Normal J navigation did not select one actual row"
        selected.scroll_into_view_if_needed();acceptance.settle(page)
        result["selectedId"]=selected.get_attribute("data-id")
        result["hitTests"].append(visible_record_hit_tests(page,"#"+selected.get_attribute("id")))
        capture("selected-gallery.png")
        row.scroll_into_view_if_needed();inspect.focus();page.keyboard.press("Shift+F10");page.keyboard.press("ArrowDown")
        page.locator("#modContextMenu").wait_for(state="visible")
        result["contextMenu"]=acceptance.assert_context_menu_geometry(page,"diagnostic-menu/"+variant)
        assert page.locator('#modContextMenu [role="menuitem"]').evaluate_all("nodes=>nodes.every(node=>{const b=node.getBoundingClientRect(),hit=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);return node===hit||node.contains(hit)})"),"Context-menu controls are occluded"
        capture("context-menu.png")
        page.keyboard.press("Escape")
        assert inspect.evaluate("element=>element===document.activeElement"),"Menu did not return focus"
        inspect.click();page.locator("#detailDrawer.open").wait_for(state="visible");acceptance.settle(page)
        assert page.locator("#drawerClose").evaluate("element=>{const b=element.getBoundingClientRect(),hit=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);return element===hit||element.contains(hit)}"),"Inspector close control is occluded"
        capture("inspector.png")
        page.locator("#drawerClose").click()
        assert not page.locator("#detailDrawer").is_visible(),"Inspector did not close"
        assert page.locator("#modList > .mod-row").count()==len(fixture["items"]),"Representative interactions lost records"
        assert not errors,errors
    finally:
        context.close()
    manifest=json.loads((ROOT/"tests/fixtures/visual_public_catalog.json").read_text("utf-8"))
    sources=manifest["sources"]
    artwork=acceptance.dashboard.catalog(profile="style-diagnostic-public-artwork",count=len(sources))
    artwork.update(profileName="Official project artwork · synthetic installed versions",game="minecraft",gameRuntime="QA fixture")
    artwork["groups"][0]["name"]="Public Modrinth projects / artwork acceptance"
    for index,(item,source) in enumerate(zip(artwork["items"],sources)):
        item.update(name=source["name"],group=artwork["groups"][0]["name"],installedVersion="QA 1.0",author="",nexusId=0,
            screenshotNote=source["description"],audit={},source={"type":"modrinth","verified":True,"url":source["projectUrl"]},
            update={"verdict":"candidate" if index%3==0 else "current","latestVersion":"QA 2.0" if index%3==0 else "QA 1.0",
                "source":"visual-acceptance-fixture","thumbnailUrl":source["thumbnailUrl"],"thumbnailKind":"icon"})
    context=browser.new_context(viewport={"width":1440,"height":1000},reduced_motion="reduce")
    page=context.new_page();page.on("pageerror",lambda error:errors.append(str(error)))
    assets={source["thumbnailUrl"]:source for source in sources}
    def route(request):
        url=request.request.url
        if url=="http://style-artwork.modhunter.invalid/":
            request.fulfill(content_type="text/html",body=acceptance.fixture_page(html,artwork))
        elif url in assets:
            source=assets[url];raw=(ROOT/"tests/fixtures"/source["thumbnailFile"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest()==source["sha256"]
            request.fulfill(content_type=source["mimeType"],body=raw)
        else:request.abort()
    context.route("**/*",route)
    try:
        page.goto("http://style-artwork.modhunter.invalid/",wait_until="load")
        page.wait_for_function("document.getElementById('livePill').classList.contains('connected')")
        page.wait_for_function("__RESP__.counts['GET progress']>=1")
        page.wait_for_function("n=>document.querySelectorAll('#modList > .mod-row').length===n",arg=len(sources))
        page.evaluate(VARIANTS[variant]);page.locator("#libraryGallery").click();acceptance.settle(page)
        for image in page.locator("#modList .mod-preview-image").all():
            image.scroll_into_view_if_needed()
            page.wait_for_function("image=>image.complete&&image.naturalWidth>0",arg=image.element_handle())
        assert page.locator("#modList .mod-preview-image").count()==len(sources)
        for theme in ("night","snow"):
            acceptance.set_dashboard_theme(page,theme);acceptance.scroll_dashboard_to_top(page)
            result["hitTests"].append(visible_record_hit_tests(page,"#row-item-0"))
            capture("official-artwork-"+("dark" if theme=="night" else "light")+".png")
        result["artworkProvenance"]={"manifest":"tests/fixtures/visual_public_catalog.json","projects":len(sources),
            "sources":[{"projectUrl":source["projectUrl"],"sha256":source["sha256"]} for source in sources]}
        result["pageErrors"]=errors
        assert not errors,errors
        result["ok"]=True
        return result
    finally:
        context.close()


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
    timed_events = []
    timed_totals = {}
    owned_markers = []
    for event in trace.get("traceEvents", []):
        name = event.get("name", "")
        names[name] += 1
        args = event.get("args", {})
        if name.startswith("modhunter-style-profile-"):
            owned_markers.append({key:event.get(key) for key in ("name","ts","pid","tid","ph")})
        if event.get("ph") in ("X","B","E"):
            timed_events.append({"name":name,"pid":event.get("pid"),"tid":event.get("tid"),
                "ts":event.get("ts"),"phase":event.get("ph"),"durationUs":event.get("dur"),
                "threadDurationUs":event.get("tdur"),"category":event.get("cat")})
            if event.get("dur") is not None:
                key = (event.get("pid"),event.get("tid"),name)
                value = timed_totals.setdefault(key,{"pid":key[0],"tid":key[1],"name":name,
                    "count":0,"inclusiveDurationUs":0,"maximumDurationUs":0})
                value["count"] += 1
                value["inclusiveDurationUs"] += event["dur"]
                value["maximumDurationUs"] = max(value["maximumDurationUs"],event["dur"])
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
        "ownedMarkers":owned_markers,"timedEvents":timed_events,
        "timedEventTotals":sorted(timed_totals.values(),key=lambda row:row["inclusiveDurationUs"],reverse=True),
        "timingScope":"Actual trace durations by process/thread/name; inclusive nested events and parallel threads must not be summed as total wall time. B/E records without a protocol duration remain explicit, not invented.",
        "eventCounts": dict(names.most_common())}


def begin_trace(session, mode="selectors"):
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
            if name != "SelectorStats" and not name.startswith("modhunter-style-profile-") and event.get("ph") not in ("X", "B", "E", "M"):
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
    session.send("Tracing.start", trace_parameters(mode))
    return capture


def finish_trace(session, page, destination, capture, completion_seconds=60):
    # ReportEvents provides the relevant records directly; it does not rely on
    # a potentially delayed trace-to-JSON stream handle. A distinct completion
    # flag also handles the valid empty terminal event in this transport mode.
    try:
        if not capture["finished"]:
            session.send("Tracing.end")
        deadline = time.monotonic() + completion_seconds
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


def full_page_transport_preflight(session, page, destination, result, trace_mode="selectors"):
    """Prove event delivery on the complete imported page before a costly trace.

    Only a toolbar control's non-inherited outline offset changes, synchronously
    restored to its exact prior style attribute. No library record or layout is
    modified. This transport check is outside every interaction timer.
    """
    started = time.perf_counter()
    capture = begin_trace(session,trace_mode)
    result["controlStyle"] = page.evaluate("""() => {
      const control=document.getElementById('libraryGallery');
      if(!control)throw Error('Expected the real Gallery toolbar control');
      const attribute=control.getAttribute('style'),before=getComputedStyle(control).outlineOffset;
      performance.mark('modhunter-style-profile-transport-start');
      let changed;
      try{
        // Use the same attribute API for mutation and restoration. The receipt
        // checks raw values instead of inferring restoration from computed CSS.
        control.setAttribute('style',(attribute||'')+';outline-offset:'+(Number.parseFloat(before)+1)+'px!important;');
        changed=getComputedStyle(control).outlineOffset;
      }finally{
        if(attribute===null)control.removeAttribute('style');else control.setAttribute('style',attribute);
      }
      const after=getComputedStyle(control).outlineOffset;
      performance.mark('modhunter-style-profile-transport-end');
      const attributeAfter=control.getAttribute('style');
      return {before,changed,after,attributeBefore:attribute,attributeAfter,
        attributeRestored:attributeAfter===attribute};
    }""")
    trace, receipt = finish_trace(session,page,destination,capture,completion_seconds=3)
    result["wallMs"] = (time.perf_counter()-started)*1000
    result["trace"] = receipt
    summary = summarize_trace(trace)
    result["eventCounts"] = summary["eventCounts"]
    result["selectorStatsEvents"] = summary["selectorStatsEvents"]
    result["selectorCount"] = summary["selectorCount"]
    style = result["controlStyle"]
    assert style["before"] != style["changed"] and style["before"] == style["after"] and style["attributeRestored"], "Transport probe did not restore the real control style"
    assert summary["traceEvents"] and summary["eventCounts"].get("modhunter-style-profile-transport-start") and summary["eventCounts"].get("modhunter-style-profile-transport-end"), "Full-page transport probe did not deliver both owned markers"
    assert not receipt["dataLossOccurred"], "Full-page transport probe dropped trace data"
    result["ok"] = True


def run_arm(browser, html, fixture, variant, actions, output, instrumented, checkpoint, trace_mode="selectors", deep_reference_directory=None):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    # Profiling deliberately slows rule matching; this is a diagnostic deadline,
    # not an acceptance target or a relaxation of the ordinary browser suite.
    page.set_default_timeout(180000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    metrics = context.new_cdp_session(page)
    metrics.send("Performance.enable")
    tracing = metrics if instrumented else None
    arm = {"variant": variant, "instrumented": instrumented, "ok": False, "actions": [],
           "browserVersion":browser.version,"freshBrowserForTrace":instrumented,
           "traceMode":trace_mode if instrumented else "none"}
    checkpoint(arm)
    try:
        page.set_content(html, wait_until="load")
        page.wait_for_function("!!window.__ARSENAL_QA__")
        arm["cssExperiment"] = page.evaluate(VARIANTS[variant])
        arm["cacheHistogram"] = {"beforeImport":cache_histogram_snapshot(metrics)}
        page.evaluate("fixture=>window.modhunterImportCatalog(fixture)", fixture)
        acceptance.settle(page)
        if page.evaluate("typeof window.__STYLE_DIAGNOSTIC_AFTER_IMPORT__==='function'"):
            arm["cssExperiment"] = page.evaluate("window.__STYLE_DIAGNOSTIC_AFTER_IMPORT__()")
            acceptance.settle(page)
        arm["cacheHistogram"]["afterImport"] = cache_histogram_snapshot(metrics)
        arm["cacheHistogram"]["importDelta"] = cache_histogram_delta(arm["cacheHistogram"]["beforeImport"],arm["cacheHistogram"]["afterImport"])
        arm["initial"] = complete_geometry(page, len(fixture["items"]))
        arm["initialComputedStyles"] = computed_samples(page)
        if deep_reference_directory is not None:
            arm["initialDeepGeometry"] = record_deep_geometry(page,metrics,variant,"initial",deep_reference_directory)
        checkpoint(arm)
        if tracing:
            arm["transportPreflight"] = {"ok":False,"completionDeadlineSeconds":3,
                "scope":"Marker-only delivery on the complete imported page; one restored non-inherited toolbar style"}
            checkpoint(arm)
            full_page_transport_preflight(tracing,page,output/f"{variant}-transport-preflight.trace.json.gz",arm["transportPreflight"],trace_mode)
            arm["transportPreflight"]["completeLibrary"] = complete_geometry(page,len(fixture["items"]))
            arm["transportPreflight"]["geometryComparison"] = compare_geometry(arm["initial"],arm["transportPreflight"]["completeLibrary"])
            assert not arm["transportPreflight"]["geometryComparison"]["differenceCount"], "Transport probe changed library geometry"
            assert not errors, errors
            checkpoint(arm)
        for action in actions:
            print(f"STYLE_PROFILE {variant} {trace_mode+'-trace' if instrumented else 'ordinary'} {action} starting", flush=True)
            result = {"action": action, "ok": False}
            arm["actions"].append(result)
            checkpoint(arm)
            result["cacheHistogramBefore"] = cache_histogram_snapshot(metrics)
            if tracing:
                start_root_observer(page)
                # Per-node invalidation tracking flooded the first trace. Keep
                # aggregate selector statistics and bounded trace storage;
                # body/list mutations are captured by the small observer.
                capture = begin_trace(tracing,trace_mode)
                page.evaluate("name=>performance.mark(name)", f"modhunter-style-profile-{action}-start")
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
                page.evaluate("name=>performance.mark(name)", f"modhunter-style-profile-{action}-end")
            result["cacheHistogramAfter"] = cache_histogram_snapshot(metrics)
            result["cacheHistogramDelta"] = cache_histogram_delta(result["cacheHistogramBefore"],result["cacheHistogramAfter"])
            if tracing:
                # End capture before complete-row assertions or JSON processing.
                trace, receipt = finish_trace(tracing, page, output / f"{variant}-{action}.trace.json.gz", capture)
                result["trace"] = receipt
                result["rootMutations"] = stop_root_observer(page)
                summary = summarize_trace(trace)
                summary_path = output / f"{variant}-{action}.{trace_mode}.json"
                write_json(summary_path, summary)
                result["traceSummary"] = {"file": summary_path.name,"mode":trace_mode,
                    "selectorStatsEvents": summary["selectorStatsEvents"],
                    "selectorCount": summary["selectorCount"], "topSelectors": summary["selectors"][:20],
                    "invalidationCounts": summary["invalidationCounts"][:20],"eventCounts":summary["eventCounts"]}
                assert summary["eventCounts"].get(f"modhunter-style-profile-{action}-start") and summary["eventCounts"].get(f"modhunter-style-profile-{action}-end"), "Trace lacks the owned workload boundaries; diagnostic incomplete"
                if trace_mode == "selectors":
                    assert summary["selectorStatsEvents"] and summary["selectorCount"], "Chromium emitted no selector statistics; diagnostic incomplete"
                else:
                    assert not summary["selectorStatsEvents"], "Timeline-only trace unexpectedly enabled selector instrumentation"
                    assert summary["eventCounts"].get("UpdateLayoutTree") and summary["eventCounts"].get("Layout"), "Timeline lacks actual style/layout work for the action"
                assert not receipt["dataLossOccurred"], "Chromium dropped trace data; diagnostic incomplete"
            checkpoint(arm)
            if action_error:
                raise action_error
            result["completeLibrary"] = complete_geometry(page, len(fixture["items"]))
            result["computedStyles"] = computed_samples(page)
            if result["completeLibrary"]["view"] == "gallery":
                result["pseudoLabels"] = pseudo_label_state(page)
            if deep_reference_directory is not None:
                result["deepGeometry"] = record_deep_geometry(page,metrics,variant,action,deep_reference_directory)
            assert not errors, errors
            result["ok"] = not result.get("deepGeometry",{}).get("comparison",{}).get("differenceCount",0)
            checkpoint(arm)
            print(f"STYLE_PROFILE {variant} {action} wall={elapsed:.1f}ms style={result['metricDeltas'].get('RecalcStyleDuration', 0):.3f}s rows={result['completeLibrary']['rows']}", flush=True)
        arm["ok"] = all(result["ok"] for result in arm["actions"]) and not arm.get("initialDeepGeometry",{}).get("comparison",{}).get("differenceCount",0)
        if not arm["ok"]:
            arm["geometryError"] = "The diagnostic CSS changed descendant/text/pseudo geometry, semantics or an unapproved computed property; timings remain recorded but equivalence is rejected"
    except Exception as error:
        arm["error"] = f"{type(error).__name__}: {error}"
        print(f"STYLE_PROFILE {variant} failed: {arm['error']}", flush=True)
    finally:
        arm["pageErrors"] = errors
        checkpoint(arm)
        metrics.detach()
        context.close()
    return arm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "native/target/style-profile")
    parser.add_argument("--mods", type=int, default=11207)
    parser.add_argument("--variants", default="control")
    parser.add_argument("--trace-variants", default="control")
    parser.add_argument("--actions", default="gallery,density,list")
    parser.add_argument("--trace-mode", choices=tuple(TRACE_MODES), default="selectors",
                        help="Timeline intentionally omits selector/per-node debug instrumentation; selectors retains the original strict statistics gate")
    parser.add_argument("--executable-path", type=Path, help="Explicit Chromium binary; otherwise retain the acceptance harness's system-first selection")
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
    lifecycle_comparison=bool(set(variants)&LIFECYCLE_VARIANTS)
    if lifecycle_comparison and variants[0]!="control":
        parser.error("Complete descendant comparisons require the unchanged control arm first")
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = acceptance.dashboard.catalog(count=args.mods)
    fixture["offline"] = True
    html, assets = acceptance.compile_dashboard()
    representative_fixture=None
    if lifecycle_comparison:
        representative_fixture=acceptance.screenshot_catalog()
        long_item=next(item for item in representative_fixture["items"] if item.get("nexusId") and item.get("update",{}).get("verdict")=="candidate")
        long_item["name"]+=" — Representative long filename fixture — optional compatibility patch for multiple installed versions and localized archives"
        representative_fixture["profileName"]="Real source catalog · one synthetic long-name QA fixture"
    proof = {"ok": False, "purpose": "Focused CSS diagnosis only; does not replace full release acceptance",
        "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sourceStatusAtStart": source_status(), "platform": platform.platform(), "assets": assets,
        "mods": args.mods, "fixtureSha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(),
        "method": "One uninstrumented diagnostic trial per fresh-page CSS arm. Separate explicitly named trace modes may add overhead; never compare their absolute times to ordinary trials or release benchmarks. All records remain laid out; no virtualization, skipped layout, or caps.",
        "traceMode":args.trace_mode,"selectorStatisticsRequired":args.trace_mode=="selectors",
        "tracingCategories": TRACE_MODES[args.trace_mode], "traceParameters": trace_parameters(args.trace_mode),
        "cacheHistogramScope":"Optional actual MPC bucket telemetry; before/after import and action snapshots outside interaction timers; unavailable is explicit",
        "traceSession": "One context.new_cdp_session(page) shared by Performance and Tracing",
        "instrumentedBrowserPolicy":"Fresh browser per instrumented arm; ordinary browser closed first",
        "retainedTraceLimitBytes": RETAINED_TRACE_LIMIT,
        "deepGeometryScope":"All 11,207 descendant rectangles, title/installed text ranges, actual DOMSnapshot pseudo bounds, pseudo styles and semantics; exact allowed style changes only. Complete reference arrays are transient; published distributions/digests and discrepancy examples are bounded." if lifecycle_comparison else None,
        "ordinaryTrials": [], "selectorTraces": []}
    receipt = args.output / "style-profile.json"
    write_json(receipt, proof)
    assert not proof["sourceStatusAtStart"], "Focused CI profile requires clean committed source"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime, tempfile.TemporaryDirectory(prefix="modhunter-style-geometry-") as references:
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = str(args.executable_path) if args.executable_path else shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            launch["executable_path"] = executable
        proof["requestedExecutable"] = executable
        proof["browserSelection"] = "explicit" if args.executable_path else "system-first, then Playwright default"
        browser = runtime.chromium.launch(**launch)
        proof.update(browser_identity(browser))
        proof["instrumentedBrowserIdentities"] = []
        try:
            for instrumented, names in ((False, variants), (True, trace_variants)):
                field = "selectorTraces" if instrumented else "ordinaryTrials"
                for variant in names:
                    if instrumented:
                        browser.close()
                        browser = runtime.chromium.launch(**launch)
                        identity = browser_identity(browser)
                        assert identity["resolvedExecutable"] == proof["resolvedExecutable"] and identity["browserVersion"] == proof["browserVersion"], "Instrumented browser identity differs from ordinary control"
                        proof["instrumentedBrowserIdentities"].append({"variant":variant,**identity})
                    def checkpoint(arm):
                        existing = next((index for index, value in enumerate(proof[field]) if value["variant"] == arm["variant"]), None)
                        if existing is None:
                            proof[field].append(arm)
                        else:
                            proof[field][existing] = arm
                        write_json(receipt, proof)
                    arm=run_arm(browser, html, fixture, variant, actions, args.output, instrumented, checkpoint,args.trace_mode,
                        Path(references) if lifecycle_comparison and not instrumented else None)
                    if lifecycle_comparison and not instrumented:
                        try:
                            arm["visualChecks"]=capture_lifecycle_frames(browser,html,variant,representative_fixture,args.output)
                        except Exception as error:
                            arm["visualChecks"]={"ok":False,"error":f"{type(error).__name__}: {error}"}
                            arm["ok"]=False
                        checkpoint(arm)
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
                if arm["variant"] in ({"persistent-flex", "persistent-flex-cssom"}|LIFECYCLE_VARIANTS) and any(
                    comparison["differenceCount"] for comparison in arm["geometryComparisonsFromControl"].values()
                ):
                    arm["ok"] = False
                    arm["geometryError"] = "Diagnostic CSS changed actual layout; inspect every-row geometry comparisons"
                if lifecycle_comparison:
                    targets={"#modList .mod-row":"row","#modList .mod-art":"art","#modList .mod-title":"title","#modList .mod-version":"version"}
                    for difference in arm["computedStyleDifferencesFromControl"]:
                        difference["allowed"]=allowed_lifecycle_style(arm["variant"],difference["state"],targets.get(difference["selector"]),
                            difference["property"],difference["control"],difference["variant"])
                    if any(not difference["allowed"] for difference in arm["computedStyleDifferencesFromControl"]):
                        arm["ok"]=False
                        arm["computedStyleError"]="A computed property changed outside the explicitly approved experiment"
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
