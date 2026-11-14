"""Launch checking: complete cached evidence, bounded work, and owned partial results."""
from __future__ import annotations
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.scanner import Cache, NexusClient, UpdateScanner, MissingAuth, NexusError, STARTUP_CACHE_SECONDS


def profile(count=2):
    return {'profileId':'A','profileName':'A','game':'skyrimspecialedition','gameRuntime':'1.5.97',
            'groups':[], 'items':[{'id':str(i),'name':f'Mod {i}','nexusId':i+1,'installedVersion':'1.0',
            'source':{'verified':True,'type':'nexus','url':f'https://www.nexusmods.com/skyrimspecialedition/mods/{i+1}'}}
            for i in range(count)]}

class StartupScannerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.cache=Cache(Path(temp.name)/'metadata.json')
    def cached(self,mid,meta,age=1):
        self.cache.set('skyrimspecialedition',mid,{'meta':meta,'checkedAt':time.time()-age})
    def test_startup_uses_recent_complete_metadata_and_publishes_it_before_network(self):
        self.cached(1,{'version':'1.0','status':'published'})
        events=[];calls=[]
        def request(path):
            self.assertEqual(events[0]['itemRecords']['0']['verdict'],'current')
            calls.append(path);return {'version':'2.0','status':'published'}
        result=UpdateScanner(self.cache,NexusClient('fixture',request)).scan(profile(),'startup',events.append)
        self.assertEqual(calls,['/v1/games/skyrimspecialedition/mods/2.json'])
        self.assertEqual(result['fromCache'],1);self.assertEqual(result['queried'],1)
        self.assertEqual(result['itemRecords']['1']['verdict'],'candidate')
        self.assertEqual(events[-1]['itemRecords']['1']['verdict'],'candidate')
        self.assertEqual(set(events[-1]['itemRecords']),{'1'})
    def test_stale_future_invalid_and_failed_cache_entries_are_refetched(self):
        cases=[({'version':'1.0'},STARTUP_CACHE_SECONDS+1),({'version':'1.0'},-300),
               ({'status':'not_found'},1),({},1),({'version':''},1)]
        for meta,age in cases:
            with self.subTest(meta=meta,age=age):
                self.cached(1,meta,age);calls=[]
                result=UpdateScanner(self.cache,NexusClient('fixture',lambda p:calls.append(p) or {'version':'2.0'})).scan(profile(1),'startup')
                self.assertEqual(len(calls),1);self.assertEqual(result['fromCache'],0)
    def test_shared_project_compares_each_installed_version_and_requests_once(self):
        data=profile();data['items'][1]['nexusId']=1;data['items'][1]['installedVersion']='2.0';data['items'][1]['source']=copy.deepcopy(data['items'][0]['source'])
        result=UpdateScanner(self.cache,NexusClient('fixture',lambda p:{'version':'2.0'})).scan(data,'startup')
        self.assertEqual(result['queried'],1)
        self.assertEqual(result['itemRecords']['0']['verdict'],'candidate')
        self.assertEqual(result['itemRecords']['1']['verdict'],'current')
    def test_auth_failure_stops_submissions_and_does_not_mean_current(self):
        calls=[]
        def request(path):calls.append(path);raise MissingAuth('Sign in to Nexus')
        result=UpdateScanner(self.cache,NexusClient('fixture',request)).scan(profile(80),'startup')
        self.assertLessEqual(len(calls),5);self.assertTrue(result['errors'])
        self.assertFalse(any(r['verdict']=='current' for r in result['itemRecords'].values()))
        self.assertTrue(all(r.get('availability')=='authentication-required' for r in result['itemRecords'].values()))

    def test_explicit_non_nexus_or_mismatched_source_never_uses_leftover_native_id(self):
        for url in ('https://github.com/a/b', 'https://www.nexusmods.com/fallout4/mods/1',
                    'https://www.nexusmods.com/skyrimspecialedition/mods/99'):
            data=profile(1);data['items'][0]['source']['url']=url;calls=[]
            result=UpdateScanner(self.cache,NexusClient('fixture',lambda p:calls.append(p))).scan(data,'startup')
            self.assertEqual(calls,[]);self.assertEqual(result['queried'],0)
    def test_startup_circuit_breaks_repeated_network_failures_without_false_current(self):
        calls=[]
        def broken(path):
            calls.append(path)
            raise NexusError('Fixture network outage')
        result=UpdateScanner(self.cache,NexusClient('fixture',broken)).scan(profile(2000),'startup')
        self.assertLessEqual(len(calls),9)
        self.assertEqual(len(result['itemRecords']),2000)
        self.assertEqual({v['availability'] for v in result['itemRecords'].values()},{'unavailable'})
        self.assertNotIn('current',{v['verdict'] for v in result['itemRecords'].values()})

    def test_wrong_project_metadata_is_never_cached_or_reported_current(self):
        for meta in ({'version':'1.0','mod_id':99}, {'version':'1.0','mod_id':True},
                     {'version':'1.0','domain_name':'fallout4'}):
            result=UpdateScanner(self.cache,NexusClient('fixture',lambda p:meta)).scan(profile(1),'startup')
            self.assertEqual(result['itemRecords']['0']['availability'],'unavailable')
            self.assertIsNone(self.cache.get('skyrimspecialedition',1))
    def test_foreign_cached_metadata_is_revalidated_even_when_recent(self):
        self.cached(1,{'version':'1.0','mod_id':99})
        result=UpdateScanner(self.cache,NexusClient('fixture',lambda p:{'version':'2.0','mod_id':1})).scan(profile(1),'startup')
        self.assertEqual(result['queried'],1);self.assertEqual(result['fromCache'],0)
        self.assertEqual(result['itemRecords']['0']['verdict'],'candidate')

class StartupServiceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        snapshot=profile();snapshot['items'][0].update(nexusId=0,source={'verified':True,'type':'github','url':'https://github.com/fixture/project'})
        self.service=ArsenalService('',Path(temp.name)/'cache.json',lambda r:self.fail('No native mutation requested'),snapshot)
        self.addCleanup(self.service.stop)
        self.started=threading.Event();self.release=threading.Event();self.finished=threading.Event()
        self.calls=[];self.publisher=None
        def scan(snapshot,progress,**kwargs):
            self.calls.append(snapshot['profileId']);self.publisher=progress
            progress({'phase':'fixture','itemRecords':{'0':{'verdict':'candidate','latestVersion':'3.0','source':'fixture-api'}}})
            self.started.set()
            if not self.release.wait(5):raise RuntimeError('Fixture not released')
            progress({'phase':'late','itemRecords':{'0':{'verdict':'current','latestVersion':'1.0'}}})
            self.finished.set()
            return {'records':{},'itemRecords':{},'queried':0,'errors':[]}
        self.service._source_scanner=SimpleNamespace(scan=scan)
        self.addCleanup(self.release_worker)
    def release_worker(self):
        self.release.set()
        if self.started.is_set():self.finished.wait(2)
        for _ in range(100):
            if self.service.scan_state().get('state')!='running':break
            time.sleep(.005)
    def launch(self):
        result=self.service.startup_check('A',self.service._execution_epoch)
        self.assertTrue(result['ok']);self.assertTrue(self.started.wait(1));return result
    def test_duplicate_windows_start_only_one_owned_scan(self):
        self.launch();responses=[]
        workers=[threading.Thread(target=lambda:responses.append(self.service.startup_check('A',0))) for _ in range(12)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(1)
        self.assertEqual(self.calls,['A']);self.assertTrue(all(r['ok'] and not r['started'] for r in responses))
        self.release_worker()
        self.assertFalse(self.service.startup_check('A',0)['started'])
    def test_wrong_or_missing_execution_context_never_starts_work(self):
        for p,e in [('B',0),('A',None),('A',True),('A',1),('',0)]:
            self.assertFalse(self.service.startup_check(p,e)['ok'])
        self.assertEqual(self.calls,[])
    def test_complete_partial_result_is_visible_before_whole_scan_finishes(self):
        generation=self.service._generation;self.launch()
        self.assertEqual(self.service.scan_state()['state'],'running')
        self.assertEqual(self.service.snapshot()['items'][0]['update']['latestVersion'],'3.0')
        self.assertGreater(self.service._generation,generation)
        self.assertNotIn('itemRecords',self.service.scan_state())
    def test_cancel_keeps_results_and_rejects_delayed_completion(self):
        self.launch();job=self.service.scan_state()['jobId']
        self.assertFalse(self.service.cancel_scan('A',job+1)['ok'])
        self.assertTrue(self.service.cancel_scan('A',job)['ok'])
        before=self.service.snapshot();self.release_worker()
        self.assertEqual(before,self.service.snapshot());self.assertEqual(self.service.scan_state()['state'],'cancelled')
        self.assertFalse(self.service.startup_check('A',0)['started'])
    def test_profile_switch_rejects_old_partial_publications(self):
        self.launch();other=profile();other['profileId']='B';self.service.set_snapshot(other)
        before=self.service.snapshot();self.release_worker();self.assertEqual(before,self.service.snapshot())
    def test_no_credentials_is_an_explicit_status_not_a_successful_current_check(self):
        self.service._source_scanner=SimpleNamespace(scan=lambda *args,**kwargs:{'records':{},'itemRecords':{},'queried':0,'errors':[]})
        self.assertTrue(self.service.startup_check('A',0)['ok'])
        for _ in range(100):
            if self.service.scan_state().get('state')!='running':break
            time.sleep(.005)
        update=self.service.snapshot()['items'][1]['update']
        self.assertEqual(update['availability'],'authentication-required');self.assertNotEqual(update['verdict'],'current')

if __name__=='__main__':unittest.main()
