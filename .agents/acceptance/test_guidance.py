"""Repository routing/snapshot acceptance, not agent-behavior or gameplay proof."""
import hashlib
import json
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(b):return hashlib.sha256(b).hexdigest()
class SharedGuidanceTests(unittest.TestCase):
 def test_all_original_nonrouter_files_preserved(self):
  expected=json.loads((ROOT/'.agents/acceptance/preserved-files.json').read_text(encoding='utf-8'))
  self.assertEqual(len(expected),419)
  for path,digest in expected.items():
   with self.subTest(path=path):self.assertEqual(sha((ROOT/path).read_bytes()),digest)
 def test_exact_reviewed_router(self):
  self.assertEqual(sha((ROOT/'AGENTS.md').read_bytes()), '04cf47717bb15f1136fe117dbc788c71b08c06b11f977168cf4e76f468fc0aae')
 def test_pinned_athena_snapshots_complete(self):
  lock=json.loads((ROOT/'.athena/governance-lock.json').read_text(encoding='utf-8'))
  self.assertEqual(lock['sourceCommit'],'11e212bb88abd7e010c8974f35ec629032a2a843')
  self.assertEqual(lock['sourceRepository'],'https://github.com/Herbertofury/Athena')
  self.assertEqual(lock['version'],'2.0.1');self.assertIs(lock['sourceWorkingTreeDirty'],False)
  self.assertEqual(len(lock['files']),16)
  for path,digest in lock['files'].items():
   self.assertTrue(path.startswith('.agents/athena/'))
   self.assertEqual(sha((ROOT/path).read_text(encoding='utf-8').replace('\r\n','\n').encode()),digest)
  self.assertEqual((ROOT/'CLAUDE.md').read_text(encoding='utf-8'),'@AGENTS.md\n')
  self.assertTrue((ROOT/'.agents/skills/modhunter-profile-lifecycle/SKILL.md').is_file())
if __name__=='__main__':unittest.main()
