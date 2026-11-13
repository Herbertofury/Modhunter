"""Release evidence must hash identical checkout bytes on Windows and Linux."""
from __future__ import annotations
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ATTESTED = (
    'mo2_update_arsenal/qt_bridge.py', 'tests/test_native_qt_bridge.py',
    'native/scripts/verify_qt_forward.py', 'native/qt_bridge/qt_bridge.cpp',
    'native/qt_bridge/qt_bridge_fixture.cpp',
    'native/scripts/mo2_smoke_plugin/modhunter_native_smoke/runner.py',
    'native/ui/watchtower.html', 'native/ui/watchtower.css', 'native/ui/watchtower.js',
    'tests/fixtures/watchtower_native_fixture.js', 'tests/test_watchtower_browser.py',
)
LAUNCHERS = ('native/Install_MO2_Plugin.bat', 'native/Check_MO2_Readiness.bat')


class ReleaseCheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        values = subprocess.check_output(
            ['git', 'check-attr', '-z', 'text', 'eol', '--', *ATTESTED, *LAUNCHERS],
            cwd=ROOT, timeout=10).decode('utf-8').split('\0')[:-1]
        if len(values) % 3:
            raise AssertionError('Malformed Git attribute response')
        cls.attributes = {(values[i], values[i + 1]): values[i + 2]
                          for i in range(0, len(values), 3)}

    def test_attested_sources_use_identical_lf_bytes_on_every_platform(self):
        for name in ATTESTED:
            with self.subTest(path=name):
                self.assertEqual(self.attributes[name, 'text'], 'set')
                self.assertEqual(self.attributes[name, 'eol'], 'lf')
                self.assertNotIn(b'\r\n', (ROOT / name).read_bytes())

    def test_windows_launchers_have_explicit_crlf_checkout(self):
        for name in LAUNCHERS:
            with self.subTest(path=name):
                self.assertEqual(self.attributes[name, 'text'], 'set')
                self.assertEqual(self.attributes[name, 'eol'], 'crlf')
                content = (ROOT / name).read_bytes()
                self.assertIn(b'\r\n', content)
                self.assertNotIn(b'\n', content.replace(b'\r\n', b''))


if __name__ == '__main__':
    unittest.main()
