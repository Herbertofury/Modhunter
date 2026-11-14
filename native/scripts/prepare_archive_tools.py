"""Stage pinned official 7-Zip 26.06 helpers, license and reproducible provenance."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys, tarfile, tempfile, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
VERSION='26.06'
BASE='https://github.com/ip7z/7zip/releases/download/26.06/'
PACKAGES={
 'linux':('7z2606-linux-x64.tar.xz','2b539cf067b2ad48e1baa5093dbaa8818b73542d1222a0ffde9dc5f2922ea62e'),
 'windows':('7z2606-x64.exe','9b697f65ecc2d9b0152f5ee96d72dde522a7827690349d9307935dacf40f42ec')}
PINNED={
 'linux/7zz':'f94066e93f9a807547a3bff1021b3ec79d5ea4f57dfead018e9698182ad283b5',
 'windows/7z.exe':'dc0493429fe28136fcb177a0e77c9875cbb3b0bff5f2f8eeba4df302e98cef76',
 'windows/7z.dll':'500db9eb17f3c21b729ba41dd02ecfe1823da05028c7e4ba129084cc74cc364e'}
def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
 return h.hexdigest()
def download(name,directory):
 filename,expected=PACKAGES[name]
 target=directory/filename
 with urllib.request.urlopen(BASE+filename,timeout=60) as source,target.open('wb') as out:
  shutil.copyfileobj(source,out)
 if digest(target)!=expected: raise ValueError('Official archive digest mismatch: '+filename)
 return target
def fetch(directory):
 (directory/'windows').mkdir()
 windows=download('windows',directory)
 if sys.platform=='win32':
  unpacker=shutil.which('7z') or str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'7-Zip/7z.exe')
  if not Path(unpacker).is_file(): raise RuntimeError('The Windows build needs 7-Zip to unpack its verified official helper distribution.')
 else:
  (directory/'linux').mkdir()
  linux=download('linux',directory)
  with tarfile.open(linux,'r:xz') as archive: archive.extractall(directory/'linux',filter='data')
  unpacker=str(directory/'linux/7zz')
  Path(unpacker).chmod(0o755)
 subprocess.run([unpacker,'x',str(windows),'-o'+str(directory/'windows'),'-y'],check=True,stdout=subprocess.DEVNULL)
 return directory
def prepare(inputs):
 required=['windows/7z.exe','windows/7z.dll']+([] if sys.platform=='win32' else ['linux/7zz'])
 for relative in required:
  if digest(inputs/relative)!=PINNED[relative]: raise ValueError('Pinned helper mismatch: '+relative)
 license_path=inputs/'windows/License.txt'
 if not license_path.is_file(): raise ValueError('Official 7-Zip license is required.')
 targets=[ROOT/'native/resources/archive-tools',ROOT/'native/target/archive-tools',ROOT/'native/target/debug/archive-tools',ROOT/'native/target/release/archive-tools']
 for target in targets:
  target.mkdir(parents=True,exist_ok=True)
  for relative in required: shutil.copy2(inputs/relative,target/Path(relative).name)
  if sys.platform!='win32': (target/'7zz').chmod(0o755)
  shutil.copy2(license_path,target/'License.txt')
  manifest={'schema':1,'version':VERSION,'source':BASE,'files':{Path(p).name:PINNED[p] for p in required},'licenseSha256':digest(license_path),'packages':{key:{'url':BASE+filename,'sha256':sha} for key,(filename,sha) in PACKAGES.items()}}
  (target/'provenance.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'ok':True,'version':VERSION,'targets':[str(p) for p in targets]}))
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--inputs',type=Path,default=os.environ.get('MODHUNTER_ARCHIVE_INPUTS'))
 args=parser.parse_args()
 if args.inputs: prepare(Path(args.inputs))
 else:
  with tempfile.TemporaryDirectory(prefix='modhunter-official-archive-tools-') as d: prepare(fetch(Path(d)))
if __name__=='__main__': main()
