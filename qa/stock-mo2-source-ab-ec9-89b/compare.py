"""Fail-closed comparison of four stock diagnostic receipts. No raw catalog export."""
from __future__ import annotations

import argparse
import importlib.util
import json
from math import ceil
from pathlib import Path
import re
from statistics import median
import sys

from payload import HERE, HOST_SHA, HOST_SIZE, PIN_SHA, check, digest, file_bytes

spec=importlib.util.spec_from_file_location('diagnostic_stockTrace', HERE/'observer/trace.py')
trace=importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)
RUNTIME={"platform":"win32","mo2":"2.5.2.0","qt":"6.7.1","pyqt":"6.7.1"}
DLL_INFO={"abi":1,"buildQt":"6.7.1","runtimeQt":"6.7.1","mechanism":"typed-qt-sender"}
RUNS=("trial-01-A","trial-02-B","trial-03-B","trial-04-A")
ROWS=11207
KEYS=("bridge.py","model.py","plugin.py","refresh_work.py","snapshot_projection.py","source_registry.py","version.py")
CHECKS={"stock_isolated_fixture","stock_large_snapshot","stock_ui_heartbeat","stock_startup_trace"}

def bounded(path):
    st=path.stat()
    check(path.is_file() and not path.is_symlink() and 0<st.st_size<=512*1024,"Missing or oversize receipt")
    raw=path.read_bytes()
    return json.loads(raw),digest(raw)

def quantile(data,p=.95):
    return sorted(data)[ceil(len(data)*p)-1]

def verified(outer,native,seal,expected,shared_dll):
    check(outer['ok'] is True and outer['phase']=='completed' and outer['nativeExitCode']==0
          and outer['nativeReceipt']==native and native['ok'] is True and native['nativeMO2'] is True,
          "Stock process or receipt incomplete")
    check(native['source']==outer['source']==expected['build']['commit']
          and native['runtime']==RUNTIME
          and outer['release']['executableVersion']=='2.5.2'
          and outer['release']['bytes']==HOST_SIZE and outer['release']['sha256']==HOST_SHA,
          "Wrong exact installed runtime, source or official stock archive")
    check(native['loadedQtBridgeSha256']==outer['qtBridge']['sha256']==shared_dll
          and native['qtBridgeBinding']==DLL_INFO,"Arms used different loaded helpers")
    check(native['loadedSourceSha256']=={k:expected['src'][k] for k in KEYS},
          "Loaded fixture modules do not match the exact installed release/candidate bytes")
    check(native['fixture']=={'profile':'Modhunter Smoke','installedGame':False,'ownedProfile':False}
          and native['generation']==1 and native['inventory']['requestedRows']==outer['expectedFixtureRows']==ROWS
          and native['inventory']['allNativeRows']==native['inventory']['fixtureRows']==ROWS
          and native['inventory']['activeFixtureRows']==8406
          and native['inventory']['orderHash']=='ab2c9a6ecaf332532bb78e77a4493b533984b154f4d57d97eb3a4346c33817bb'
          and native['inventory']['fixtureHash']=='90fbd5be829d13bec0a3a213aa8c65ec7337ac7c7bf0488a82c99620554b7da7',
          "Missing complete same-profile/order/activation witness")
    check({row['name'] for row in native['checks'] if row['passed'] is True}==CHECKS,"Partial diagnostic checks")
    check(outer['fixtureInputSeal']==seal and len(seal)==64,"Fixture inputs not immutable")
    t=native['timing']
    expected_trace=native['startupTrace']
    j=trace.StartupTrace()
    j.tickets=len(expected_trace['attempts'])
    for ev in expected_trace['edges']:
        p=dict(ev)
        j.events.append((round(p.pop('msFromPluginInit')*1e6),p.pop('attempt'),p.pop('edge'),p))
    origin = t['pluginInitToPublicationMs']
    authoritative=trace.verify_trace(j,origin=0,
       first=round((origin-t['firstAttemptToPublicationMs'])*1e6),published=round(origin*1e6),
       terminal_count=native['startupAttempts'])
    check(authoritative==expected_trace, "Bounded observer trace did not replay")
    offsets=t['qtTickOffsetsFromUiMs']
    intervals=[b-a for a,b in zip(offsets,offsets[1:])]
    check(len(offsets)==t['uiQtTickCount'] and t['intervalMs']==10
           and min(intervals)>0 and abs(quantile(intervals)-t["timerInterTickP95Ms"])<1e-6
           and abs(max(offsets[0],max(intervals))-t["maxCoveredGapMs"])<1e-6
           and abs(offsets[0]-t["firstUiReadyBoundaryToTickMs"])<1e-6
           and abs(offsets[-1]-t['uiReadyToPublicationMs']-t['publicationToNextQtTickMs'])<1e-6,
          "Timer cannot witness phase completions")
    ticket=authoritative['attempts'][-1]['id']
    selected={ev['edge']:ev['msFromPluginInit'] for ev in authoritative['edges'] if ev['attempt']==ticket}
    return {
      "attemptCount":len(authoritative['attempts']),"firstAttemptToPublicationMs":t['firstAttemptToPublicationMs'],
      "uiReadyToPublicationMs":t['uiReadyToPublicationMs'],"pluginInitToPublicationMs":t['pluginInitToPublicationMs'],
      "acceptedWorkerMs":selected['worker-exit']-selected['worker-enter'],
      "ownerPublishMs":selected['publish-exit']-selected['publish-enter'],
      "uiReadyToFirstTimerTickMs":t["firstUiReadyBoundaryToTickMs"],"worstCoveredTimerMs":t['maxCoveredGapMs'],
      "actualTimerP95Ms":quantile(intervals),"actualTimerMaxMs":max(intervals),"actualDeliveredTicks":len(offsets),
      "loadedSourceSha256":native['loadedSourceSha256'],"payloadHashWitnesses":native['readyCatalogHashes'],
      "attemptOutcomes":authoritative['attempts']
    }

def compare(root):
    pd,_=bounded(root/'payload-verification.json')
    raw=(HERE/'inputs.json').read_bytes()
    check(digest(raw)==PIN_SHA,"Input pin tampered")
    pins=json.loads(raw)
    dll=pins['A']['bridgeSha256']
    expected_assets={k:{h:v[h] for h in ("build","outerSha256","innerSha256","bridgeSha256")} for k,v in pins.items()}
    check(pd['arms']==expected_assets and pd['controlledHelperSha256']==dll and pd['stockMo2Sha256']==HOST_SHA,
          "Wrong controlled helper/vendor file")
    runs=[]
    evidences=set(x.name for x in (root/'receipts').iterdir())
    check(evidences==set(RUNS),"Need exactly four uncontaminated ABBA launch receipts")
    fixture_seal=None
    payload=None
    for label in RUNS:
        i=root/'receipts'/label
        check(set(x.name for x in i.iterdir())=={'result.json','native-receipt.json'},"Unexpected receipt files")
        s,ss=bounded(i/'result.json')
        n,ns=bounded(i/'native-receipt.json')
        seal=s['fixtureInputSeal']
        if fixture_seal is None:fixture_seal=seal
        check(seal==fixture_seal,"The four launch inputs differ")
        row=verified(s,n,fixture_seal,pins[label[-1]],dll)
        if payload is None: payload=row['payloadHashWitnesses']
        check(row['payloadHashWitnesses']==payload and all(re.fullmatch("[0-9a-f]{64}",v) for v in payload.values())
              and set(payload)=={
              'native','protectedCatalog','profile','nativeSources','protectedSources'},
              "Complete normalized catalog/profile/source mismatch")
        runs.append({"run":label,"stockReceiptSha256":ns,"orchestratorReceiptSha256":ss,**row})
    all_first=all(r['attemptCount']==1 for r in runs)
    keys=tuple(k for k,v in runs[0].items() if k.endswith('Ms'))
    arms=lambda name:[r for r in runs if r['run'].endswith(name)]
    signed={k:{"A_median":median(r[k] for r in arms('A')),"B_median":median(r[k] for r in arms('B')),
              "B_minus_A_early":runs[1][k]-runs[0][k],"B_minus_A_late":runs[2][k]-runs[3][k]} for k in keys}
    drift={k:{name:abs(arms(name)[0][k]-arms(name)[1][k]) for name in ('A','B')} for k in keys}
    # Fixed tolerances predeclared before hosted scheduling (not learned from
    # the sample). Passing them is not population repeatability or a release gate.
    limits={'acceptedWorkerMs':250,'uiReadyToPublicationMs':300,'actualTimerP95Ms':2,
            'actualTimerMaxMs':10,'worstCoveredTimerMs':15}
    stable=all(all(drift[k][name]<=limit for name in ('A','B')) for k,limit in limits.items())
    both=lambda k,limit:all(signed[k][edge]<=limit for edge in ("B_minus_A_early","B_minus_A_late"))
    worker=both('acceptedWorkerMs',-100)
    ready=both('uiReadyToPublicationMs',0)
    responsiveness=all(both(x,0) for x in ('actualTimerP95Ms','actualTimerMaxMs','worstCoveredTimerMs'))
    coherent=all((all_first,stable,worker,ready,responsiveness))
    out={"scope":"Exact source-only stock component comparison; not package/installation/Gallery approval.",
         "schedule":"ABBA one Windows VM, four fresh stock processes and same absolute synthetic identity path",
         "runs":runs,"fixtureInputSeal":fixture_seal,"balancedComplete":True,"allFirstAttempt":all_first,
         "artifactInputs":expected_assets,"controlledHelperSha256":dll,"officialStockMo2Sha256":HOST_SHA,
         "comparison":signed,"sameArmDriftMs":drift,"predeclaredDriftLimitsMs":limits,
         "sameArmDriftWithinLimits":stable,"bothWorkerPairsAtLeast100msLower":worker,
         "bothReadyPairsNoLater":ready,"bothPairsNoHigherUIWorstOrTail":responsiveness,
         "metDescriptiveSignalCriteria":coherent,
         "interpretation":"Four measured values only; not a causal speedup or repeatability claim even if criteria hold."}
    with (root/'comparison.json').open('x',encoding='utf-8') as fp:
        json.dump(out,fp,indent=2)
    return out

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--staged',type=Path,required=True)
    a=p.parse_args()
    o=compare(a.staged)
    print("Verified complete ABBA comparison; diagnostics only. Comparable first-attempt timing:",o["allFirstAttempt"])
