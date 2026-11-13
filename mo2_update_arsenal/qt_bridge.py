"""Narrow compiled Qt accessor used only inside owned Nexus callbacks.

PyQt refuses protected QObject.sender() on objects created by C++. The bundled
helper uses Qt's typed member API; it never decodes QObject memory or file pointers.
"""
from __future__ import annotations
import ctypes
import os
import re
from pathlib import Path
import sys


# These minor lines have explicit compiled-bridge CI coverage. Never treat all
# future Qt 6 releases, development builds, or an older runtime as qualified.
_TESTED_QT_LINES = {(6, 7), (6, 11)}


def compatible_qt_versions(abi: int, built: str, runtime: str, host: str) -> bool:
    """Require the same loaded Qt and a tested, stable, non-older Qt 6 runtime.

    Qt documents backwards binary compatibility for public APIs within a major
    version with the same platform/toolchain/build configuration. The helper
    only calls QObject's typed API; the live signal probe below is also required.
    """
    def parse(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", value):
            return None
        return tuple(map(int, value.split(".")))
    build, actual, observed = parse(built), parse(runtime), parse(host)
    return bool(type(abi) is int and abi == 1 and build and actual and observed
                and runtime == host and build[:2] == (6, 7)
                and actual[:2] in _TESTED_QT_LINES and actual >= build)


class NativeQtSender:
    def __init__(self, library_path: Path | None = None):
        from PyQt6 import QtCore, sip
        self.QtCore, self.sip = QtCore, sip
        suffix = ".dll" if os.name == "nt" else ".dylib" if sys.platform == "darwin" else ".so"
        self.path = library_path or Path(__file__).parent / "bin" / ("modhunter-qt-bridge" + suffix)
        if ctypes.sizeof(ctypes.c_void_p) != 8 or not self.path.is_file():
            raise RuntimeError("Install the matching 64-bit Modhunter MO2 package with its native Qt bridge helper to use MO2's Nexus file list.")
        try:
            library = ctypes.CDLL(str(self.path))
            library.modhunter_qt_bridge_abi.argtypes = []
            library.modhunter_qt_bridge_abi.restype = ctypes.c_uint32
            for name in ("modhunter_qt_build_version", "modhunter_qt_runtime_version"):
                function = getattr(library, name)
                function.argtypes = []
                function.restype = ctypes.c_char_p
            library.modhunter_qt_callback_sender.argtypes = [ctypes.c_void_p]
            library.modhunter_qt_callback_sender.restype = ctypes.c_void_p
            abi = library.modhunter_qt_bridge_abi()
            built = library.modhunter_qt_build_version().decode("ascii")
            runtime = library.modhunter_qt_runtime_version().decode("ascii")
        except (AttributeError, OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError("Modhunter's native Qt bridge could not load; reinstall the matching MO2 package.") from exc
        if not compatible_qt_versions(abi, built, runtime, QtCore.qVersion()):
            raise RuntimeError("Modhunter's native Qt bridge does not match MO2's Qt runtime; install the matching package.")
        self.library = library
        self.info = {"abi": abi, "buildQt": built, "runtimeQt": runtime, "mechanism": "typed-qt-sender"}
        self.self_test_passed = False
        self._verify_owned_signal()

    def _verify_owned_signal(self):
        """Probe only disposable objects on the host GUI thread, without Nexus.

        This is a live capability check, not evidence that a particular MO2 beta
        or a real authenticated download has been tested. Qt owns no user data
        here. Direct delivery and pointer identity must both agree.
        """
        core, sip = self.QtCore, self.sip
        app = core.QCoreApplication.instance()
        if app is None or core.QThread.currentThread() != app.thread():
            raise RuntimeError("The native Qt bridge self-test requires MO2's GUI thread")

        class Emitter(core.QObject):
            pulse = core.pyqtSignal()

        sender = Emitter()
        resolver = self
        observations = []

        class Receiver(core.QObject):
            @core.pyqtSlot()
            def receive(self):
                # Never let an exception escape a PyQt slot: some embedded
                # hosts terminate the process for an unhandled slot exception.
                try:
                    current = resolver.sender(self)
                    observations.append(int(sip.unwrapinstance(current)) if current is not None else 0)
                except Exception:
                    observations.append(0)

        receiver = Receiver()
        try:
            sender.pulse.connect(receiver.receive, type=core.Qt.ConnectionType.DirectConnection)
            if self.sender(receiver) is not None:
                raise RuntimeError("The native Qt bridge reported a sender outside a signal")
            sender.pulse.emit()
            if observations != [int(sip.unwrapinstance(sender))] or self.sender(receiver) is not None:
                raise RuntimeError("The native Qt bridge failed its live sender identity test")
            self.self_test_passed = True
        finally:
            sender.pulse.disconnect(receiver.receive)
            sip.delete(receiver)
            sip.delete(sender)

    def sender(self, receiver):
        app = self.QtCore.QCoreApplication.instance()
        if not isinstance(receiver, self.QtCore.QObject) or self.sip.isdeleted(receiver):
            raise RuntimeError("The native Nexus callback receiver is no longer a live QObject")
        if app is None or self.QtCore.QThread.currentThread() != app.thread() or receiver.thread() != app.thread():
            raise RuntimeError("Nexus callbacks must run on MO2's GUI thread")
        pointer = self.library.modhunter_qt_callback_sender(int(self.sip.unwrapinstance(receiver)))
        if not pointer:
            return None
        # wrapinstance borrows an existing QObject; Qt retains its ownership.
        return self.sip.wrapinstance(pointer, self.QtCore.QObject)
