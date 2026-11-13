"""Run the packaged Qt 6.7 helper against real Qt 6.11.1/PyQt in Windows CI.

No MO2 binary, game, browser account or network request is exercised here. The
separate native-MO2 job remains required. This receipt is not beta-12 acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    'mo2_update_arsenal/qt_bridge.py', 'tests/test_native_qt_bridge.py',
    'native/scripts/verify_qt_forward.py', 'native/qt_bridge/qt_bridge.cpp',
    'native/qt_bridge/qt_bridge_fixture.cpp',
    'native/scripts/mo2_smoke_plugin/modhunter_native_smoke/runner.py',
)
EXPECTED = {
    'test_cpp_receiver_protected_sender_gap_is_closed_by_typed_helper',
    'test_cpp_receiver_first_file_bootstrap_and_raw_qvariant_payload',
    'test_compiled_helper_rejects_worker_thread_before_qobject_use',
    'test_actual_native_probe_completes_and_checks_lifetimes_under_optimized_python',
    'test_optimized_native_probe_still_rejects_wrong_helper_hash',
    'test_optimized_native_probe_still_rejects_wrong_qt_runtime',
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This acceptance receipt requires actual Windows.')
    os.environ['MODHUNTER_QT_BRIDGE_TEST_DIR'] = str(args.bridge_dir.resolve())
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
    from PyQt6 import QtCore
    from mo2_update_arsenal.qt_bridge import NativeQtSender
    from test_native_qt_bridge import CompiledQtTests

    class Result(unittest.TextTestResult):
        def __init__(self, *values, **options):
            super().__init__(*values, **options)
            self.checks = []
        def addSuccess(self, test):
            super().addSuccess(test)
            self.checks.append({'name': test._testMethodName, 'passed': True})

    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    helper = args.bridge_dir / 'modhunter-qt-bridge.dll'
    resolver = NativeQtSender(helper)
    runtime_ok = (resolver.info['buildQt'] == '6.7.1'
                  and resolver.info['runtimeQt'] == QtCore.qVersion() == '6.11.1'
                  and QtCore.PYQT_VERSION_STR == '6.11.0'
                  and resolver.self_test_passed and app is not None)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CompiledQtTests)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    ok = (runtime_ok and result.wasSuccessful() and not result.skipped
          and result.testsRun == len(EXPECTED)
          and {check['name'] for check in result.checks} == EXPECTED)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--', *SOURCES], cwd=ROOT, text=True).strip())
    report = {'ok': ok and not dirty, 'realQt': True, 'schemaVersion': 1,
              'candidateCommit': commit, 'sourceDirty': dirty, **resolver.info,
              'pyQtVersion': QtCore.PYQT_VERSION_STR, 'ownedSignalSelfTest': resolver.self_test_passed,
              'helperSha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
              'sourceFiles': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
              'checks': result.checks, 'testsRun': result.testsRun, 'skipped': len(result.skipped),
              'scope': 'Real Windows Qt 6.11.1 and PyQt 6.11.0, C++ receiver fixture, same packaged Qt 6.7.1 helper. No exact MO2 2.5.3b12 or authenticated-download claim.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'testsRun': result.testsRun, 'runtimeQt': QtCore.qVersion()}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
