"""Native proof rejects the exact captured dirty state with bounded diagnostics."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('native_proof_binding',ROOT/'native/scripts/bind_native_proof.py')
binding=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binding)


class NativeProofBindingTests(unittest.TestCase):
    def test_rejection_reports_the_first_all_repository_status_without_requery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            executable=root/'fixture.exe'
            executable.write_bytes(b'owned executable fixture')
            checkpoint=root/'checkpoint.json'
            status_calls=[]

            def git(command,**kwargs):
                if command[1:]==['rev-parse','HEAD']:
                    return 'a'*40+'\n'
                if command[1:]==['status','--porcelain','--untracked-files=all']:
                    status_calls.append(command)
                    # A second query would be clean. It must not erase the
                    # first dirty observation or its out-of-prefix filename.
                    return b'?? root-tool-output.txt\n' if len(status_calls)==1 else b''
                self.fail('Unexpected Git command: '+repr(command))

            with mock.patch.object(binding,'ROOT',root),mock.patch.object(binding,'source_names',return_value=[]),mock.patch.object(binding.subprocess,'check_output',side_effect=git),mock.patch.object(sys,'argv',['bind_native_proof.py','--executable',str(executable),'--checkpoint',str(checkpoint)]):
                with self.assertRaisesRegex(RuntimeError,r'root-tool-output\.txt'):
                    binding.main()
            self.assertEqual(len(status_calls),1)
            self.assertFalse(checkpoint.exists())

    def test_dirty_path_diagnostic_is_bounded_and_keeps_root_paths(self):
        lines=['?? aqtinstall.log']+[' M '+('x'*300)+str(i) for i in range(19)]
        result=binding.dirty_details(('\n'.join(lines)+'\n').encode())
        self.assertIn('?? aqtinstall.log',result)
        self.assertIn('and 8 more repository paths',result)
        self.assertLess(len(result),12*242+80)

    def test_only_known_root_qt_log_is_ignored_and_other_changes_still_reject(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def git(*arguments):
                return subprocess.check_output(['git',*arguments],cwd=root,stderr=subprocess.DEVNULL)
            git('init','-q')
            git('config','core.autocrlf','true')
            (root/'.gitignore').write_bytes((ROOT/'.gitignore').read_bytes())
            (root/'.gitattributes').write_bytes(b'*.html text eol=lf\n')
            asset=root/'native/ui/fixture.html'
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b'<html>Exact LF asset</html>\n')
            git('add','.gitignore','.gitattributes','native/ui/fixture.html')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Owned test fixture')
            (root/'aqtinstall.log').write_bytes(b'Qt downloader diagnostic\n')
            # A deterministic build rewrite and full-byte SHA read remain clean
            # even with checkout newline conversion enabled.
            asset.write_bytes(b'<html>Exact LF asset</html>\n')
            with mock.patch.object(binding,'ROOT',root):
                current,porcelain=binding.capture_identity(asset)
                self.assertFalse(current['sourceDirty'])
                self.assertEqual(porcelain,b'')
                self.assertEqual(git('status','--porcelain','--untracked-files=all'),b'')
                (root/'other.log').write_bytes(b'Unrecognized root file\n')
                nested=root/'native/aqtinstall.log'
                nested.write_bytes(b'Not the known root output\n')
                asset.write_bytes(b'<html>Changed source</html>\n')
                changed,porcelain=binding.capture_identity(asset)
                self.assertTrue(changed['sourceDirty'])
                details=binding.dirty_details(porcelain)
                self.assertIn('other.log',details)
                self.assertIn('native/aqtinstall.log',details)
                self.assertIn('native/ui/fixture.html',details)


if __name__=='__main__':
    unittest.main()
