"""Narrow compiled Qt accessor used only inside owned Nexus callbacks.

PyQt refuses protected QObject.sender() on objects created by C++. The bundled
helper uses Qt's typed member API; it never decodes QObject memory or file pointers.
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import sys


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
        if abi != 1 or built.split(".")[:2] != runtime.split(".")[:2] or runtime != QtCore.qVersion():
            raise RuntimeError("Modhunter's native Qt bridge does not match MO2's Qt runtime; install the matching package.")
        self.library = library
        self.info = {"abi": abi, "buildQt": built, "runtimeQt": runtime, "mechanism": "typed-qt-sender"}

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
