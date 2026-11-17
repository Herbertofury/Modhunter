"""Synthetic service checks. Never opens a native installer or touches mod files."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from test_install_brief import SNAP, TICKET
from mo2_update_arsenal.bridge import ArsenalService

class SiegeInstallConsentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.s=ArsenalService('',Path(self.tmp.name)/'cache.json',lambda _:self.fail('Native dispatch forbidden'),deepcopy(SNAP))
  self.s._downloads[7]=deepcopy(TICKET)
  self.accepted=[];self.s._enqueue_install=lambda entry:self.accepted.append(entry)
 def test_exact_token_accepts_once_without_executing_native(self):
  token=self.s.preview_install(7)['reviewToken']
  self.assertTrue(self.s.install_reviewed_download(7,review_token=token)['ok']);self.assertEqual(len(self.accepted),1)
 def test_context_round_trip_invalidates_even_with_same_profile(self):
  token=self.s.preview_install(7)['reviewToken'];self.s._execution_epoch+=2
  self.assertFalse(self.s.install_reviewed_download(7,review_token=token)['ok']);self.assertEqual(self.accepted,[])
 def test_changed_file_or_backup_warning_requires_new_consent(self):
  token=self.s.preview_install(7)['reviewToken'];self.s._downloads[7]['fileName']='other.7z'
  self.assertFalse(self.s.install_reviewed_download(7,review_token=token)['ok']);self.assertEqual(self.accepted,[])
 def test_obsolete_complete_archive_does_not_block_newer_match(self):
  item=deepcopy(SNAP['items'][0]);item['update']['latestVersion']='3.0'
  self.assertFalse(self.s._ticket_blocks_update(deepcopy(TICKET),item))
  self.assertTrue(self.s._ticket_blocks_update({**deepcopy(TICKET),'status':'downloading'},item))

 def test_invalid_unicode_token_is_rejected_not_executed(self):
  self.assertFalse(self.s.install_reviewed_download(7,review_token='☃')['ok']);self.assertEqual(self.accepted,[])
