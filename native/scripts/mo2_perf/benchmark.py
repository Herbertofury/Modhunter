"""Real same-host MO2 A/B/C driver; portable disposable Windows only."""
from __future__ import annotations
import argparse, ctypes as C, ctypes.wintypes as W, hashlib, itertools, json, math
import os, platform, re, shutil, stat, struct, subprocess, sys, time, uuid
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ARMS = ("absent","released","candidate")
SIZES = (40, 1000)
QUIET = 20
STEPS = 48
PING_LIMIT = 500
MO2_PORTABLE_SHA256 = "e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815"
PUBLISHED_SHA256 = "fe3552411af9d3dbcc218e5146030417be0adf417b22bccf700b473adc07bfd1"
RELEASE = {"mo2": "2.5.2", "published": "v0.12.1", "published_head": "f96b86969ec9f9c7ba6928284f16223b673d33c0"}
SAFE_ARM_LIMITS = {"startup_ms": (400, .15), "early_wm_p95_ms": (10, .15), "wm_p95_ms": (10, .15),
                   "work_p95_ms": (25, .15), "idle_core_pct": (.2, 0)}
HEX = re.compile(r"^[a-f0-9]{64}$")
SOURCE = Path(__file__).resolve().parent

def need(ok, detail):
    if not ok:
        raise RuntimeError(detail)

def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def pct(samples, q):
    need(bool(samples) and all(math.isfinite(x) for x in samples), "empty or invalid timing series")
    return sorted(samples)[max(1, math.ceil(len(samples) * q)) - 1]

def order():
    for size in SIZES:
        for i, arms in enumerate(itertools.permutations(ARMS)):
            for arm in arms:
                yield size, i, arm

def tes4_marker():
    # Existing Windows smoke's inert TES4+HEDR, no masters or records.
    return struct.pack("<4sIIIIHH4sHfII",b"TES4",18,0,0,0,44,0,b"HEDR",12,1.7,0,0x800)

def fixture_names(size):
    return [f"Bench {'Alpha' if i % 2 == 0 else 'Beta'} {i:04d}" for i in range(size)]

def fixture_marker_map(size):
    enabled=[name for i,name in enumerate(fixture_names(size)) if i%4!=0]
    return {f"Bench{i:04d}.esp":enabled[i%len(enabled)] for i in range(size)}

def payload_id(size):
    return hashlib.sha256(("\n".join(fixture_names(size))+"\n").encode()).hexdigest()

def deterministic_report(size, results):
    pairs = [r for r in results if r["size"] == size]
    need(len(pairs) == 18, "each size needs six complete paired triplets")
    by = {(r["block"], r["arm"]):r for r in pairs}
    need(set(by) == {(i,a) for i in range(6) for a in ARMS}, "duplicate or missing arm")
    need([r["arm"] for r in pairs] == [a for s,_,a in order() if s == size], "run order was not counterbalanced")
    for row in pairs:
        need(row["valid"] and row["payload_id"] == payload_id(size) and not row["failed_pings"]
             and all(isinstance(row[m], (int,float)) and not isinstance(row[m],bool) and math.isfinite(row[m])
                     and row[m]>=0 for m in SAFE_ARM_LIMITS), "invalid/missed-response trial")
    def compare(base, metric, floor, frac):
        values = [by[i,base][metric] for i in range(6)]
        deltas = [by[i,"candidate"][metric]-values[i] for i in range(6)]
        limit = max(floor, frac*pct(values,.50))
        worst = max(deltas)
        return {"paired_deltas":deltas,"limit":limit,"worst_delta":worst,"within_guardrail":worst<=limit}
    return {baseline:{m:compare(baseline,m,*b) for m,b in SAFE_ARM_LIMITS.items()}
            for baseline in ("absent","released")}

def dump_new(p,obj):
    with p.open("x", encoding="utf-8") as f:
        json.dump(obj,f,indent=2,allow_nan=False)
        f.write("\n")

def text_new(p,s):
    p.write_bytes(s.replace("\n","\r\n").encode("utf-8"))

def inspect_tree(root, maximum):
    """Reject symlinks, junctions, special files and destructively large inputs."""
    total=0; fingerprints={}
    for base,dirs,files in os.walk(root, followlinks=False):
        for name in sorted(dirs+files):
            p=Path(base)/name
            st=p.lstat()
            need(not p.is_symlink() and not (getattr(st,"st_file_attributes",0)&0x400), "linked or special input: "+name)
            rel=p.relative_to(root).as_posix()
            need(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode), "nonregular input: "+name)
            if stat.S_ISREG(st.st_mode):
                total+=st.st_size
                need(total<=maximum and len(fingerprints)<16000,"input tree exceeds bound")
                fingerprints[rel]=sha(p)
    return fingerprints

def published_manifest(path, expected):
    need(path.stat().st_size < 8_000_000 and sha(path)==expected, "published plugin ZIP identity mismatch")
    seen=set(); out={}; total=0
    prefix="MO2/mo2_update_arsenal/"
    with ZipFile(path) as z:
        need(len(z.infolist()) <= 3000, "published ZIP entry limit")
        for info in z.infolist():
            n=info.filename
            if not n.startswith(prefix) or info.is_dir():
                continue
            rel=n[len(prefix):]
            parts=PurePosixPath(rel).parts
            need(parts and not rel.startswith("/") and "\\" not in rel and
                 all(part not in (".","..") and not any(c in part for c in ':*?<>|"') for part in parts)
                 and not stat.S_ISLNK(info.external_attr>>16)
                 and rel.lower() not in seen, "unsafe published plugin member")
            seen.add(rel.lower()); total += info.file_size
            need(total <= 220_000_000 and info.file_size <= 100_000_000, "published ZIP size limit")
            with z.open(info) as f:
                out[rel]=hashlib.file_digest(f,"sha256").hexdigest()
    need("plugin.py" in out and "bin/modhunter-qt-bridge.dll" in out, "published plugin payload missing")
    return out

def same_tree_copy(root,dst,expected,maximum):
    shutil.copytree(root,dst)
    need(inspect_tree(dst,maximum)==expected,"copied tree differs from pinned intake")

def stage(root,size):
    inst=root/"instance"; game=root/"synthetic-game"; profile=inst/"profiles"/"Modhunter Smoke"
    for q in (profile,*(inst/x for x in ("cache","downloads","overwrite","mods")),
              *(game/x for x in ("Data","Documents","Saves"))):
        q.mkdir(parents=True)
    text_new(game/"fixture.exe","Inert text marker; not an executable. Never launch.\n")
    names=fixture_names(size)
    for i,name in enumerate(names):
        p=inst/"mods"/name; p.mkdir()
        text_new(p/"meta.ini",f"[General]\ngameName=modhuntersmoke\nmodid=0\nversion=1.0.0\nrepository=Local\nnotes=Owned benchmark fixture\n")
        text_new(p/f"bench_{i:04d}.txt","synthetic benchmark payload\n")
    for marker,owner in fixture_marker_map(size).items():
        (inst/"mods"/owner/marker).write_bytes(tes4_marker())
    text_new(profile/"modlist.txt","# This file was automatically generated by Mod Organizer.\n"+
             "".join(("+" if i%4!=0 else "-")+n+"\n" for i,n in enumerate(names)))
    for file in ("plugins.txt","loadorder.txt","archives.txt","lockedorder.txt"):
        text_new(profile/file,"")
    text_new(root/"portable.txt","Owned CI benchmark fixture\n")
    text_new(root/"nxmhandler.ini","[General]\nnoregister=true\n")
    text_new(root/"ModOrganizer.ini",
      "[General]\nfirst_start=false\ngameName=Modhunter CI Fixture\n"
      f"gamePath=@ByteArray({game.as_posix()})\nselected_profile=@ByteArray(Modhunter Smoke)\nversion=2.5.2\n"
      f"\n[Settings]\nbase_directory={inst.as_posix()}\nmod_directory=%BASE_DIR%/mods\n"
      "profiles_directory=%BASE_DIR%/profiles\ndownload_directory=%BASE_DIR%/downloads\n"
      "overwrite_directory=%BASE_DIR%/overwrite\ncache_directory=%BASE_DIR%/cache\n"
      "language=en\ncheck_for_updates=false\ncategory_mappings=false\nautocheck_update_install=false\nlog_level=0\n")
    return names

class Win:
    def __init__(self):
        self.u=C.WinDLL("user32",use_last_error=True)
        self.k=C.WinDLL("kernel32",use_last_error=True)
        self.u.SendMessageTimeoutW.argtypes=(W.HWND,W.UINT,W.WPARAM,W.LPARAM,W.UINT,W.UINT,C.POINTER(C.c_size_t))
        self.u.SendMessageTimeoutW.restype=W.LPARAM
        self.u.GetWindowThreadProcessId.argtypes=(W.HWND,C.POINTER(W.DWORD))
        self.u.GetWindowThreadProcessId.restype=W.DWORD
        self.u.IsWindowVisible.argtypes=(W.HWND,)
        self.u.IsWindowVisible.restype=W.BOOL
        self.k.OpenProcess.argtypes=(W.DWORD,W.BOOL,W.DWORD)
        self.k.OpenProcess.restype=W.HANDLE
        self.k.CloseHandle.argtypes=(W.HANDLE,)
        self.k.CloseHandle.restype=W.BOOL
        self.k.GetProcessTimes.argtypes=(W.HANDLE,)+(C.POINTER(W.FILETIME),)*4
        self.k.GetProcessTimes.restype=W.BOOL
    def pid(self,hwnd):
        p=W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd,C.byref(p))
        return p.value
    def ping(self,hwnd,pid):
        need(self.pid(hwnd)==pid,"main Qt window PID is not the owned child")
        out=C.c_size_t()
        C.set_last_error(0)
        before=time.perf_counter()
        # Independent process observes the real native window message queue.
        ok=self.u.SendMessageTimeoutW(hwnd,0,0,0,0x1|0x2|0x20,PING_LIMIT,C.byref(out))
        err=C.get_last_error()
        return (time.perf_counter()-before)*1000,(None if ok else err)
    def cpu(self,pid):
        handle=self.k.OpenProcess(0x1000,False,pid)
        need(handle,"could not open owned child for timing")
        try:
            vals=[W.FILETIME() for _ in range(4)]
            need(self.k.GetProcessTimes(handle,*map(C.byref,vals)),"GetProcessTimes failed")
            return sum((v.dwHighDateTime<<32)+v.dwLowDateTime for v in vals[2:])/10_000_000
        finally:
            self.k.CloseHandle(handle)

def wait_file(path,proc,seconds):
    end=time.perf_counter()+seconds
    while time.perf_counter()<end:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        need(proc.poll() is None,"owned MO2 exited before "+path.name)
        time.sleep(.025)
    raise RuntimeError("native step timed out: "+path.name)

def no_existing_mo2():
    import winreg
    need(not (Path(os.environ["LOCALAPPDATA"])/"ModOrganizer").exists(),"existing or unexpected global MO2 data")
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,"nxm"):
            raise RuntimeError("existing or unexpected NXM handler")
    except FileNotFoundError:
        pass
    listed=subprocess.check_output(["tasklist.exe","/FI","IMAGENAME eq ModOrganizer.exe","/FO","CSV","/NH"],text=True,timeout=5)
    need('"modorganizer.exe"' not in listed.lower(),"an existing MO2 process is already active")

def trial(root, events, size, block, arm, win):
    env={k:v for k,v in os.environ.items() if not k.startswith(("MODHUNTER","QT_","QML")) and k not in ("PYTHONHOME","PYTHONPATH")}
    env.update(PYTHONNOUSERSITE="1", MO2_BENCH_ROOT=str(root), MO2_BENCH_EVENTS=str(events),
               MO2_BENCH_ARM=arm, MO2_BENCH_SIZE=str(size), MO2_BENCH_QUIET=str(QUIET),
               MO2_BENCH_STEPS=str(STEPS), MO2_BENCH_NONCE=uuid.uuid4().hex)
    launched=time.perf_counter()
    proc=subprocess.Popen([str(root/"ModOrganizer.exe")],cwd=root,env=env)
    try:
        ready=wait_file(events/"ready.json",proc,45)
        startup=(time.perf_counter()-launched)*1000
        need(ready["pid"]==proc.pid and ready["nonce"]==env["MO2_BENCH_NONCE"] and ready["initial_fixture_count"]==size,
             "READY is not the owned full native UI fixture")
        need(ready["production_action"] == (arm!="absent") and ready["production_path"] == (arm!="absent"),
             "expected installed (or absent) production plugin not established")
        hwnd=int(ready["hwnd"])
        need(win.pid(hwnd)==proc.pid and win.u.IsWindowVisible(hwnd),"native window not visible or wrong owner")
        # Catch deferred plugin startup stalls too. Separate from quiet CPU and burst probes.
        early_until=time.perf_counter()+2; early=[]; early_codes=[]
        while time.perf_counter()<early_until:
            tick=time.perf_counter()
            lat,code=win.ping(hwnd,proc.pid); early.append(lat); early_codes.append(code)
            time.sleep(max(0,.08-(time.perf_counter()-tick)))
        need(len(early)>=12,"insufficient early-loop probes; startup performance is inconclusive")
        # No main-thread pings or observer recurring work during the quiet slice.
        a=time.perf_counter(); cpu0=win.cpu(proc.pid)
        time.sleep(QUIET-5)
        cpu1=win.cpu(proc.pid); quiet_end=time.perf_counter()
        need(not (events/"burst.json").exists(), "idle slice overlapped active work")
        idle=(cpu1-cpu0)/(quiet_end-a)*100
        burst=wait_file(events/"burst.json",proc,10)
        need(burst["nonce"]==env["MO2_BENCH_NONCE"] and burst["native_vfs_verified"]==size,
             "unexpected burst marker or incomplete native VFS fixture")
        samples=[]; ping_errors=[]; advances=[]; oversleeps=[]; origin=time.perf_counter(); prev=origin
        expected=origin
        while not (events/"result.json").exists() and time.perf_counter()-origin<25:
            tick=time.perf_counter(); advances.append((tick-prev)*1000); prev=tick
            oversleeps.append(max(0,(tick-expected)*1000))
            elapsed,error=win.ping(hwnd,proc.pid)
            samples.append(elapsed); ping_errors.append(error)
            expected=max(tick+.08,time.perf_counter())
            time.sleep(max(0,expected-time.perf_counter()))
        result=wait_file(events/"result.json",proc,8)
        need(result["ok"] and result["nonce"]==env["MO2_BENCH_NONCE"],"native observer failure")
        work=[float(x) for x in result["work_ms"]]
        need(len(work)==STEPS and len(samples)>=60 and min(samples)>=0 and min(work)>=0,
             "missing active operation or independent window samples")
        row={"valid": max(oversleeps) < 150 and max(early+samples) < 250, "size":size,"block":block,"arm":arm,"payload_id":payload_id(size),
             "startup_ms":startup,"idle_core_pct":idle,"native_vfs_verified":burst["native_vfs_verified"],
             "native_fixture_esp_rows":burst["native_fixture_esp_rows"],
             "timeouts":(early_codes+ping_errors).count(1460),
             "failed_pings":sum(e is not None for e in early_codes+ping_errors),
             "early_wm_samples_ms":early,"early_wm_errors":early_codes,
             "early_wm_p95_ms":pct(early,.95),"early_wm_max_ms":max(early),
             "wm_win32_errors":ping_errors,"wm_samples_ms":samples,
             "wm_schedule_gaps_ms":advances,"observer_oversleep_ms":oversleeps,
             "wm_p95_ms":pct(samples,.95),"wm_p99_ms":pct(samples,.99),
             "work_p95_ms":pct(work,.95),"work_p99_ms":pct(work,.99),"work_max_ms":max(work),
             "wm_max_ms":max(samples),"quiet_cpu_window_ms":(quiet_end-a)*1000,
             "work_samples_ms":work,"work_round_legs": ["filter","data","refresh","plugins","clear","select"]}
        # Retain timeout values and failure evidence instead of dropping censored samples.
        dump_new(events/"metrics.json",row)
        need(proc.wait(timeout=8)==0,"unclean normal MO2 exit")
        return row
    finally:
        if proc.poll() is None:
            proc.terminate(); proc.wait(timeout=8)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--mo2-root",type=Path,required=True,help="pristine official MO2 2.5.2 expanded fixture directory")
    p.add_argument("--mo2-exe-sha256",required=True)
    p.add_argument("--candidate-plugin",type=Path,required=True)
    p.add_argument("--released-plugin",type=Path,required=True)
    p.add_argument("--candidate-commit",required=True)
    p.add_argument("--release-asset",type=Path,required=True)
    p.add_argument("--mo2-archive",type=Path,required=True)
    p.add_argument("--mo2-archive-sha256",required=True)
    p.add_argument("--candidate-plugin-sha256",required=True)
    p.add_argument("--fixture-game",type=Path,required=True,help="exact pinned current mo2 fixture game shim")
    p.add_argument("--preflight",action="store_true",help="three 40-row native runs; never an acceptance verdict")
    args=p.parse_args()
    need(os.name=="nt" and os.environ.get("GITHUB_ACTIONS")=="true" and os.environ.get("RUNNER_ENVIRONMENT")=="github-hosted",
         "refusing anything except a clean disposable GitHub-hosted Windows runner")
    need(all(HEX.fullmatch(x) for x in (args.mo2_exe_sha256,args.mo2_archive_sha256,args.candidate_plugin_sha256)) and
         re.fullmatch("[0-9a-f]{40}",args.candidate_commit),"missing trusted release/build identity")
    no_existing_mo2()
    temp=Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    mo=args.mo2_root.resolve(strict=True)
    need(mo.is_relative_to(temp) and mo!=temp,"upstream official expansion must be in RUNNER_TEMP")
    exe=mo/"ModOrganizer.exe"
    need(sha(exe)==args.mo2_exe_sha256 and (mo/"plugins/basic_games/basic_game.py").is_file(),
         "wrong upstream executable/fixture layout")
    need(not any((mo/n).exists() for n in ("ModOrganizer.ini","portable.txt","nxmhandler.ini","instance","synthetic-game","profiles","mods",
                                            "plugins/mo2_update_arsenal","plugins/modhunter_benchmark","plugins/modhunter_native_smoke")),
         "upstream directory must be pristine")
    inputs={a:getattr(args,a+"_plugin").resolve(strict=True) for a in ARMS[1:]}
    for a,path in inputs.items():
        need(path.is_relative_to(temp) and path!=temp,"stage compiled plugin inputs in RUNNER_TEMP")
        need(all((path/f).is_file() for f in ("plugin.py","__init__.py","web/index.html","bin/modhunter-cli.exe","bin/modhunter-qt-bridge.dll")),
             a+" payload is not the staged portable plugin")
    mo_size=1_200_000_000; plugin_size=220_000_000
    need(args.mo2_archive.stat().st_size==149660212 and
         sha(args.mo2_archive)==args.mo2_archive_sha256==MO2_PORTABLE_SHA256,
         "upstream MO2 portable archive identity mismatch")
    common=inspect_tree(mo,mo_size)
    hashes={a:inspect_tree(path,plugin_size) for a,path in inputs.items()}
    need(hashes["released"]==published_manifest(args.release_asset,PUBLISHED_SHA256),
         "released staged plugin differs from the real published ZIP bytes")
    need(hashes["candidate"]["plugin.py"]==args.candidate_plugin_sha256,
         "candidate runtime plugin.py does not match the intended source artifact")
    for manifest in hashes.values():
        need(not any("data" in f.split("/") or "__pycache__" in f.split("/") or f.endswith(".pyc") for f in manifest),
             "plugin contains user data or stale bytecode")
    game=args.fixture_game.resolve(strict=True)
    need(git_blob(game)=="80cddd4e283e997b867edfad2f9df2670d0a4b49","not the exact recognized CI game fixture")
    out=args.output.resolve()
    need(out.is_relative_to(temp) and out!=temp and str(out).isascii() and not any(c in str(out) for c in "()%"),"new ASCII scratch under RUNNER_TEMP is required")
    need(all(not out.is_relative_to(x) and not x.is_relative_to(out) for x in (mo,*inputs.values())),
         "benchmark output and inputs may not be nested")
    out.mkdir()
    dump_new(out/"provenance.json",{**RELEASE,"candidate_commit":args.candidate_commit,"host":platform.platform(),
        "python":sys.version.split()[0],"logical_cpus":os.cpu_count(),"mo2_exe_sha256":args.mo2_exe_sha256,
        "mo2_archive_sha256":args.mo2_archive_sha256,"mo2_archive_upstream_digest_published":False,
        "published_asset_sha256":PUBLISHED_SHA256,"candidate_plugin_py_sha256":args.candidate_plugin_sha256,
        "host_files":common,"plugin_files":hashes,"observer_sha256":sha(SOURCE/"observer/__init__.py"),"fixture_game_git_blob":git_blob(game),
        "driver_sha256":sha(Path(__file__)),"quiet_seconds":QUIET,"expected_active_operations":STEPS,"ping_timeout_ms":PING_LIMIT})
    win=Win(); all_rows=[]
    runs=list(order())[:3] if args.preflight else order()
    for size,block,arm in runs:
        no_existing_mo2()
        td=out/f"{size}-{block}-{arm}"; td.mkdir()
        root=td/"portable"; events=td/"events"; events.mkdir()
        same_tree_copy(mo,root,common,mo_size)
        stage(root,size)
        shutil.copy2(game,root/"plugins/basic_games/games/game_modhunter_smoke.py")
        shutil.copytree(SOURCE/"observer", root/"plugins/modhunter_benchmark", ignore=shutil.ignore_patterns("__pycache__"))
        if arm!="absent":
            same_tree_copy(inputs[arm],root/"plugins/mo2_update_arsenal",hashes[arm],plugin_size)
        try:
            all_rows.append(trial(root,events,size,block,arm,win))
            no_existing_mo2()
        except Exception as e:
            dump_new(events/"failure.json",{"status":"not qualified","error":str(e)})
            raise
        # Limit disk. Delete only this harness-created clone on success; keep raw measurement files.
        shutil.rmtree(root)
    if args.preflight:
        dump_new(out/"preflight.json",{"not_release_acceptance":True,"mode":"native three-arm wiring preflight","observed":all_rows})
        return 0 if all(r["valid"] and not r["failed_pings"] for r in all_rows) else 1
    guarded={str(s):deterministic_report(s,all_rows) for s in SIZES}
    met=all(x["within_guardrail"] for a in guarded.values() for b in a.values() for x in b.values())
    observed={}
    for size in SIZES:
        observed[str(size)]={}
        for arm in ARMS:
            rows=[r for r in all_rows if r["size"]==size and r["arm"]==arm]
            observed[str(size)][arm]={"trials":len(rows),
                  "median_startup_ms":pct([r["startup_ms"] for r in rows],.5),
                  "worst_wm_ms":max(r["wm_max_ms"] for r in rows),
                  "worst_early_wm_ms":max(r["early_wm_max_ms"] for r in rows),
                  "worst_work_ms":max(r["work_max_ms"] for r in rows),
                  "worst_trial_wm_p95_ms":max(r["wm_p95_ms"] for r in rows),
                  "worst_trial_work_p95_ms":max(r["work_p95_ms"] for r in rows),
                  "median_idle_core_pct":pct([r["idle_core_pct"] for r in rows],.5)}
    dump_new(out/"comparison.json",{"candidate_pilot_guardrails_met":met,"not_release_acceptance":True,
                                   "observed":observed,"paired_guardrails":guarded})
    return 0 if met else 1

def git_blob(p):
    b=p.read_bytes()
    return hashlib.sha1(f"blob {len(b)}\0".encode()+b).hexdigest()

if __name__=="__main__":
    raise SystemExit(main())
