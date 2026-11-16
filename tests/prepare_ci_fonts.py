#!/usr/bin/env python3
"""Install pinned fallbacks for observed Linux CI glyph gaps, never product assets.

Upstream font SHA-256 is published in the pinned official SPDX manifest:
https://github.com/notofonts/noto-cjk/blob/f8d157532fbfaeda587e826d4cd5b21a49186f7c/_manifest/spdx_2.2/manifest.spdx.json
The accompanying OFL license is downloaded and verified before installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zlib

UPSTREAM_COMMIT = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
BASE_URL = f"https://raw.githubusercontent.com/notofonts/noto-cjk/{UPSTREAM_COMMIT}/"
FONT_NAME = "NotoSansCJKtc-Regular.otf"
FONT_FAMILY = "Noto Sans CJK TC"
FONT_POSTSCRIPT = "NotoSansCJKtc-Regular"
FILES = (
    {"path": "Sans/OTF/TraditionalChinese/" + FONT_NAME, "name": FONT_NAME,
     "bytes": 16435884, "sha256": "dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4",
     "gitBlob": "f9376bae1d421520f73a3c6c9d50b22f89548301"},
    {"path": "Sans/LICENSE", "name": "LICENSE", "bytes": 4301,
     "sha256": "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2",
     "gitBlob": "d952d62c065f3f35fb83a173496e90b21525aef3"},
)
# This is the actual Traditional Chinese Preferences heading used by UI QA.
COVERAGE_TEXT = "外觀、隱私與維護"
SUPPLEMENT_COMMIT = "925ebb8364f5ec2cb0614305900782f44d32a3ab"
SUPPLEMENT_BASE = f"https://raw.githubusercontent.com/notofonts/noto-fonts/{SUPPLEMENT_COMMIT}/"
SUPPLEMENT_FONTS = (
    {"path": "hinted/ttf/NotoSansRunic/NotoSansRunic-Regular.ttf", "name": "NotoSansRunic-Regular.ttf",
     "bytes": 9044, "sha256": "49e540798dedccf24301437fb5ea5c8a3f42143b1b6c108c1d48c78a41c4ce0f",
     "gitBlob": "114595f54023e304a3e66c9f904da56e5d1b13f4", "family": "Noto Sans Runic",
     "postscript": "NotoSansRunic-Regular", "coverage": "ᛟᚠ"},
    {"path": "hinted/ttf/NotoSansSymbols2/NotoSansSymbols2-Regular.ttf", "name": "NotoSansSymbols2-Regular.ttf",
     "bytes": 656852, "sha256": "876598db5f1e3bef2bc77c4d94c5b0d5d2c1620be4878a06f18fb5434e331688",
     "gitBlob": "45df8301c7bdfaccff52e30a0973132e94b2e95d", "family": "Noto Sans Symbols2",
     "postscript": "NotoSansSymbols2-Regular", "coverage": "⌖"},
)
SUPPLEMENT_LICENSE = {"path": "LICENSE", "name": "LICENSE", "bytes": 4377,
    "sha256": "0dab92d0544f7b233403f14b84a663bdbfa746982eda629e7f4f9ffe1b036feb",
    "gitBlob": "c82d72e422e2d08c5ab439b6bac7c2177ea0c565"}


def run(*arguments: str) -> str:
    result = subprocess.run(arguments, check=True, capture_output=True, text=True,
                            timeout=45)
    return result.stdout.strip() or result.stderr.strip()


def verify_bytes(data: bytes, expected: dict) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if len(data) != expected["bytes"] or digest != expected["sha256"] or blob != expected["gitBlob"]:
        raise RuntimeError(f"Pinned file integrity mismatch: {expected['path']}")
    return {"path": expected["path"], "bytes": len(data), "sha256": digest,
            "gitBlob": blob, "crc32": f"{zlib.crc32(data):08x}", "url": expected.get("baseUrl", BASE_URL) + expected["path"]}


def charset_contains(charset: str, codepoint: int) -> bool:
    for token in charset.split():
        endpoints = token.split("-")
        if len(endpoints) not in (1, 2):
            raise ValueError(f"Unexpected fontconfig charset token: {token}")
        low = int(endpoints[0], 16)
        high = int(endpoints[-1], 16)
        if low <= codepoint <= high:
            return True
    return False


def glyph_coverage(font_path: Path, text: str) -> dict:
    charset = run("fc-query", "--format", "%{charset}", str(font_path))
    glyphs = [{"character": char, "codepoint": f"U+{ord(char):04X}",
               "covered": charset_contains(charset, ord(char))}
              for char in dict.fromkeys(text)]
    if not glyphs or not all(glyph["covered"] for glyph in glyphs):
        raise RuntimeError(f"Installed font lacks required glyphs: {font_path.name}: {glyphs}")
    return {"text": text, "method": "fc-query FreeType/fontconfig charset", "glyphs": glyphs}


def registered_font(font_path: Path, family: str, postscript: str, coverage: str) -> dict:
    selected = Path(run("fc-match", "-f", "%{file}", family))
    if selected.resolve() != font_path.resolve():
        raise RuntimeError(f"Pinned font did not resolve through fontconfig: {selected}")
    actual_family = run("fc-query", "--format", "%{family}", str(font_path))
    if family not in actual_family.split(","):
        raise RuntimeError(f"Unexpected installed font family: {actual_family}")
    actual_postscript = run("fc-query", "--format", "%{postscriptname}", str(font_path))
    if actual_postscript != postscript:
        raise RuntimeError(f"Unexpected installed PostScript font name: {actual_postscript}")
    return {"registeredFile": str(selected), "registeredFamily": actual_family,
            "registeredPostScriptName": actual_postscript, "glyphCoverage": glyph_coverage(font_path, coverage)}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".modhunter-font-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(output: Path) -> dict:
    report = {"schema": 1, "ok": False, "scope": "Linux CI font prerequisite; not bundled in Modhunter",
              "scriptSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "upstreamRepository": "notofonts/noto-cjk", "upstreamCommit": UPSTREAM_COMMIT,
              "license": "OFL-1.1", "fontFamily": FONT_FAMILY, "files": []}

    def checkpoint() -> None:
        atomic_write(output, (json.dumps(report, indent=2) + "\n").encode())

    checkpoint()
    try:
        if platform.system() != "Linux" or os.environ.get("CI", "").lower() != "true":
            raise RuntimeError("This prerequisite is restricted to Linux CI jobs")
        for executable in ("fc-cache", "fc-match", "fc-query"):
            if not shutil.which(executable):
                raise RuntimeError(f"Required fontconfig command is unavailable: {executable}")
        data_root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        if not data_root.is_absolute():
            raise RuntimeError("XDG_DATA_HOME must be an absolute path")
        font_dir = data_root / "fonts/modhunter-ci" / UPSTREAM_COMMIT
        supplement_dir = data_root / "fonts/modhunter-ci" / SUPPLEMENT_COMMIT
        report["fontDirectory"] = str(font_dir)
        report["genericMatchBefore"] = run("fc-match", "-f", "%{file}\n%{family}\n", ":lang=zh-tw")
        pending = []
        downloads = [(expected, font_dir) for expected in FILES]
        downloads += [({**expected, "baseUrl": SUPPLEMENT_BASE}, supplement_dir)
                      for expected in (*SUPPLEMENT_FONTS, SUPPLEMENT_LICENSE)]
        for expected, directory in downloads:
            destination = directory / expected["name"]
            if destination.is_file():
                data = destination.read_bytes()
                record = verify_bytes(data, expected)
                record["cached"] = True
            else:
                request = urllib.request.Request(expected.get("baseUrl", BASE_URL) + expected["path"],
                                                 headers={"User-Agent": "Modhunter-CI-Font-QA/0.12"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read(expected["bytes"] + 1)
                record = verify_bytes(data, expected)
                record["cached"] = False
            report["files"].append(record)
            pending.append((destination, data))
        # Verify all font/license artifacts before writing any of them.
        for destination, data in pending:
            atomic_write(destination, data)
        run("fc-cache", "-f", str(font_dir), str(supplement_dir))
        font_path = font_dir / FONT_NAME
        report.update(registered_font(font_path, FONT_FAMILY, FONT_POSTSCRIPT, COVERAGE_TEXT))
        report["supplementalGlyphCoverage"] = {"fullwidthZoom": {
            "fontFamily": FONT_FAMILY, "postScriptName": FONT_POSTSCRIPT,
            **glyph_coverage(font_path, "＋－")}}
        report["supplementalFonts"] = []
        for expected in SUPPLEMENT_FONTS:
            record = registered_font(supplement_dir / expected["name"], expected["family"],
                                     expected["postscript"], expected["coverage"])
            report["supplementalFonts"].append({
                "upstreamRepository": "notofonts/noto-fonts", "upstreamCommit": SUPPLEMENT_COMMIT,
                "license": "OFL-1.1", "fontFamily": expected["family"],
                "path": expected["path"], "bytes": expected["bytes"], "sha256": expected["sha256"], **record})
        report.update(
                      genericMatchAfter=run("fc-match", "-f", "%{file}\n%{family}\n", ":lang=zh-tw"),
                      fontconfigVersion=run("fc-query", "--version"), ok=True)
        checkpoint()
        return report
    except Exception as error:
        report["error"] = str(error)
        checkpoint()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("native/target/ci-fonts.json"))
    args = parser.parse_args()
    report = prepare(args.output)
    print(json.dumps({"ok": report["ok"], "fontFamily": report["fontFamily"],
                      "upstreamCommit": report["upstreamCommit"], "receipt": str(args.output)}))


if __name__ == "__main__":
    main()
