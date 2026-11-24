"""Exact-file preflight using the real service without a listening HTTP socket."""
import copy
import tempfile
import unittest
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.file_advisor import index_nexus_files, nexus_file_id
from mo2_update_arsenal.quick_update import choose_nexus_file
import test_update_workshop_bridge as workshop
PROFILE = workshop.PROFILE


class NexusIdentityTests(unittest.TestCase):
    def test_no_coercion_or_exception_for_malformed_provider_ids(self):
        for value in (None, True, False, 11.9, 11.0, float('nan'), float('inf'),
                      {}, [], '', '11.0', ' 11', '+11', '١١', '9' * 5000, -1, 0):
            with self.subTest(value=str(value)[:30]):
                self.assertEqual(nexus_file_id(value), 0)
        for value in (11, '11', '0011'):
            self.assertEqual(nexus_file_id(value), 11)

    def test_colliding_integer_and_string_ids_are_ambiguous(self):
        indexed = index_nexus_files([None, {'fileId':11}, {'fileId':'11'}, {'fileId':12}])
        self.assertIsNone(indexed[11])
        self.assertEqual(indexed[12], {'fileId':12})

    def test_quick_update_rejects_duplicate_id_even_if_only_one_main_matches(self):
        item = {'update': {'source':'nexus-api', 'verdict':'candidate', 'latestVersion':'2'}}
        main = {'fileId':11, 'version':'2', 'category':1}
        for rows in ([main, {'fileId':'11', 'version':'1', 'category':2}],
                     [dict(main, fileId=11.9)], [dict(main, fileId=True)]):
            self.assertIsNone(choose_nexus_file(item, rows)[0])


class NexusPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        def native(req):
            self.calls.append((req.cmd, copy.deepcopy(req.payload)))
            req.finish({'ok':True, 'downloadId': 400 + len(self.calls)})
        self.service = ArsenalService('<html/>', Path(self.tmp.name) / 'cache.json', native, copy.deepcopy(PROFILE))
        self.rows = [{'fileId':11}, {'fileId':12}]
        self.fetches = []
        def fetch(mid):
            self.fetches.append(mid)
            return {'ok':True, 'files':copy.deepcopy(self.rows)}
        self.service.fetch_files = fetch

    def tearDown(self):
        self.service.stop()
        self.tmp.cleanup()

    def queue(self, two=False):
        entries = [workshop.WorkshopHTTPTests.file()]
        if two:
            entries.append(workshop.WorkshopHTTPTests.file('live-two',12,'1.1',False))
        return self.service.queue_reviewed_downloads({'profileId':'profile-a',
            'confirm':'QUEUE_REVIEWED_DOWNLOADS', 'entries':entries})

    def test_duplicate_or_lossy_identity_rejects_whole_batch_without_dispatch(self):
        for second in ({'fileId':'12'}, {'fileId':12.9}, {'fileId':True}):
            self.rows = [{'fileId':11}, {'fileId':12}, second] if second['fileId']=='12' else [{'fileId':11},second]
            result = self.queue(two=True)
            self.assertFalse(result['ok'], result)
            self.assertFalse(self.calls)
            self.assertFalse(self.service.downloads())

    def test_malformed_unrelated_rows_do_not_crash_or_hide_valid_file(self):
        self.rows = [None, {}, {'fileId':'oops'}, {'fileId':11}]
        result = self.queue()
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.calls[0][1]['fileId'],11)

    def test_multiple_files_share_one_complete_project_preflight(self):
        result = self.queue(two=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.fetches,[101,101,101])  # one preflight + per-dispatch freshness checks
        self.assertEqual([payload['fileId'] for _,payload in self.calls],[11,12])
        self.assertEqual(len(result['queued']),2)

    def test_failed_preflight_can_retry_with_corrected_list(self):
        self.rows = [{'fileId':11}, {'fileId':'11'}]
        self.assertFalse(self.queue()['ok'])
        self.rows = [{'fileId':11}]
        self.assertTrue(self.queue()['ok'])
        self.assertEqual(len(self.calls),1)

    def test_duplicate_appearing_after_preflight_blocks_dispatch(self):
        def fetch(mid):
            self.fetches.append(mid)
            return {'ok':True, 'files': [{'fileId':11}] if len(self.fetches)==1
                    else [{'fileId':11}, {'fileId':'11'}]}
        self.service.fetch_files = fetch
        result = self.queue()
        self.assertFalse(result['queued'])
        self.assertEqual(len(result['failed']),1)
        self.assertFalse(self.calls)
