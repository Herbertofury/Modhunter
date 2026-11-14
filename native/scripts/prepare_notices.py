"""Bundle exact locked Cargo package license metadata and source license texts."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
def main():
    meta=json.loads(subprocess.check_output(['cargo','metadata','--manifest-path',str(ROOT/'native/Cargo.toml'),'--locked','--format-version','1'],text=True))
    packages=[]
    for package in sorted(meta['packages'],key=lambda p:(p['name'],p['version'])):
        if package['id'] in meta['workspace_members']:
            continue
        directory=Path(package['manifest_path']).parent
        candidates=[]
        if package.get('license_file'):
            candidates.append(directory/package['license_file'])
        for subdir in [directory,directory/'licenses',directory/'license']:
            if subdir.is_dir():
                candidates.extend(p for p in subdir.iterdir() if p.is_file() and p.name.lower().startswith(('license','licence','copying','copyright','notice')))
        texts=[]
        for path in sorted(set(candidates)):
            if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:
                raise ValueError('Invalid package license text: '+package['name'])
            texts.append({'file':str(path.relative_to(directory)).replace(os.sep,'/'),'text':path.read_text('utf-8',errors='replace')})
        packages.append({'name':package['name'],'version':package['version'],'license':package.get('license'),'repository':package.get('repository'),'texts':texts})
    document={'schema':1,'scope':'Exact locked Cargo dependency source metadata. Some packages apply only to a supported build target; external runtime distributions retain their own terms.','packages':packages}
    path=ROOT/'native/resources/THIRD_PARTY_NOTICES.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'packages':len(packages),'licenseTexts':sum(len(p['texts']) for p in packages),'bytes':path.stat().st_size}))
if __name__=='__main__':main()
