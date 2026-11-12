from __future__ import annotations
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from mo2_update_arsenal.model import take_snapshot, source_url, inspect_local_flags, identity
from mo2_update_arsenal.scanner import Cache, NexusClient, UpdateScanner, version_verdict, local_mo2_candidates, safe_game_slug

class Mod:
    def __init__(self,name,nexus_id=0,ver='1.0',latest='1.2',separator=False,path=''):
        self._name,self._nexus_id,self._ver,self._latest,self._sep,self._path=name,nexus_id,ver,latest,separator,path
    def name(self):return self._name
    def nexusId(self):return self._nexus_id
    def version(self):return self._ver
    def newestVersion(self):return self._latest
    def isSeparator(self):return self._sep
    def absolutePath(self):return self._path
    def notes(self):return ''
    def repository(self):return 'Nexus'

class ModList:
    def __init__(self,mods):self.mods=mods
    def allModsByProfilePriority(self,profile):return list(self.mods)
    def getMod(self,name):return self.mods[name]
    def priority(self,name):return list(self.mods).index(name)+100
    def state(self,name):return 'ACTIVE'

class Organizer:
    def __init__(self,mods,profile='Warrior'):
        self.mods=ModList(mods);self._profile=profile
    def managedGame(self):return SimpleNamespace(gameShortName=lambda:'skyrimspecialedition')
    def profileName(self):return self._profile
    def basePath(self):return 'C:/Modding/MO2'
    def modList(self):return self.mods
    def profile(self):return self._profile

class TestModel(unittest.TestCase):
    def setUp(self):
        self.mods={'Utilities':Mod('Utilities',separator=True),'SKSE Addon':Mod('SKSE Addon',543,ver='1.1'),'Spells':Mod('Spells',separator=True),'Magic Tweaks':Mod('Magic Tweaks',0)}
    def test_separator_and_status(self):
        snap=take_snapshot(Organizer(self.mods),'1.5.97');self.assertEqual(len(snap['items']),2);self.assertEqual(len(snap['groups']),2);self.assertEqual(snap['groups'][0]['count'],1);self.assertEqual(snap['groups'][1]['count'],1);self.assertEqual(snap['gameRuntime'],'1.5.97');self.assertEqual(snap['skseRuntime'],'2.0.20')
        self.assertEqual(snap['items'][0]['nexusId'],543);self.assertTrue(snap['items'][0]['source']['verified']);self.assertEqual(snap['items'][1]['source']['verified'],False)
    def test_stable_per_profile_id_and_group(self):
        a=take_snapshot(Organizer(self.mods));b=take_snapshot(Organizer(self.mods));c=take_snapshot(Organizer(self.mods,'Rogue'))
        self.assertEqual(a['items'][0]['id'],b['items'][0]['id']);self.assertNotEqual(a['items'][0]['id'],c['items'][0]['id'])
    def test_detected_windows_exe_version_normalization(self):
        class Game:
            def gameNexusName(self): return 'skyrimspecialedition'
            def gameVersion(self): return '1.5.97.0'
        o=Organizer(self.mods)
        o.managedGame=lambda: Game()
        snap=take_snapshot(o)
        self.assertEqual(snap['gameRuntime'],'1.5.97')
        self.assertEqual(snap['skseRuntime'],'2.0.20')
    def test_dll_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'SKSE'/'Plugins';p.mkdir(parents=True);(p/'x.dll').write_bytes(b'dll');self.assertEqual(inspect_local_flags(Path(tmp)),['DLL'])
    def test_no_false_link(self):
        self.assertFalse(source_url('skyrimspecialedition',0)['verified'])

class TestScanner(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        self.cache=Cache(Path(self.dir.name)/'cache.json')
        self.snapshot={'game':'skyrimspecialedition','items':[{'nexusId':1,'installedVersion':'1.0'},{'nexusId':2,'installedVersion':'2.1'},{'nexusId':0,'installedVersion':'1.0'}]}
    def test_semver_conservative(self):
        self.assertEqual(version_verdict('v1.2','1.3'),'candidate')
        self.assertEqual(version_verdict('1.2.0','1.2'),'current')
        self.assertEqual(version_verdict('1.7','1.5'),'newer-installed')
        self.assertEqual(version_verdict('3.1-beta','3.1'),'review')
    def test_fast_activity_delta(self):
        calls=[]
        def fetch(path):
            calls.append(path)
            if 'updated.json' in path:return [{'mod_id':1},{'mod_id':1234}]
            if '/mods/1.json' in path:return {'version':'1.4','status':'published'}
            raise AssertionError('Should only request changed item')
        scanner=UpdateScanner(self.cache,NexusClient('test-key',request_json=fetch))
        result=scanner.scan(self.snapshot,'quick');self.assertEqual(result['records']['1']['verdict'],'candidate');self.assertEqual(result['records']['2']['verdict'],'unverified');self.assertEqual(result['queried'],1)
        self.assertEqual(len(calls),2)
    def test_full_makes_baseline(self):
        def fetch(path):
            return {'version':'3.0','status':'published'}
        result=UpdateScanner(self.cache,NexusClient('test',request_json=fetch)).scan(self.snapshot,'deep')
        self.assertEqual(result['queried'],2)
        # A full raid-clear audit cannot rely on stale earlier cache entries.
        calls=[]
        def second_fetch(path):
            calls.append(path)
            return {'version':'3.0','status':'published'}
        result2=UpdateScanner(self.cache,NexusClient('test',request_json=second_fetch)).scan(self.snapshot,'deep')
        self.assertEqual(result2['queried'],2)
        self.assertEqual(result2['cachedOrDeferred'],0)
        self.assertEqual(len(calls),2)
    def test_cached_profile(self):
        s={'items':[{'nexusId':5,'installedVersion':'1.0','latestKnownVersion':'2.0'}]}
        self.assertEqual(local_mo2_candidates(s)['records']['5']['verdict'],'candidate')
        with self.assertRaises(ValueError):safe_game_slug('../../evil')

if __name__=='__main__':unittest.main()
