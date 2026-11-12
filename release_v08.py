"""Reproducible Modhunter 0.8 release: 3 fully independent entry points.
No native Windows executable is invented or claimed.
"""
from __future__ import annotations
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib,json,shutil,os

R=Path(__file__).resolve().parent
OUT=Path(os.environ.get('MODHUNTER_ARTIFACT_DIR','/mnt/data'))
PKG=OUT/'Modhunter-0.8'
DIST=PKG/'dist'
ASSETS=PKG/'assets'
DIST.mkdir(parents=True,exist_ok=True);ASSETS.mkdir(parents=True,exist_ok=True)
WEB=R/'mo2_update_arsenal/web/index.html'
assert WEB.exists() and WEB.stat().st_size>450_000
assert 'REGION_SAGAS' in WEB.read_text('utf8') and 'unfinished bestiary' in WEB.read_text('utf8').lower()
html=DIST/'Modhunter.html';shutil.copy2(WEB,html)
for src,dst in [('atlas-map.png','atlas-map.png'),('modhunter-lore-bestiary.png','bestiary.png'),('desktop-tabbar.png','desktop-tabs.png')]:
    p=R/'screenshots'/src
    assert p.is_file(),p
    shutil.copy2(p,ASSETS/dst)
plugin=DIST/'Modhunter-MO2-Plugin.zip'
app=DIST/'Modhunter-Desktop-Source.zip'
source=DIST/'Modhunter-Complete-Source.zip'

def put(z,p,asname=None):
    z.write(p,asname or p.relative_to(R).as_posix())

with ZipFile(plugin,'w',ZIP_DEFLATED,compresslevel=9) as z:
    put(z,R/'Install_to_MO2.bat')
    for p in sorted((R/'mo2_update_arsenal').glob('*.py')):put(z,p)
    put(z,WEB)
    put(z,R/'README.md')
    put(z,R/'wiki/MO2-Plugin.md')
    put(z,R/'wiki/Rewards-and-Integrity.md')

with ZipFile(app,'w',ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted((R/'desktop').glob('*')):
        if p.is_file():put(z,p)
    put(z,WEB)
    put(z,R/'README.md')
    put(z,R/'wiki/Getting-Started.md')

allowed_root={'README.md','Install_to_MO2.bat','build.py','release_v08.py','release_modhunter.py','upgrade_atlas_v08.py'}
keep_images={'atlas-map.png','atlas-campaign.png','desktop-tabbar.png','modhunter-lore-bestiary.png','modhunter-lore-npc-boss.png','modhunter-menu.png','shiverfell-open-world.png'}
with ZipFile(source,'w',ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted(R.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(R)
        if p.name.startswith('.') or '__pycache__' in p.parts or '.pytest_cache' in p.parts or 'node_modules' in p.parts:continue
        if 'legacy' in p.parts:continue
        if p.suffix in ('.pyc','.log','.tmp'):continue
        if p.name in ('js-compiled.js','build-output.json','build-release.json'):continue
        if 'screenshots' in p.parts and p.name not in keep_images:continue
        if len(rel.parts)==1 and rel.name not in allowed_root:continue
        if rel.parts[0] not in {'mo2_update_arsenal','desktop','wiki','docs','tests','scripts','.github','screenshots'} and len(rel.parts)>1:continue
        put(z,p)
    put(z,html,'web/Modhunter.html')
    for p in sorted(ASSETS.iterdir()): z.write(p,'assets/'+p.name)

files={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (html,plugin,app,source)}
for p in (plugin,app,source):
    with ZipFile(p) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(set(z.namelist()))
        if p==plugin:assert 'mo2_update_arsenal/web/index.html' in z.namelist()
        if p==app:assert 'desktop/main.cjs' in z.namelist() and 'mo2_update_arsenal/web/index.html' in z.namelist()
        if p==source:assert 'wiki/Home.md' in z.namelist() and 'tests/atlas_qa.py' in z.namelist()

manifest={'product':'Modhunter: The Unfinished World','version':'0.8.0-dev','components':{'desktop':'Electron 45 source, independent offline + optional MO2 loopback','html':'self-contained offline browser dashboard + games','mo2':'Mod Organizer 2 Python Tool with self-hosted browser dashboard'},'content':{'sampleMods':227,'separatorSections':115,'regions':12,'namedStoryContracts':96,'masteryTiers':5,'firstClearMilestones':480,'baseTimerHoursAtAllTiers':80,'heroClasses':5,'upgradePaths':22,'evolutions':6,'legacyPerks':6},'testing':{'pythonTests':68,'pythonSubtests':3,'atlasChromium':'pass','electronChromeChromium':'pass','desktopSyntax':'pass','nativeWindowsElectron':'unverified','nativeWindowsMO2':'unverified'},'files':files}
(DIST/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
for p in (html,plugin,app,source):
    shutil.copy2(p,OUT/p.name)
print(json.dumps({'output':str(PKG),'files':files,'assets':[(p.name,p.stat().st_size) for p in ASSETS.iterdir()]},indent=2))
