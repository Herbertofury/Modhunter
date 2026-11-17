"""Read-only protocol tests; fixture profiles contain no user/private game source."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from mo2_update_arsenal.shared_cards import inspect_shared_cards, shared_cards_path


def profile():
    value = {k:{} for k in ('match','collection','heroes','packs','progression','quests','achievements','stats','cosmetics','settings')}
    return {**value,'version':3,'decks':[], 'futureUnknown':{'keep':'all bytes'}}


class SharedCardsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{'XDG_DATA_HOME':self.temp.name,'LOCALAPPDATA':self.temp.name,'HOME':self.temp.name})
        self.env.start();self.addCleanup(self.env.stop)
        self.path=shared_cards_path()
    def make_db(self, value=None, version=2):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA application_id=1196639044');db.execute('PRAGMA user_version='+str(version))
            db.execute('CREATE TABLE snapshots(slot TEXT,revision INTEGER,ruleset TEXT,profile TEXT,PRIMARY KEY(slot,revision))')
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?)',('main',1,'gamesync-cardgame-v3',json.dumps(value or profile())))
    def test_empty_xdg_does_not_choose_different_store(self):
        with patch('mo2_update_arsenal.shared_cards.sys.platform','linux'), patch.dict(os.environ,{'XDG_DATA_HOME':''}):
            self.assertEqual(inspect_shared_cards()['state'],'unavailable')
    def test_absent_does_not_create_files(self):
        self.assertEqual(inspect_shared_cards()['state'],'absent');self.assertFalse(self.path.parent.exists())
    def test_reads_without_mutating_or_exposing_profile(self):
        p=profile();p['sharedCardCatalog']={'sharedCardSnapshot':{'entries':[{'card':{'id':'x'}}]}}
        self.make_db(p);before=self.path.read_bytes();out=inspect_shared_cards()
        self.assertEqual(out['revision'],1);self.assertEqual(out['cardCount'],1);self.assertFalse(out['playable'])
        self.assertNotIn('futureUnknown',json.dumps(out));self.assertEqual(before,self.path.read_bytes())
    def test_future_schema_preserved(self):
        self.make_db(version=99);before=self.path.read_bytes();self.assertEqual(inspect_shared_cards()['state'],'unsupported');self.assertEqual(before,self.path.read_bytes())
    def test_original_schema_read_without_migration(self):
        self.make_db(version=1);before=self.path.read_bytes();self.assertEqual(inspect_shared_cards()['state'],'available');self.assertEqual(before,self.path.read_bytes())
    def test_invalid_revision_is_not_reported_as_available(self):
        self.make_db()
        with sqlite3.connect(self.path) as db:db.execute("UPDATE snapshots SET revision=-1")
        self.assertEqual(inspect_shared_cards()['state'],'unsupported')
    def test_wrong_identity(self):
        self.make_db()
        with sqlite3.connect(self.path) as db:db.execute('PRAGMA application_id=1')
        self.assertEqual(inspect_shared_cards()['state'],'unsupported')
    def test_invalid_profile_and_budget_leave_data(self):
        self.make_db()
        with patch('mo2_update_arsenal.shared_cards.PROFILE_BUDGET',4):self.assertEqual(inspect_shared_cards()['state'],'unavailable')
        with sqlite3.connect(self.path) as db:db.execute("UPDATE snapshots SET profile='not-json'")
        before=self.path.read_bytes();self.assertEqual(inspect_shared_cards()['state'],'unavailable');self.assertEqual(before,self.path.read_bytes())
    def test_exclusive_writer_returns_unavailable_not_absent(self):
        self.make_db();db=sqlite3.connect(self.path);self.addCleanup(db.close);db.execute('BEGIN EXCLUSIVE')
        self.assertEqual(inspect_shared_cards()['state'],'unavailable');db.rollback();self.assertEqual(inspect_shared_cards()['state'],'available')
    def test_status_route_requires_existing_session_authorization(self):
        import http.client
        from mo2_update_arsenal.bridge import ArsenalService
        service=ArsenalService('<p>test</p>',Path(self.temp.name)/'cache.json',lambda request:None,{'profileId':'p','items':[]})
        service.start();self.addCleanup(service.stop);port=service._server.server_port
        for token,status in [(None,403),(service.token,200)]:
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            self.addCleanup(conn.close);headers={'X-Arsenal-Token':token} if token else {}
            conn.request('GET','/api/shared-cards/status',headers=headers);response=conn.getresponse();body=response.read()
            self.assertEqual(response.status,status)
            if status==200:self.assertEqual(json.loads(body)['state'],'absent')

if __name__=='__main__':unittest.main()
