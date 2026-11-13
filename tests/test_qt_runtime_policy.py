"""Static policy tests; live C++/PyQt signal tests run separately on Windows."""
import unittest
from mo2_update_arsenal.qt_bridge import compatible_qt_versions


class QtRuntimePolicyTests(unittest.TestCase):
    def test_tested_stable_runtime_lines(self):
        for runtime in ('6.7.1', '6.7.3', '6.11.0', '6.11.1', '6.11.3'):
            self.assertTrue(compatible_qt_versions(1, '6.7.1', runtime, runtime), runtime)

    def test_older_unknown_and_prerelease_runtimes_fail_closed(self):
        for runtime in ('6.7.0', '6.6.3', '6.8.3', '6.10.3', '6.12.0', '7.0.0',
                        '6.11.1-beta', '6.11', '6.11.1.0', '', '6.11.1\x00', None):
            self.assertFalse(compatible_qt_versions(1, '6.7.1', runtime, runtime), runtime)

    def test_distinct_loaded_runtimes_and_unknown_abi_are_rejected(self):
        self.assertFalse(compatible_qt_versions(1, '6.7.1', '6.11.1', '6.11.0'))
        for abi in (0, 2, -1, True, '1'):
            self.assertFalse(compatible_qt_versions(abi, '6.7.1', '6.11.1', '6.11.1'))
        self.assertFalse(compatible_qt_versions(1, '6.11.1', '6.11.1', '6.11.1'))


if __name__ == '__main__':
    unittest.main()
