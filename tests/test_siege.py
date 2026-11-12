"""Modhunter Siege: native update bounties cannot be earned by browser-only claims."""
from __future__ import annotations
import copy
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from mo2_update_arsenal.gameplay import Chronicle
from mo2_update_arsenal.bridge import ArsenalService

MOD={'id':'live-77','name':'Engine Fixes and Armaments','mo2InternalName':'EngineFixes','nexusId':77,
     'installedVersion':'1.0','update':{'verdict':'candidate','latestVersion':'2.0','checkedAt':1929000000,'source':'nexus-api'}}
PROFILE={'profileId':'modhunter-demo','profileName':'Skyrim 1.5.97','game':'skyrimspecialedition','groups':[], 'items':[MOD]}

class NativeBossBountyTests(unittest.TestCase):
 def setUp(self):
  self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
  self.store=Chronicle(Path(self.dir.name)/'chronicle.json')
 def test_claim_creates_no_currency_or_ranked_xp(self):
  granted=self.store.claim_mod_boss('p',MOD,'run_abcdefghijk');self.assertTrue(granted['ok'])
  self.assertFalse(granted['awarded']);self.assertEqual(granted['bounty']['status'],'defeated')
  self.assertEqual(self.store.overview('p')['score'],0)
  self.assertEqual(self.store.realm_wallet('p')['earned'],0)
  dup=self.store.claim_mod_boss('p',MOD,'run_abcdefghijk');self.assertTrue(dup['idempotent'])
  self.assertEqual(len(self.store.boss_bounties('p')['bounties']),1)
 def test_claim_only_if_real_comparable_update_and_live_id(self):
  bad=copy.deepcopy(MOD);bad['update']['verdict']='review'
  self.assertFalse(self.store.claim_mod_boss('p',bad,'run_abcdefghijk')['ok'])
  bad=copy.deepcopy(MOD);bad['installedVersion']='latest';bad['update']['latestVersion']='2.0'
  self.assertFalse(self.store.claim_mod_boss('p',bad,'run_abcdefghijk')['ok'])
  bad=copy.deepcopy(MOD);bad['id']='demo-mod'
  self.assertFalse(self.store.claim_mod_boss('p',bad,'run_abcdefghijk')['ok'])
 def test_verified_update_adds_double_boss_points_and_one_extra_update_currency(self):
  self.store.claim_mod_boss('p',MOD,'run_abcdefghijk')
  upgraded=copy.deepcopy(MOD);upgraded['installedVersion']='2.0'
  result=self.store.record_update('p',upgraded,'1.0','2.0','version-observed')
  self.assertTrue(result['ok']);self.assertEqual(result['event']['bossBounty']['bonusPoints'],200)
  self.assertEqual(result['event']['bossBounty']['bonusSigils'],10)
  self.assertEqual(self.store.overview('p')['score'],300) # 100 native + 200 bonus
  self.assertEqual(self.store.realm_wallet('p')['earned'],20) # 10 native + 10 boss bounty
  self.assertEqual(self.store.realm_wallet('p')['bossBonusSigils'],10)
  self.assertEqual(self.store.boss_bounties('p')['bounties'][0]['status'],'forged')
  self.assertFalse(self.store.record_update('p',upgraded,'1.0','2.0','version-observed')['ok'])
  self.assertEqual(self.store.realm_wallet('p')['earned'],20)
  self.assertTrue(self.store.claim_mod_boss('p',MOD,'run_bbbbbbbbb')['idempotent'])
 def test_profile_isolation_and_wrong_versions_do_not_claim(self):
  self.store.claim_mod_boss('profile-A',MOD,'run_abcdefghijk')
  upgraded=copy.deepcopy(MOD);upgraded['installedVersion']='2.0'
  self.assertTrue(self.store.record_update('profile-B',upgraded,'1.0','2.0','version-observed')['ok'])
  self.assertEqual(self.store.realm_wallet('profile-B')['earned'],10)
  other=copy.deepcopy(MOD);other['installedVersion']='1.5'
  self.assertTrue(self.store.record_update('profile-A',other,'1.0','1.5','version-observed')['ok'])
  self.assertEqual(self.store.realm_wallet('profile-A')['earned'],20)
 def test_save_separate_from_world_and_cannot_mutate_currency(self):
  a=self.store.save_siege_state('a',{'v':1,'mode':'playing','run':{'score':999999}},0)
  self.assertTrue(a['ok']);self.assertEqual(a['revision'],1)
  self.assertFalse(self.store.save_siege_state('a',{'v':1,'wallet':{'earned':999999}},1)['ok'])
  self.assertTrue(self.store.save_siege_state('a',{'v':1,'mode':'playing'},0)['conflict'])
  self.assertEqual(self.store.realm_wallet('a')['earned'],0)
  reopened=Chronicle(Path(self.dir.name)/'chronicle.json')
  self.assertEqual(reopened.siege_state('a')['state']['run']['score'],999999)

class BridgeSiegeTests(unittest.TestCase):
 def setUp(self):
  self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
  def native(req):req.finish({'ok':True,'selected':req.payload.get('name')})
  self.service=ArsenalService('<!doctype html><!--__MO2_BOOT__-->',Path(self.dir.name)/'cache.json',native,copy.deepcopy(PROFILE))
  self.service.start();self.addCleanup(self.service.stop)
  self.port=self.service._server.server_port
 def call(self,method,path,body=None,token=True,origin=True):
  c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=8)
  h={'Host':f'127.0.0.1:{self.port}'}
  if token:h['X-Arsenal-Token']=self.service.token
  if method=='POST':
   h['Content-Type']='application/json'
   if origin:h['Origin']=f'http://127.0.0.1:{self.port}'
  c.request(method,path,body=json.dumps(body) if body is not None else None,headers=h)
  response=c.getresponse();status=response.status;data=json.loads(response.read());c.close();return status,data
 def test_exact_target_and_native_mo2_focus(self):
  status,data=self.call('GET','/api/siege/targets');self.assertEqual(status,200)
  self.assertEqual(data['targets'][0]['id'],'live-77')
  self.assertEqual(data['targets'][0]['name'],MOD['name'])
  status,out=self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo','modId':'live-77','runId':'run_abcdefghij'})
  self.assertEqual(status,200);self.assertTrue(out['ok'])
  self.assertEqual(self.service.chronicle.realm_wallet('modhunter-demo')['earned'],0)
  focus=self.call('POST','/api/focus',{'profileId':'modhunter-demo','id':'live-77'})[1]
  self.assertEqual(focus['selected'],'EngineFixes')
 def test_requires_same_origin_profile_and_real_candidate(self):
  self.assertEqual(self.call('GET','/api/siege/targets',token=False)[0],403)
  self.assertEqual(self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo','modId':'live-77','runId':'run_abcdefghij'},origin=False)[0],403)
  self.assertFalse(self.call('POST','/api/siege/boss/defeated',{'profileId':'another','modId':'live-77','runId':'run_abcdefghij'})[1]['ok'])
  self.assertFalse(self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo','modId':'live-unknown','runId':'run_abcdefghij'})[1]['ok'])
  bad=copy.deepcopy(PROFILE);bad['items'][0]['installedVersion']='2.0'
  bad['items'][0]['update']={'verdict':'current','latestVersion':'2.0'}
  self.service.set_snapshot(bad)
  self.assertEqual(self.call('GET','/api/siege/targets')[1]['count'],0)
  self.assertFalse(self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo','modId':'live-77','runId':'run_abcdefghij'})[1]['ok'])
 def test_full_upgrade_only_after_native_version_changed_and_restart(self):
  self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo','modId':'live-77','runId':'run_abcdefghij'})
  changed=copy.deepcopy(PROFILE);changed['items'][0]['installedVersion']='2.0'
  self.service.set_snapshot(changed)
  self.assertEqual(self.call('GET','/api/siege/targets')[1]['count'],0)
  self.assertEqual(self.call('GET','/api/siege/bounties')[1]['forged'],1)
  self.assertEqual(self.call('GET','/api/realm')[1]['available'],20)
  self.assertEqual(self.call('GET','/api/game')[1]['score'],300)
  changed_profile=copy.deepcopy(PROFILE);changed_profile['profileId']='another'
  self.service.set_snapshot(changed_profile)
  self.assertEqual(self.call('GET','/api/siege/bounties')[1]['forged'],0)
  self.assertEqual(self.call('GET','/api/realm')[1]['available'],0)
 def test_thematic_target_lore_keeps_exact_mod_identity(self):
  lineup=copy.deepcopy(PROFILE)
  lineup['items']=[
   {**copy.deepcopy(MOD),'id':'live-weapon','mo2InternalName':'AxeFolder','name':'Electron Maul - SSE'},
   {**copy.deepcopy(MOD),'id':'live-armor','mo2InternalName':'ArmorFolder','name':'Iron Rose Armor 2k Complex Material'},
   {**copy.deepcopy(MOD),'id':'live-npc','mo2InternalName':'CelunaFolder','name':'Celuna - Dark Elf Follower',
     'contentEntities':{'npcs':['Celuna']}},
  ]
  self.service.set_snapshot(lineup)
  result=self.call('GET','/api/siege/targets')[1]
  self.assertEqual(result['count'],3)
  types={t['id']:t['lore'] for t in result['targets']}
  self.assertEqual(types['live-weapon']['kind'],'weapon')
  self.assertEqual(types['live-armor']['kind'],'armor')
  self.assertEqual(types['live-npc']['kind'],'npc')
  self.assertEqual(types['live-npc']['provenance'],'content-metadata')
  self.assertEqual(types['live-npc']['subject'],'Celuna')
  # A creature or fantasy label must never be accepted as a mod identity.
  result=self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo',
   'modId':types['live-weapon']['bossTitle'],'runId':'run_abcdefghij'})[1]
  self.assertFalse(result['ok'])
  result=self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo',
   'modId':'live-weapon','runId':'run_abcdefghij'})[1]
  self.assertTrue(result['ok'])
  selected=self.call('POST','/api/focus',{'profileId':'modhunter-demo','id':'live-weapon'})[1]
  self.assertEqual(selected['selected'],'AxeFolder')

 def test_same_nexus_project_separate_mods_and_version_identity(self):
  two=copy.deepcopy(PROFILE);two['items'].append({**copy.deepcopy(MOD),'id':'live-78','mo2InternalName':'AnotherPlugin','installedVersion':'1.2'})
  self.service.set_snapshot(two)
  result=self.call('GET','/api/siege/targets')[1]
  self.assertEqual(result['count'],2)
  self.assertEqual({m['id'] for m in result['targets']},{'live-77','live-78'})
  self.call('POST','/api/siege/boss/defeated',{'profileId':'modhunter-demo','modId':'live-77','runId':'run_abcdefghij'})
  updated=copy.deepcopy(two);updated['items'][1]['installedVersion']='2.0'
  self.service.set_snapshot(updated)
  self.assertEqual(self.call('GET','/api/realm')[1]['available'],10)
  updated2=copy.deepcopy(updated);updated2['items'][0]['installedVersion']='2.0'
  self.service.set_snapshot(updated2)
  self.assertEqual(self.call('GET','/api/realm')[1]['available'],30)

if __name__=='__main__':unittest.main()
