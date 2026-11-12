"""Live MO2-verified currency, purchase idempotency, persistence and per-profile saves."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from mo2_update_arsenal.gameplay import Chronicle, REALM_ITEMS, REALM_COINS_PER_UPDATE


class RealmEconomyTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.path=Path(t.name)/'chronicle.json'
        self.c=Chronicle(self.path)
        self.profile='mo2:skyrimspecialedition:a'
    def earn(self,n,profile=None):
        profile=profile or self.profile
        for i in range(n):
            mod={'id':f'live-{i+1}', 'name':f'Test Mod {i+1}'}
            result=self.c.record_update(profile,mod,'1.0','2.0','version-observed')
            self.assertTrue(result['ok'],result)
    def test_only_native_updates_mint_currency(self):
        p=self.profile
        self.assertEqual(self.c.realm_wallet(p)['available'],0)
        self.assertFalse(self.c.record_update(p,{'id':'live-1'},'1.0','1.0','mo2-install-complete')['ok'])
        self.assertFalse(self.c.record_update(p,{'id':'live-1'},'1.0','2.0','browser-checklist')['ok'])
        self.c.set_progress(p,{'live-1':{'status':'done'}},{},'1.5.97',{'live-1'})
        self.assertEqual(self.c.realm_wallet(p)['available'],0)
        self.earn(1)
        w=self.c.realm_wallet(p)
        self.assertEqual(w['earned'],REALM_COINS_PER_UPDATE)
        self.assertEqual(w['verifiedUpdates'],1)
        self.assertFalse(self.c.record_update(p,{'id':'live-1'},'1.0','2.0','version-observed')['ok'])
        self.assertEqual(self.c.realm_wallet(p)['available'],10)
    def test_atomic_purchase_idempotence_and_reload(self):
        self.earn(2)
        first=self.c.purchase_realm(self.profile,'rift-key','txn-test-00001')
        self.assertTrue(first['ok'])
        self.assertEqual(first['wallet']['available'],16)
        again=self.c.purchase_realm(self.profile,'rift-key','txn-test-00001')
        self.assertTrue(again['idempotent']);self.assertEqual(again['wallet']['available'],16)
        self.assertFalse(self.c.purchase_realm(self.profile,'supplies','txn-test-00001')['ok'])
        persisted=Chronicle(self.path).realm_wallet(self.profile)
        self.assertEqual(persisted['available'],16)
        self.assertEqual(len(persisted['purchases']),1)
    def test_permanent_ownership_and_insufficient_balance(self):
        self.earn(1)
        self.assertFalse(self.c.purchase_realm(self.profile,'hearth-hall','txn-test-00002')['ok'])
        self.assertTrue(self.c.purchase_realm(self.profile,'steel-blade','txn-test-00003')['ok'])
        self.assertFalse(self.c.purchase_realm(self.profile,'steel-blade','txn-test-00004')['ok'])
        self.assertFalse(self.c.purchase_realm(self.profile,'not-real','txn-test-00005')['ok'])
        self.assertFalse(self.c.purchase_realm(self.profile,'rift-key','oops')['ok'])
        self.assertEqual(self.c.realm_wallet(self.profile)['available'],2)
    def test_profile_isolation_in_currency_and_world(self):
        self.earn(1)
        self.assertTrue(self.c.purchase_realm(self.profile,'supplies','txn-test-00006')['ok'])
        self.assertEqual(self.c.realm_wallet('another-profile')['available'],0)
        self.assertEqual(self.c.realm_world('another-profile')['revision'],0)
        self.assertEqual(self.c.save_realm_world(self.profile,{'v':1,'player':{'x':43},'inventory':{'wood':10}},0)['revision'],1)
        self.assertEqual(self.c.realm_world(self.profile)['world']['player']['x'],43)
        self.assertEqual(self.c.realm_world('another-profile')['world'],{})
    def test_world_revision_conflict_and_currency_spoof_rejected(self):
        world={'v':1,'player':{'x':2,'y':-4},'inventory':{'wood':8},'worldTime':11}
        self.assertEqual(self.c.save_realm_world(self.profile,world,0)['revision'],1)
        self.assertEqual(self.c.realm_wallet(self.profile)['earned'],0)
        self.assertTrue(self.c.save_realm_world(self.profile,copy.deepcopy(world),0)['conflict'])
        self.assertFalse(self.c.save_realm_world(self.profile,{**world,'wallet':{'available':999999}},1)['ok'])
        self.assertFalse(self.c.save_realm_world(self.profile,{'v':1,'player':{},'inventory':{},'money':999},1)['ok'])
        self.assertFalse(self.c.save_realm_world(self.profile,{**world,'known':list(range(999999))},1)['ok'])
        self.assertEqual(self.c.realm_world(self.profile)['revision'],1)
    def test_mythforge_world_fields_persist_without_affecting_ranked_wallet(self):
        world={'v':1,'player':{'x':3,'skillPoints':2},'inventory':{'wood':1},
               'story':{'fragments':['shrine','watchtower'],'wardenDefeated':False},
               'perks':{'ironheart':1,'eagle-eye':2}}
        result=self.c.save_realm_world(self.profile,world,0)
        self.assertTrue(result['ok'],result)
        restored=Chronicle(self.path).realm_world(self.profile)['world']
        self.assertEqual(restored['story']['fragments'],['shrine','watchtower'])
        self.assertEqual(restored['perks']['ironheart'],1)
        self.assertEqual(self.c.realm_wallet(self.profile)['available'],0)
        self.assertFalse(self.c.save_realm_world(self.profile,{**world,'wallet':{'available':9000}},1)['ok'])

    def test_concurrent_purchases_cannot_overspend(self):
        self.earn(1)
        results=[]
        threads=[]
        for i in range(8):
            t=threading.Thread(target=lambda index=i:results.append(self.c.purchase_realm(self.profile,'rift-key',f'txn-test-concurrent-{index}')))
            threads.append(t);t.start()
        for t in threads:t.join(timeout=4)
        self.assertEqual(sum(x['ok'] for x in results),2)
        self.assertEqual(self.c.realm_wallet(self.profile)['available'],2)
        self.assertEqual(len(self.c.realm_wallet(self.profile)['purchases']),2)
    def test_shop_prices_are_nonnegative_and_backed_by_exact_catalog(self):
        self.assertEqual(set(REALM_ITEMS),{'rift-key','supplies','steel-blade','hunter-bow','ember-heart','winter-cloak','hearth-hall','wolf-companion'})
        self.assertTrue(all(v['cost']>=1 for v in REALM_ITEMS.values()))

if __name__ == '__main__': unittest.main()
