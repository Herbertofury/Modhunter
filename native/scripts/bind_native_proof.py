"""Bind an actual native fixture receipt to unchanged source and executable bytes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()
def source_names():
    paths=subprocess.check_output(['git','ls-files','-z','native/crates','native/ui','native/scripts/bind_native_proof.py','native/scripts/watchtower_smoke.ps1','native/scripts/windows_smoke.ps1','native/Cargo.toml','native/Cargo.lock'],cwd=ROOT).decode().split('\0')
    return sorted(p for p in paths if p and (p.endswith(('.rs','.js','.css','.html','.toml','.json','.ps1','.py')) or p=='native/Cargo.lock'))
def capture_identity(executable):
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    # Keep the exact all-repository status that decided this identity. A later
    # filtered query can hide a root-level build output or observe a new state.
    porcelain=subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=ROOT)
    return ({'schema':1,'candidateCommit':commit,'sourceDirty':bool(porcelain),
        'executableSha256':sha(executable),'sourceFiles':{p:sha(ROOT/p) for p in source_names()}},porcelain)
def identity(executable):
    return capture_identity(executable)[0]
def dirty_details(porcelain):
    changes=porcelain.decode('utf-8',errors='backslashreplace').splitlines()
    details='; '.join(line[:240] for line in changes[:12])
    if len(changes)>12:details+=f'; and {len(changes)-12} more repository paths'
    return details or 'Git returned a nonempty status; no clean source identity was accepted.'
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args();current,porcelain=capture_identity(args.executable)
    if current['sourceDirty']:
        raise RuntimeError('Native acceptance must start and finish on an unchanged clean source tree. '+dirty_details(porcelain))
    if not args.report:
        args.checkpoint.write_text(json.dumps(current,sort_keys=True),encoding='utf-8');return
    before=json.loads(args.checkpoint.read_text('utf-8'))
    if before!=current:raise RuntimeError('Source or executable changed during native acceptance')
    report=json.loads(args.report.read_text('utf-8-sig'))
    if report.get('ok') is not True:raise RuntimeError('Cannot bind a failed native fixture receipt')
    report.update(nativeBindingSchema=1,candidateCommit=current['candidateCommit'],sourceDirty=False,
        executableSha256=current['executableSha256'],sourceFiles=current['sourceFiles'])
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'report':args.report.name,'candidateCommit':current['candidateCommit'],'executableSha256':current['executableSha256'],'sourceFiles':len(current['sourceFiles'])}))
if __name__=='__main__':main()
