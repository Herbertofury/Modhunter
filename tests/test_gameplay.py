"""Ranked XP cannot be forged from browser checklists, reinstalls or cached scans."""
import copy
import tempfile
import unittest
from pathlib import Path

from mo2_update_arsenal.gameplay import Chronicle


def mod(identifier='live-01',version='1.0',nexus=44):
    return {'id':identifier,'name':'Dragonsteel','nexusId':nexus,'installedVersion':version,'update':{'verdict':'current'}}


def audit(items,queried=1,errors=None,mode='deep'):
    return {'state':'completed','mode':mode,'queried':queried,'uniqueNexusMods':1,'cachedOrDeferred':1-queried,'errors':errors or []}


class ChronicleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'chronicle.json'
        self.ledger=Chronicle(self.path)
        self.profile='skyrim-profile-A'

    def test_only_real_version_upgrades_earn_ranked_xp(self):
        self.assertFalse(self.ledger.record_update(self.profile,mod(), '1.0','1.0','mo2-install-complete')['ok'])
        self.assertFalse(self.ledger.record_update(self.profile,mod(), '1.0','2.0','browser-checklist')['ok'])
        self.assertTrue(self.ledger.record_update(self.profile,mod(), '1.0','2.0','mo2-install-complete')['ok'])
        self.assertFalse(self.ledger.record_update(self.profile,mod(), '1.0','2.0','mo2-install-complete')['ok'])
        self.assertFalse(self.ledger.record_update(self.profile,mod(), '1.0','2.0','version-observed')['ok'])
        self.assertEqual(self.ledger.overview(self.profile)['score'],100)
        self.assertEqual(self.ledger.overview(self.profile)['updates'],1)

    def test_browser_progress_cannot_forge_trophies_or_scores(self):
        result=self.ledger.set_progress(self.profile,{'live-01':{'status':'done','note':'Stormcloak','score':1000000,'achievements':['world-eater'],'favorite':True},'fake':{'status':'done'}},{'effect':'slash','score':999},'1.5.97',{'live-01'})
        self.assertTrue(result['ok']);self.assertEqual(result['savedRows'],1)
        self.assertEqual(self.ledger.overview(self.profile)['score'],0)
        self.assertEqual(self.ledger.progress(self.profile)['rows']['live-01']['note'],'Stormcloak')
        self.assertNotIn('score',self.ledger.progress(self.profile)['rows']['live-01'])
        self.assertNotIn('fake',self.ledger.progress(self.profile)['rows'])
        self.assertEqual(self.ledger.overview(self.profile)['achievements'][0]['unlocked'],False)

    def test_victory_requires_fresh_full_audit_and_observed_install(self):
        snapshot={'items':[mod()]}
        self.assertIsNone(self.ledger.maybe_victory(self.profile,snapshot,audit(snapshot),True))
        self.ledger.record_update(self.profile,mod(),'1.0','2.0','version-observed')
        self.assertIsNone(self.ledger.maybe_victory(self.profile,snapshot,audit(snapshot,queried=0),True))
        self.assertIsNone(self.ledger.maybe_victory(self.profile,snapshot,audit(snapshot,mode='quick'),True))
        self.assertIsNone(self.ledger.maybe_victory(self.profile,snapshot,audit(snapshot,errors=['quota']),True))
        self.assertIsNone(self.ledger.maybe_victory(self.profile,snapshot,audit(snapshot),False))
        changed=copy.deepcopy(snapshot);changed['items'][0]['update']['verdict']='candidate'
        self.assertIsNone(self.ledger.maybe_victory(self.profile,changed,audit(changed),True))
        crown=self.ledger.maybe_victory(self.profile,snapshot,audit(snapshot),True)
        self.assertIsNotNone(crown);self.assertGreater(crown['bonus'],2000)
        self.assertIsNone(self.ledger.maybe_victory(self.profile,snapshot,audit(snapshot),True))
        self.assertEqual(self.ledger.overview(self.profile)['victories'],1)
        self.assertEqual(self.ledger.overview(self.profile)['unseenVictory']['id'],crown['id'])
        self.assertTrue(self.ledger.ack_victory(self.profile,crown['id'])['ok'])
        self.assertIsNone(self.ledger.overview(self.profile)['unseenVictory'])

    def test_observe_profile_isolation_and_persistence(self):
        before={'profileId':self.profile,'items':[mod()]}
        after={'profileId':self.profile,'items':[mod(version='2.0')]}
        awards=self.ledger.observe(before,after);self.assertEqual(len(awards),1)
        self.assertEqual(len(self.ledger.observe(before,after)),0)
        other=copy.deepcopy(after);other['profileId']='Another profile'
        self.assertEqual(self.ledger.observe(before,other),[])
        self.ledger.set_profile_name(self.profile,'My Skyrim')
        persisted=Chronicle(self.path)
        self.assertEqual(persisted.overview(self.profile)['score'],100)
        self.assertEqual(persisted.leaderboard()[0]['name'],'My Skyrim')

    def test_combo_and_badge_unlocks_are_cumulative(self):
        for i in range(5):
            m=mod(identifier='live-'+str(i))
            self.assertTrue(self.ledger.record_update(self.profile,m,'1.0','2.0','version-observed')['ok'])
        o=self.ledger.overview(self.profile)
        self.assertGreater(o['score'],500)
        self.assertGreaterEqual(o['bestCombo'],5)
        self.assertTrue(next(b for b in o['achievements'] if b['id']=='apprentice')['unlocked'])
        self.assertFalse(next(b for b in o['achievements'] if b['id']=='world-eater')['unlocked'])

if __name__=='__main__':unittest.main()
