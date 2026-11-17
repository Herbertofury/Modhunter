"""Shared provenance for actual Chromium acceptance, never native-runtime proof."""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import subprocess
import unittest


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_checks = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_checks.append({"name": test._testMethodName.removeprefix("test_"), "passed": True})


def source_identity(root: Path, sources) -> dict:
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--", *sources], cwd=root, text=True).strip()),
        "sourceSha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
    }


def chromium_identity(browser, page) -> dict:
    return {"engine": browser.browser_type.name, "version": browser.version,
            "userAgent": page.evaluate("navigator.userAgent")}


def screenshot_identity(data: bytes) -> dict:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("Chromium did not produce a PNG screenshot")
    width, height = struct.unpack(">II", data[16:24])
    return {"sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height}
