"""Security, live snapshots, update identity and MO2 command dispatch via actual loopback HTTP."""
from __future__ import annotations
import copy
import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.scanner import UpdateScanner,Cache,NexusClient

PROFILE={'profileId':'p1','game':'skyrimspecialedition','items':[{'id':'m1','name':'One','nexusId':77,'installedVersion':'1.0','latestKnownVersion':'2.0'},{'id':'m2','name':'Two','nexusId':77,'installedVersion':'2.0','latestKnownVersion':'2.0'}],'groups':[]}
class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  def native(req):
   req.finish({'ok':True,'downloadId':100,'files':['modlist.txt'],'catalog':copy.deepcopy(PROFILE)})
  self.service=ArsenalService('<!doctype html><!--__MO2_BOOT__--><p>Offline</p>',Path(self.tmp.name)/'cache.json',native,copy.deepcopy(PROFILE))
  self.service.start();self.port=self.service._server.server_port
  self.addCleanup(self.service.stop)
 def call(self,method,path,data=None,token=True,origin=True,host=None):
  conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
  headers={'Host':host or f'127.0.0.1:{self.port}'}
  if token:headers['X-Arsenal-Token']=self.service.token
  if method=='POST':
   headers['Content-Type']='application/json'
   if origin:headers['Origin']=f'http://127.0.0.1:{self.port}'
  payload=json.dumps(data).encode() if data is not None else None
  conn.request(method,path,body=payload,headers=headers)
  r=conn.getresponse();status=r.status;body=r.read().decode();conn.close()
  return status,body
 def test_home_no_private_key_and_session_boot(self):
  self.service.set_key('secret-never-in-html')
  status,body=self.call('GET','/',token=False)
  self.assertEqual(status,200);self.assertIn('window.__MO2_BRIDGE_BOOT__',body);self.assertIn(self.service.token,body);self.assertNotIn('secret-never-in-html',body)
 def test_rejects_unauthorized_and_cross_origin(self):
  self.assertEqual(self.call('GET','/api/snapshot',token=False)[0],403)
  self.assertEqual(self.call('GET','/api/snapshot',host='attacker.example')[0],403)
  self.assertEqual(self.call('POST','/api/backup',{'confirm':'BACKUP'},origin=False)[0],403)
  self.assertEqual(self.call('POST','/api/backup',{'confirm':'BACKUP'},token=False)[0],403)
  self.assertEqual(self.call('GET','/api/snapshot')[0],200)
 def test_rejects_unapproved_commands(self):
  self.assertEqual(self.call('POST','/api/command',{'shell':'del C:/Users'},token=True)[0],404)
  self.assertIn('Invalid',self.call('POST','/api/download',{'modId':'abc','fileId':3,'confirm':'DOWNLOAD'})[1])
  self.assertEqual(self.call('GET','/api/files?modId=999')[0],200)
  self.assertIn('not associated',self.call('GET','/api/files?modId=999')[1])
 def test_cached_local_scan(self):
  self.assertEqual(self.call('POST','/api/scan',{'mode':'local'})[0],200)
  for _ in range(100):
   res=json.loads(self.call('GET','/api/scan')[1])
   if res['scan']['state']!='running':break
   time.sleep(.015)
  self.assertEqual(res['scan']['state'],'completed')
  snap=json.loads(self.call('GET','/api/snapshot')[1])['catalog']['items']
  self.assertEqual(snap[0]['update']['verdict'],'candidate')
  self.assertEqual(snap[1]['update']['verdict'],'unverified')
 def test_deduplicated_nexus_projects_keep_per_item_verdict(self):
  def fetch(path):return {'version':'2.0','status':'published'}
  result=UpdateScanner(Cache(Path(self.tmp.name)/'dupes.json'),NexusClient('fake',request_json=fetch)).scan(PROFILE,'deep')
  self.assertEqual(result['queried'],1)
  self.assertEqual(result['itemRecords']['m1']['verdict'],'candidate')
  self.assertEqual(result['itemRecords']['m2']['verdict'],'current')
 def test_files_explicit_verification_and_profile_isolation(self):
  self.service._file_cache[('skyrimspecialedition',77)]={'ok':True,'files':[{'fileId':9}]}
  rejected=self.service.request_download(77,7,'DOWNLOAD')
  self.assertFalse(rejected['ok'])
  permitted=self.service.request_download(77,9,'DOWNLOAD')
  self.assertTrue(permitted['ok']);self.assertEqual(permitted['downloadId'],100)
  self.assertIn(100,self.service.downloads())
  changed=copy.deepcopy(PROFILE);changed['profileId']='p2';self.service.set_snapshot(changed)
  self.assertNotIn(100,self.service.downloads())
 def test_profile_change_clears_previous_scan_state(self):
  self.service._scan={'state':'completed','records':{'77':{'verdict':'candidate'}}}
  switched=copy.deepcopy(PROFILE);switched['profileId']='another-profile'
  self.service.set_snapshot(switched)
  self.assertEqual(self.service.scan_state()['state'],'idle')
  self.assertNotIn('update',self.service.snapshot()['items'][0])
 def test_no_snapshot_mutation_on_failed_scan(self):
  self.service.begin_scan('quick')
  for _ in range(100):
   if self.service.scan_state()['state']!='running':break
   time.sleep(.015)
  self.assertEqual(self.service.scan_state()['state'],'error')
  self.assertNotIn('update',self.service.snapshot()['items'][0])
 def test_exact_focus_http_uses_native_name_not_browser_text_and_no_full_rebuild(self):
  snap=copy.deepcopy(PROFILE);snap['items'][0]['mo2InternalName']='Native One'
  self.service.set_snapshot(snap)
  gen=self.service._generation
  denied=json.loads(self.call('POST','/api/focus',{'profileId':'foreign','id':'m1','name':'Fake'})[1]);self.assertFalse(denied['ok'])
  denied=json.loads(self.call('POST','/api/focus',{'profileId':'p1','id':'m2'})[1]);self.assertFalse(denied['ok'])
  response=json.loads(self.call('POST','/api/focus',{'profileId':'p1','id':'m1','name':'Injected mod name'})[1]);self.assertTrue(response['ok'])
  revision=json.loads(self.call('GET','/api/revision')[1]);self.assertEqual(revision['selectedModId'],'m1')
  self.assertEqual(revision['generation'],gen)
  self.assertEqual(self.call('POST','/api/focus',{'profileId':'p1','id':'m1'},origin=False)[0],403)

 def test_http_progress_persistence_and_ranked_score_separation(self):
  payload={'profileId':'p1','rows':{'m1':{'status':'done','favorite':True,'score':9000,'note':'Correct build'},'other':{'status':'done'}},'prefs':{'focus':True},'verifiedRuntime':'1.5.97'}
  self.assertEqual(self.call('POST','/api/progress',payload)[0],200)
  saved=json.loads(self.call('GET','/api/progress')[1])['progress']
  self.assertEqual(saved['rows']['m1']['note'],'Correct build')
  self.assertNotIn('other',saved['rows']);self.assertNotIn('score',saved['rows']['m1'])
  game=json.loads(self.call('GET','/api/game')[1]);self.assertEqual(game['score'],0);self.assertEqual(game['updates'],0)
  self.assertEqual(self.call('POST','/api/progress',{**payload,'profileId':'wrong'})[0],409)
  self.assertEqual(self.call('POST','/api/progress',payload,origin=False)[0],403)
  self.assertEqual(self.call('POST','/api/victory/ack',{'profileId':'p1','victoryId':'fake'})[0],200)
  self.assertFalse(json.loads(self.call('POST','/api/victory/ack',{'profileId':'p1','victoryId':'fake'})[1])['ok'])

 def test_live_version_event_credits_only_native_evidence_and_profile_scope(self):
  old=copy.deepcopy(PROFILE);old['items'][0]['id']='live-1';self.service.set_snapshot(old)
  updated=copy.deepcopy(old);updated['items'][0]['installedVersion']='2.0'
  self.service.set_snapshot(updated)
  out=json.loads(self.call('GET','/api/game')[1]);self.assertEqual(out['updates'],1);self.assertGreater(out['score'],0)
  self.service.set_snapshot(copy.deepcopy(updated));self.assertEqual(json.loads(self.call('GET','/api/game')[1])['updates'],1)
  foreign=copy.deepcopy(updated);foreign['profileId']='p2';self.service.set_snapshot(foreign)
  self.assertEqual(json.loads(self.call('GET','/api/game')[1])['updates'],0)

 def test_snapshot_keeps_per_item_update(self):
  first=copy.deepcopy(PROFILE);first['items'][0]['update']={'verdict':'candidate'};first['items'][1]['update']={'verdict':'current'}
  self.service.set_snapshot(first)
  self.service.set_snapshot(copy.deepcopy(PROFILE))
  self.assertEqual(self.service.snapshot()['items'][0]['update']['verdict'],'candidate')
  self.assertEqual(self.service.snapshot()['items'][1]['update']['verdict'],'current')

if __name__=='__main__':unittest.main()

# Realm endpoints deliberately share the same token, Host and Origin checks as the
# privileged download/install/focus operations; they never award ranked XP.
class RealmBridgeTests(BridgeTests):
 def test_realm_http_wallet_purchases_world_and_origin_guards(self):
  self.assertEqual(self.call('GET','/api/realm',token=False)[0],403)
  self.assertEqual(self.call('POST','/api/realm/purchase',{'profileId':'p1','sku':'rift-key','txn':'txn-realm-test-123'},origin=False)[0],403)
  wallet=json.loads(self.call('GET','/api/realm')[1]);self.assertEqual(wallet['available'],0)
  bad=json.loads(self.call('POST','/api/realm/purchase',{'profileId':'p1','sku':'rift-key','txn':'txn-realm-test-123'})[1]);self.assertFalse(bad['ok'])
  result=json.loads(self.call('POST','/api/realm/world',{'profileId':'p1','revision':0,'world':{'v':1,'player':{'x':12},'inventory':{}}})[1]);self.assertTrue(result['ok']);self.assertEqual(result['revision'],1)
  self.assertEqual(json.loads(self.call('GET','/api/realm/world')[1])['world']['player']['x'],12)
  self.assertTrue(json.loads(self.call('POST','/api/realm/world',{'profileId':'p1','revision':0,'world':{'v':1}})[1])['conflict'])
  self.assertFalse(json.loads(self.call('POST','/api/realm/world',{'profileId':'p1','revision':1,'world':{'v':1,'wallet':{'available':999}}})[1])['ok'])
  self.assertFalse(json.loads(self.call('POST','/api/realm/world',{'profileId':'other','revision':1,'world':{'v':1}})[1])['ok'])
  result=self.service.chronicle.record_update('p1',{'id':'live-a','name':'Mod A'},'1.0','2.0','version-observed');self.assertTrue(result['ok'])
  w=json.loads(self.call('GET','/api/realm')[1]);self.assertEqual(w['available'],10)
  buy=json.loads(self.call('POST','/api/realm/purchase',{'profileId':'p1','sku':'steel-blade','txn':'txn-realm-test-987'})[1]);self.assertTrue(buy['ok']);self.assertEqual(buy['wallet']['available'],2)
  idem=json.loads(self.call('POST','/api/realm/purchase',{'profileId':'p1','sku':'steel-blade','txn':'txn-realm-test-987'})[1]);self.assertTrue(idem['idempotent'])
  self.assertEqual(json.loads(self.call('GET','/api/game')[1])['updates'],1)
