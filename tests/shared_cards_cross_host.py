"""Explicit interoperability acceptance: pass an independently built protocol CLI.

Usage: python tests/shared_cards_cross_host.py /path/to/gs-card-save
Not a substitute for packaged Windows/MO2 gameplay acceptance.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main(helper):
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as folder:
        env={**os.environ,'HOME':folder,'LOCALAPPDATA':folder,'XDG_DATA_HOME':folder}
        value={k:{} for k in ('match','collection','heroes','packs','progression','quests','achievements','stats','cosmetics','settings')}
        value.update(version=3,decks=[],futureUnknown={'retained':True})
        def invoke(request):
            completed=subprocess.run([str(Path(helper).resolve())],input=json.dumps(request),text=True,capture_output=True,check=True,env=env)
            response=json.loads(completed.stdout);assert response['ok'],response
            return response['result']
        for revision,writer in enumerate(['gamesync','modhunter'],1):
            value['progression']['xp']=revision
            invoke({'action':'commit','input':{'slot':'main','operation':f'crosshost-{revision}','writer':writer,'ruleset':'gamesync-cardgame-v3','expected_revision':revision-1,'profile':value}})
            # A fresh ModHunter process reads the same location after writer exits.
            observed=subprocess.run([sys.executable,'-m','mo2_update_arsenal.shared_cards'],cwd=root,env=env,text=True,capture_output=True,check=True)
            status=json.loads(observed.stdout);assert status['state']=='available',status
            assert status['revision']==revision and not status['playable'],status
            assert invoke({'action':'load','slot':'main'})['profile']==value
        print('PASS: two native writer processes, independent ModHunter reads same per-user store; full profile preserved')

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit(__doc__)
    main(sys.argv[1])
