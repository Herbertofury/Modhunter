"""Reproducible local-only HTML bundler. Run: python build.py"""
from __future__ import annotations
from pathlib import Path
import hashlib, json, os
ROOT=Path(__file__).resolve().parent
WEB=ROOT/'mo2_update_arsenal'/'web'
script=(WEB/'app.js').read_text(encoding='utf8').replace('/*__POWERSHELL__*/',json.dumps((WEB/'MO2_Source_Exporter.ps1').read_text(encoding='utf8'),ensure_ascii=False))
script=(WEB/'mod_insights.js').read_text(encoding='utf8')+'\n'+script
script=(WEB/'source_insights.js').read_text(encoding='utf8')+'\n'+script
css=(WEB/'style.css').read_text(encoding='utf8')+'\n'+(WEB/'aether_fx.css').read_text(encoding='utf8')+'\n'+(WEB/'realm.css').read_text(encoding='utf8')+'\n'+(WEB/'siege.css').read_text(encoding='utf8')+'\n'+(WEB/'atelier.css').read_text(encoding='utf8')
model=json.loads((WEB/'catalog.json').read_text(encoding='utf8'))
css+='\n'+(WEB/'mod_insights.css').read_text(encoding='utf8')
css+='\n'+(WEB/'source_insights.css').read_text(encoding='utf8')
page=(WEB/'app.template.html').read_text(encoding='utf8').replace('/*__STYLE__*/',css).replace('/*__SCRIPT__*/',script).replace('/*__AETHER_SCRIPT__*/',(WEB/'aether_fx.js').read_text(encoding='utf8')).replace('/*__REALM_SCRIPT__*/',(WEB/'realm.js').read_text(encoding='utf8')).replace('<!--__REALM_LAYOUT__-->',(WEB/'realm_layout.html').read_text(encoding='utf8').replace('<!--__SIEGE_LAYOUT__-->',(WEB/'siege_layout.html').read_text(encoding='utf8'))).replace('/*__SIEGE_SCRIPT__*/',(WEB/'siege.js').read_text(encoding='utf8')).replace('/*__CATALOG__*/',json.dumps(model,ensure_ascii=False,separators=(',',':')).replace('</','<\\/'))
assert '/*__' not in page
assert '<!--__MO2_BOOT__-->' in page
(WEB/'index.html').write_text(page,encoding='utf8')
standalone=ROOT/'web'/'Modhunter.html'
standalone.parent.mkdir(parents=True,exist_ok=True)
standalone.write_text(page,encoding='utf8')
out=Path(os.environ.get('MODHUNTER_ARTIFACT_DIR','/mnt/data'))/'MO2_Update_Arsenal.html'
out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(page,encoding='utf8')
print(json.dumps({'file':str(out),'modCount':len(model['items']),'groups':len(model['groups']),'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()},indent=2))
