"""Bundle the exact shared dashboard into the Rust binary; no Node install needed."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
subprocess.run([sys.executable, str(ROOT / 'native/scripts/prepare_locales.py')], cwd=ROOT, check=True)
env = dict(os.environ, MODHUNTER_ARTIFACT_DIR=str(ROOT / 'native' / 'target' / 'assets'))
subprocess.run([sys.executable, str(ROOT / 'build.py')], cwd=ROOT, env=env, check=True)
ui = ROOT / 'native' / 'ui'
ui.mkdir(exist_ok=True)
shutil.copyfile(ROOT / 'mo2_update_arsenal' / 'web' / 'index.html', ui / 'Modhunter.html')
shutil.copyfile(ROOT / 'desktop' / 'chrome.css', ui / 'chrome.css')
print(json.dumps({'dashboardSha256': hashlib.sha256((ui/'Modhunter.html').read_bytes()).hexdigest(),
                  'bytes': (ui/'Modhunter.html').stat().st_size}))
