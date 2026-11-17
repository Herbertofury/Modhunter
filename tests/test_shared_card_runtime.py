import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mo2_update_arsenal.shared_card_runtime import invoke_shared_card_runtime

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'HOME':self.temp.name,'LOCALAPPDATA':self.temp.name,'XDG_DATA_HOME':self.temp.name})
        env.start();self.addCleanup(env.stop)
    def test_absent_runtime_does_not_create_store(self):
        self.assertEqual(invoke_shared_card_runtime({'action':'load','slot':'main'})['outcome'],'not-started')
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])
    def test_no_caller_executable_writer_or_ruleset(self):
        for data in [{'action':'load','slot':'main','path':'/tmp/tool'}, {'action':'commit','input':{'writer':'gamesync'}}, {'action':'delete','slot':'main'}, {'action':'commit','input':{'slot':'main','operation':'a','expectedRevision':True,'profile':{}}}]:
            self.assertEqual(invoke_shared_card_runtime(data)['outcome'],'not-started')
    def test_bad_manifest_never_executes(self):
        from mo2_update_arsenal.shared_cards import shared_cards_path
        folder=shared_cards_path().parent/'runtime-v1';folder.mkdir(parents=True)
        name='gs-card-save.exe' if os.name=='nt' else 'gs-card-save'
        (folder/name).write_bytes(b'not executable')
        (folder/'manifest.json').write_text(json.dumps({'protocol':1,'executable':name,'sha256':'wrong'}))
        with patch('mo2_update_arsenal.shared_card_runtime.subprocess.Popen') as launch:
            self.assertEqual(invoke_shared_card_runtime({'action':'load','slot':'main'})['outcome'],'not-started')
            launch.assert_not_called()
    def test_non_finite_profile_refused_before_launch(self):
        data={'action':'commit','input':{'slot':'main','operation':'x','expectedRevision':0,'profile':{'bad':float('inf')}}}
        self.assertEqual(invoke_shared_card_runtime(data)['outcome'],'not-started')
    def test_process_outcomes_preserve_uncertainty(self):
        from subprocess import TimeoutExpired
        cases=[({'ok':True,'result':{'revision':2}},7,'unknown'),
               ({'ok':False},1,'unknown'),({'ok':True},0,'unknown'),
               ({'ok':False,'error':'Conflict'},0,'unknown'),
               ({'ok':False,'error':'Conflict'},1,'rejected')]
        for response,code,outcome in cases:
            class Child:
                returncode=code
                def communicate(self,raw,timeout):
                    output.write(json.dumps(response).encode())
            def launch(*args,**kwargs):
                nonlocal output
                output=kwargs['stdout'];return Child()
            output=None
            with patch('mo2_update_arsenal.shared_card_runtime._runtime',return_value=Path('/known/runtime')), patch('mo2_update_arsenal.shared_card_runtime.subprocess.Popen',side_effect=launch):
                self.assertEqual(invoke_shared_card_runtime({'action':'load','slot':'main'})['outcome'],outcome)
        class TimedOut:
            returncode=None
            killed=False
            def communicate(self,*args,**kwargs):
                if not self.killed:raise TimeoutExpired('runtime',30)
            def kill(self):self.killed=True
        child=TimedOut()
        with patch('mo2_update_arsenal.shared_card_runtime._runtime',return_value=Path('/known/runtime')), patch('mo2_update_arsenal.shared_card_runtime.subprocess.Popen',return_value=child):
            self.assertEqual(invoke_shared_card_runtime({'action':'load','slot':'main'})['outcome'],'unknown')
            self.assertTrue(child.killed)
    def test_deep_profile_is_controlled_failure(self):
        value={};current=value
        for _ in range(2000):current['nested']={};current=current['nested']
        self.assertEqual(invoke_shared_card_runtime({'action':'commit','input':{'slot':'main','operation':'a','expectedRevision':0,'profile':value}})['outcome'],'not-started')
    def test_authenticated_post_required(self):
        import http.client
        from mo2_update_arsenal.bridge import ArsenalService
        service=ArsenalService('<p>test</p>',Path(self.temp.name)/'cache.json',lambda request:None,{'profileId':'p','items':[]})
        service.start();self.addCleanup(service.stop);port=service._server.server_port
        for token,origin,status in [(False,True,403),(True,False,403),(True,True,200)]:
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5);self.addCleanup(conn.close)
            headers={'Content-Type':'application/json'}
            if token:headers['X-Arsenal-Token']=service.token
            if origin:headers['Origin']=f'http://127.0.0.1:{port}'
            conn.request('POST','/api/shared-cards/invoke',json.dumps({'action':'load','slot':'main'}),headers)
            response=conn.getresponse();response.read();self.assertEqual(response.status,status)

if __name__=='__main__':unittest.main()
