"""Bind two existing Windows ZIPs and render one synthetic trace runner per arm."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import zipfile

HERE=Path(__file__).resolve().parent
PIN_SHA="2b7d277bd8f876c6fe255bd7e19be5a311e80010bbe91137325f81914791f3b1"
HOST_SHA="e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815"
HOST_SIZE=149660212
SOURCE_PREFIX="MO2/mo2_update_arsenal/"
HELPER=SOURCE_PREFIX+"bin/modhunter-qt-bridge.dll"
PINS={}
SCAFFOLD={
 "mo2_native_smoke.ps1":"320e09da7c61e590dce05dc255ebe66c8fa0862f236990445775f1e8cfd3bb45",
 "mo2_smoke_plugin/game_modhunter_smoke.py":"a315d7589a084d18473dc82e079fd170b119ac71368bc5fb684d32fc6cca6c71",
 "mo2_smoke_plugin/modhunter_native_smoke/__init__.py":"43dd1b849678e1d625113e621b585f22cf9f665324eeeb97c3184edcd54a4afb",
}

def check(ok, reason):
    if not ok: raise ValueError(reason)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_bytes(path, size, pin):
    st=path.lstat()
    check(stat.S_ISREG(st.st_mode) and not getattr(st,"st_file_attributes",0)&stat.FILE_ATTRIBUTE_REPARSE_POINT
          and st.st_size==size, "Unexpected pinned input file wrapper or size")
    raw=path.read_bytes()
    check(digest(raw)==pin, "Pinned input SHA256 differs")
    return raw

def entry(z,path,size=None):
    entries=[i for i in z.infolist() if i.filename==path]
    check(len(entries)==1,"Duplicate or missing allowlisted member")
    i=entries[0]
    check(not i.is_dir() and stat.S_IFMT(i.external_attr>>16) in (0,stat.S_IFREG)
          and not i.external_attr&0x400 and not i.flag_bits&1 and 0<i.file_size<=32*1024**2
          and (size is None or i.file_size==size),"Nonregular or unbounded allowlisted member")
    return z.read(i)

def inspect_arm(input_path, arm, pins):
    raw=file_bytes(input_path,pins["outerSize"],pins["outerSha256"])
    if arm=="B":
        with zipfile.ZipFile(io.BytesIO(raw)) as outside:
            nested=entry(outside,"Modhunter-Rust-Windows.zip",pins["innerSize"])
            raw_bridge=entry(outside,"qt-bridge/Release/modhunter-qt-bridge.dll",pins["bridgeSize"])
        raw=nested
    check(len(raw)==pins["innerSize"] and digest(raw)==pins["innerSha256"],"Nested portable ZIP differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as pkg:
        check(json.loads(entry(pkg,"BUILD_INFO.json"))==pins["build"],"Version/commit/workflow binding failed")
        names={i.filename.removeprefix(SOURCE_PREFIX) for i in pkg.infolist()
               if i.filename.startswith(SOURCE_PREFIX) and not i.is_dir() and not i.filename.removeprefix(SOURCE_PREFIX).startswith("bin/")}
        check(names==set(pins["src"]) and len(names)==48,"Expected exact 48 source members")
        found={}
        for n in sorted(names):
            leaf=PurePosixPath(n)
            check(leaf.as_posix()==n and not leaf.is_absolute() and ".." not in leaf.parts and "\\" not in n and ":" not in n,
                  "Disallowed source member")
            b=entry(pkg,SOURCE_PREFIX+n)
            check(digest(b)==pins["src"][n],"Installed source SHA256 failed")
            found[n]=b
        bridge=entry(pkg,HELPER,pins["bridgeSize"])
        check(bridge.startswith(b"MZ") and digest(bridge)==pins["bridgeSha256"],"Shipped DLL pin failed")
        if arm=="B":
            check(bridge==raw_bridge,"Raw job helper does not match held packaged helper")
    return found,bridge

def once(s,old,new):
    check(s.count(old)==1,"Detached test-template binding failed")
    return s.replace(old,new)

def constants(s, pin):
    src=pin["src"]
    digestor=hashlib.sha256()
    for n,h in sorted(src.items()):digestor.update(n.encode()+b"\0"+bytes.fromhex(h))
    new_d={"VERSION":repr(pin["build"]["commit"]),"PACKAGE_PIN":repr(digestor.hexdigest())}
    result={n:src[n] for n in ("plugin.py","model.py","bridge.py","refresh_work.py","snapshot_projection.py",
                               "source_registry.py","version.py")}
    new_d["REFRESH_PINS"]=repr(result)
    module=ast.parse(s)
    lines=s.splitlines(keepends=True)
    for tree in reversed(module.body):
        if isinstance(tree,ast.Assign) and len(tree.targets)==1 and isinstance(tree.targets[0],ast.Name):
            name=tree.targets[0].id
            if name in new_d:
                lines[tree.lineno-1:tree.end_lineno]=[f"{name} = {new_d.pop(name)}\n"]
    check(not new_d,"Missing observer provenance constant")
    return "".join(lines)

def renderer(arm, installed, pins, root, scaffold, host):
    repo=root/("input-"+arm)
    for name,b in installed.items():
        p=repo/"mo2_update_arsenal"/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(b)
    for name,b in scaffold.items():
        p=repo/"native/scripts"/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(b)
    template=HERE/"observer"
    stager=root/("stager-"+arm)
    stager.mkdir()
    for name in ("trace.py","stage.py"):
        shutil.copyfile(template/name,stager/name)
    (stager/"fixture.py").write_text(constants((template/"fixture-template.py").read_text(),pins),newline="\n")
    runner=(template/"runner.py").read_text()
    runner=once(runner,"        self._finish(True, None, timing=stats, startupTrace=startup_trace,",
        """        from .wire import catalog_digests
        ready_digests = catalog_digests(witness, self.server.snapshot_response_bytes(), self._perf_generation)
        self._finish(True, None, timing=stats, startupTrace=startup_trace, readyCatalogHashes=ready_digests,""")
    (stager/"runner.py").write_text(runner,newline="\n")
    output=root/("arm-"+arm)
    subprocess.run([sys.executable,stager/"stage.py","--repository",repo,"--output",output,"--rows","11207"],check=True)
    shutil.copyfile(HERE/"wire.py",output/"mo2_smoke_plugin/modhunter_native_smoke/wire.py")
    shutil.copyfile(HERE/"seal_fixture.py",output/"seal_fixture.py")
    script=output/"stock_scale.ps1"
    s=script.read_text()
    s=once(s,"    [string]$DesktopExecutable = '',", "    [string]$DesktopExecutable = '',\n    [string]$PinnedMo2Archive = '',")
    s=once(s,"$Archive = Join-Path $WorkDirectory 'Mod.Organizer-2.5.2.7z'",
           "$Archive = [IO.Path]::GetFullPath($PinnedMo2Archive)\n"
           "if (!(Test-Path -LiteralPath $Archive -PathType Leaf)) { throw 'No immutable pre-staged stock archive' }")
    s=once(s,"    Invoke-WebRequest -Uri $ReleaseUrl -OutFile $Archive -TimeoutSec 300 -MaximumRetryCount 3 -RetryIntervalSec 3",
           "    # This carrier verifies and reuses a read-only stock archive; it never downloads inside a lane.")
    s=once(s,"'fixture.py', 'trace.py')","'fixture.py', 'trace.py', 'wire.py')")
    s=once(s,"    Write-Utf8File (Join-Path $PortableRoot 'portable.txt')",
           "    & $FixturePython.Source (Join-Path $PSScriptRoot 'seal_fixture.py') create $InstanceRoot\n"
           "    if ($LASTEXITCODE -ne 0) { throw 'Generated fixture seal failed' }\n"
           "    Write-Utf8File (Join-Path $PortableRoot 'portable.txt')")
    s=once(s,"    $Summary['nativeReceipt'] = $Receipt",
           "    $FixtureSeal = & $FixturePython.Source (Join-Path $PSScriptRoot 'seal_fixture.py') verify $InstanceRoot\n"
           "    if ($LASTEXITCODE -ne 0 -or $FixtureSeal -notmatch '^[0-9a-f]{64}$') { throw 'Generated fixture changed during stock launch' }\n"
           "    $Summary['fixtureInputSeal'] = $FixtureSeal\n"
           "    $Summary['nativeReceipt'] = $Receipt")
    script.write_text(s,newline="\n")
    return repo

def build(repo, base, candidate, host, root, *, verify_only=False):
    raw=(HERE/"inputs.json").read_bytes()
    check(digest(raw)==PIN_SHA,"Local pin manifest edited")
    pins=json.loads(raw)
    check(len(pins)==2 and len(pins['A']['src'])==48 and pins['A']['src'].keys()==pins['B']['src'].keys()
          and {n for n,v in pins['A']['src'].items() if pins['B']['src'][n]!=v}=={'source_registry.py','version.py'},
          "Unexpected comparison source delta")
    a,bridge=inspect_arm(base,'A',pins['A'])
    b,_=inspect_arm(candidate,'B',pins['B'])
    file_bytes(host,HOST_SIZE,HOST_SHA) if host else None
    if verify_only:
        return
    check(host is not None,"Real carrier requires pinned stock tool archive")
    check(not root.exists() and not root.is_symlink() and not root.resolve().is_relative_to(repo.resolve()),"Expected a new disposable stage root")
    scaffold={name:(repo/"native/scripts"/name).read_bytes() for name in SCAFFOLD}
    check(all(digest(scaffold[n])==h for n,h in SCAFFOLD.items()),"Exact shipped baseline scaffold differs")
    root.mkdir()
    (root/'controlled-qt-bridge.dll').write_bytes(bridge)
    for arm,files in (('A',a),('B',b)):renderer(arm,files,pins[arm],root,scaffold,host)
    (root/"payload-verification.json").write_text(json.dumps({
       "arms":{k:{h:v[h] for h in ("build","outerSha256","innerSha256","bridgeSha256")} for k,v in pins.items()},
       "controlledHelperSha256":digest(bridge),"stockMo2Sha256":HOST_SHA,"sourceDifferences":["source_registry.py","version.py"],
       "scope":"Exact Python/web source comparison only; common Test 2 Qt bridge; no Rust CLI, WebView2, installer or Gallery acceptance."
       },sort_keys=True,indent=2)+"\n")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--repository",type=Path,required=True)
    p.add_argument("--baseline",type=Path,required=True)
    p.add_argument("--candidate",type=Path,required=True)
    p.add_argument("--host",type=Path)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--verify-only",action="store_true")
    a=p.parse_args()
    build(a.repository,a.baseline,a.candidate,a.host,a.root,verify_only=a.verify_only)
