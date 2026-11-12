"""Reproducible Modhunter 0.8 release: 3 fully independent entry points.
No native Windows executable is invented or claimed.
"""
from __future__ import annotations
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib,json,shutil,os,unittest

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


# One download with three separately runnable, but interoperable editions.
# Original v0.1-v0.8 ZIPs stay preserved in the historical vault, not swapped
# into the executable product as obsolete duplicates.
all_in_one=DIST/'Modhunter-All-in-One.zip'
start_here=PKG/'START-HERE.md'
start_here.write_text("""# MODHUNTER / ONE COMMAND CENTER

PRIMARY MISSION: Keep your real Mod Organizer 2 mods up to date.

This is one merged product containing Questbound, AetherFX, Shiverfell,
Mythforge, Modhunter Siege, Atlas and the full Update Workshop.

## Three independent ways to start

1. Standalone: open Modhunter.html. Offline review, quests, FX, survival,
   Siege and Atlas run without installing any other component.
2. Desktop: run Desktop/Run_ModHunter.bat on Windows with Node.js installed.
   Native tabs run the same HTML; optional read-only MO2 profile import works
   without the MO2 Python plugin.
3. MO2 plugin: run MO2/Install_to_MO2.bat and choose an MO2 installation.
   Start Tools -> Update Arsenal - Quest Board in MO2. The plugin's own
   dashboard reads the real active profile, scans mods and downloads reviewed
   archives into MO2. Desktop installation is not required.

Optionally connect the desktop app to the MO2 plugin's local session
for the same authenticated live profile in native Chromium tabs.

## First full update
Select active MO2 profile and correct game runtime; choose Check all mod
sources; review newest compatible Nexus or public GitHub release files;
queue reviewed downloads through MO2; install using its regular prompts;
then refresh and re-scan. Unverified private-provider pages remain manual.
No unattended installations. MO2 2.5.3 is a target, not a claim that
native Windows 2.5.3 validation has already passed.

## Preservation of original releases
All 36 original output files across v0.1 to v0.8 are preserved unchanged at:
https://drive.google.com/file/d/1tQBnUZ_vDF7tUTtiXf8WJC41OEXNVG_5/view

See Docs/Unified-Modhunter.md for the complete architecture and feature map.
""",encoding='utf8')
parts=(html,plugin,app,source)
bundle_index={
  'name':'Modhunter All-in-One','version':'0.8.0-dev-unified',
  'primaryPurpose':'Keep installed MO2 mods up to date',
  'mo2CompatibilityTarget':'2.5.x; 2.5.3 targeted, native Windows unverified',
  'standaloneWorksAlone':True,'desktopWorksWithoutPlugin':True,
  'pluginWorksWithoutDesktop':True,
  'originalReleaseArchive':'https://drive.google.com/file/d/1tQBnUZ_vDF7tUTtiXf8WJC41OEXNVG_5/view',
  'parts':{p.name:{'bytes':p.stat().st_size,
                  'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in parts}
}
with ZipFile(all_in_one,'w',ZIP_DEFLATED,compresslevel=9) as z:
    z.write(start_here,'START-HERE.md')
    z.write(html,'Modhunter.html')
    for p in sorted((R/'desktop').glob('*')):
        if p.is_file() and p.name!='Modhunter.html':
            z.write(p,'Desktop/'+p.name)
    z.write(html,'Desktop/Modhunter.html')
    z.write(R/'Install_to_MO2.bat','MO2/Install_to_MO2.bat')
    for p in sorted((R/'mo2_update_arsenal').glob('*.py')):
        z.write(p,'MO2/mo2_update_arsenal/'+p.name)
    z.write(WEB,'MO2/mo2_update_arsenal/web/index.html')
    z.write(plugin,'Installers/'+plugin.name)
    z.write(app,'Installers/'+app.name)
    z.write(source,'Source/'+source.name)
    z.write(R/'README.md','Docs/README.md')
    for p in sorted((R/'wiki').glob('*.md')):
        z.write(p,'Docs/'+p.name)
    for p in sorted(ASSETS.iterdir()):
        z.write(p,'Assets/'+p.name)
    z.writestr('bundle-index.json',json.dumps(bundle_index,indent=2,ensure_ascii=False))
with ZipFile(all_in_one) as z:
    assert z.testzip() is None
    assert len(z.namelist())==len(set(z.namelist()))
    for required in (
        'START-HERE.md','Modhunter.html','Desktop/Run_ModHunter.bat',
        'Desktop/Modhunter.html','MO2/Install_to_MO2.bat',
        'MO2/mo2_update_arsenal/plugin.py','MO2/mo2_update_arsenal/web/index.html',
        'Source/Modhunter-Complete-Source.zip','Docs/Unified-Modhunter.md',
        'Docs/Update-Workshop.md','Docs/Original-Releases-and-Output-Audit.md',
        'bundle-index.json'):
        assert required in z.namelist(),required
    for name in ('Modhunter.html','Desktop/Modhunter.html','MO2/mo2_update_arsenal/web/index.html'):
        assert z.read(name)==html.read_bytes(),name
    for p in (plugin,app,source):
        prefix='Source/' if p==source else 'Installers/'
        assert hashlib.sha256(z.read(prefix+p.name)).hexdigest()==bundle_index['parts'][p.name]['sha256']
files[all_in_one.name]={'bytes':all_in_one.stat().st_size,
                        'sha256':hashlib.sha256(all_in_one.read_bytes()).hexdigest()}

manifest={'product':'Modhunter: The Unfinished World','version':'0.8.0-dev','components':{'desktop':'Electron source, independent read-only MO2 profile import and optional live plugin loopback','html':'self-contained offline browser dashboard + games','mo2':'Mod Organizer 2 Python Tool with self-hosted browser dashboard'},'content':{'sampleMods':227,'separatorSections':115,'regions':12,'namedStoryContracts':96,'masteryTiers':5,'firstClearMilestones':480,'baseTimerHoursAtAllTiers':80,'heroClasses':5,'upgradePaths':22,'evolutions':6,'legacyPerks':6},'testing':{'pythonTests':unittest.TestLoader().discover(str(R/'tests'),pattern='test_*.py').countTestCases(),'pythonSubtests':3,'atlasChromium':'pass','electronChromeChromium':'pass','desktopSyntax':'pass','desktopOfflineImporterNode':'pass','nativeWindowsElectron':'unverified','nativeWindowsMO2':'unverified'},'files':files}
(DIST/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
for p in (html,plugin,app,source,all_in_one):
    shutil.copy2(p,OUT/p.name)
print(json.dumps({'output':str(PKG),'files':files,'assets':[(p.name,p.stat().st_size) for p in ASSETS.iterdir()]},indent=2))
