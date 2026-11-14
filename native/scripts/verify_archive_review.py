#!/usr/bin/env python3
"""Exercise the actual Rust archive reviewer against complete byte fixtures.

This gate covers parser/extraction safety; it does not certify native MO2 or
WebView2 interaction. Its JSON proof binds the exact CLI, helper and source tree.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[2]

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def run(args, **kw):
    return subprocess.run([str(a) for a in args], capture_output=True, timeout=200, **kw)

def vint(n):
    result = bytearray()
    while n >= 128:
        result.append((n & 127) | 128)
        n >>= 7
    result.append(n)
    return bytes(result)

def rar4(path, name, payload):
    # RAR 4 stored method; CRC covers each complete header excluding HEAD_CRC.
    def header(body):
        return struct.pack('<H', zlib.crc32(body) & 0xffff) + body
    encoded = name.encode('ascii')
    main = header(struct.pack('<BHHHI', 0x73, 0, 13, 0, 0))
    file = header(struct.pack('<BHHIIBIIBBHI', 0x74, 0x8000, 32 + len(encoded),
        len(payload), len(payload), 2, zlib.crc32(payload), 0, 20, 0x30, len(encoded), 0x20) + encoded)
    end = header(struct.pack('<BHH', 0x7b, 0, 7))
    path.write_bytes(b'Rar!\x1a\x07\x00' + main + file + payload + end)

def rar5(path, name, payload):
    # Stored RAR 5 headers follow https://www.rarlab.com/technote.htm.
    def header(body):
        data = vint(len(body)) + body
        return struct.pack('<I', zlib.crc32(data)) + data
    encoded = name.encode('utf-8')
    body = vint(2) + vint(2) + vint(len(payload)) + vint(4) + vint(len(payload)) + vint(32)
    body += struct.pack('<I', zlib.crc32(payload)) + vint(0) + vint(0) + vint(len(encoded)) + encoded
    path.write_bytes(b'Rar!\x1a\x07\x01\x00' + header(b'\x01\x00\x00') + header(body) + payload + header(b'\x05\x00\x00'))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cli', required=True, type=Path)
    parser.add_argument('--helper', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    cli = args.cli.resolve()
    helper = (args.helper or cli.parent / 'archive-tools' / ('7z.exe' if os.name == 'nt' else '7zz')).resolve()
    checks = []
    proof = {'schema': 1, 'kind': 'actual-archive-review', 'ok': False, 'platform': platform.system(),
        'checks': checks, 'cliSha256': sha(cli), 'helperSha256': sha(helper), 'nativeMo2Claim': False,
        'nativeWebView2Claim': False}
    if os.name == 'nt':
        proof['helperDllSha256'] = sha(helper.with_name('7z.dll'))
    source_files = ['native/crates/modhunter-core/src/archive_review.rs',
        'native/crates/modhunter-core/src/acquisition/mod.rs',
        'native/crates/modhunter-core/src/content_vault/mod.rs',
        'native/crates/modhunter-core/src/bin/modhunter-cli.rs',
        'native/scripts/prepare_archive_tools.py', 'native/scripts/verify_archive_review.py']
    sources = {p: sha(ROOT / p) for p in source_files}
    proof['sources'] = sources
    proof['candidateCommit'] = run(['git', 'rev-parse', 'HEAD'], cwd=ROOT).stdout.decode().strip()
    proof['sourceDirty'] = bool(run(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT).stdout.strip())
    proof['version'] = run([cli, '--version']).stdout.decode().strip()
    helper_version = run([helper, 'i'])
    if helper_version.returncode:
        raise RuntimeError('Pinned helper could not run')
    proof['helperVersion'] = helper_version.stdout.decode(errors='replace').strip().splitlines()[0]
    env = dict(os.environ, MODHUNTER_ARCHIVE_HELPER=str(helper))
    originals = {}
    with tempfile.TemporaryDirectory(prefix='modhunter-archive-gate-') as work:
        work = Path(work)
        serial = 0
        def zipmake(name, files):
            path = work / name
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for key, data in files.items(): archive.writestr(key, data)
            originals[path] = sha(path)
            return path
        def request(archive, stage=None, action='inspect', **extra):
            nonlocal serial
            serial += 1
            return {'action': action, 'archive': str(archive), 'stage': str(stage or work / f'stage-{serial}'),
                    'expectedSha256': sha(archive), 'expectedBytes': archive.stat().st_size, **extra}
        def call(req, passed=True, custom_env=None):
            result = run([cli, 'archive-review', '--request', '-'], input=json.dumps(req).encode(), env=custom_env or env)
            if passed:
                if result.returncode: raise AssertionError(result.stderr.decode(errors='replace'))
                body = json.loads(result.stdout)
                if not body.get('ok'): raise AssertionError(body)
                return body
            if not result.returncode: raise AssertionError('Unsafe input was accepted: ' + result.stdout.decode(errors='replace')[:1000])
            return result
        def verify(body, expected):
            actual = {row['path']: row for row in body['entries']}
            if set(actual) != set(expected): raise AssertionError(f'Full manifest differs: {set(actual)} != {set(expected)}')
            for name, data in expected.items():
                if actual[name]['bytes'] != len(data) or actual[name]['sha256'] != hashlib.sha256(data).hexdigest(): raise AssertionError(name)
                if (Path(body['incomingRoot']) / name).read_bytes() != data: raise AssertionError('Extracted bytes differ')
        def check(name, fn):
            try:
                fn()
            except Exception as exc:
                checks.append({'name': name, 'ok': False, 'error': str(exc)})
                print(f'FAIL {name}: {exc}', file=sys.stderr)
            else:
                checks.append({'name': name, 'ok': True})
                print('PASS ' + name)
        flat = {'meshes/test.nif': b'mesh\x00fixture', 'textures/test.dds': b'texture\x00fixture', 'meta.ini': b'archive metadata', 'readme.txt': b'optional text'}
        archive = zipmake('flat.zip', flat)
        base_req = request(archive)
        base = {}
        def flat_case():
            base.update(call(base_req)); verify(base, flat)
            if base['layout'] != 'gameData' or base['requiresInstaller']: raise AssertionError(base)
            verified = call({**base_req, 'action': 'verify'})
            if verified['manifestDigest'] != sha(Path(base['stage']) / '.modhunter-review-owned.json'): raise AssertionError('Marker digest differs')
        check('zip_flat_complete_bytes', flat_case)
        def wrapper():
            body = call(request(zipmake('wrapper.zip', {'Example/Data/meshes/test.nif': b'one'})))
            verify(body, {'meshes/test.nif': b'one'})
            if not body['incomingRoot'].replace('\\', '/').endswith('/Example/Data'): raise AssertionError(body)
        check('zip_unambiguous_wrapper', wrapper)
        fomod = zipmake('fomod.zip', {'fomod/ModuleConfig.xml': b'<config/>', 'option1/meshes/test.nif': b'choice'})
        fomod_req = request(fomod)
        def installer():
            body = call(fomod_req)
            if not body['requiresInstaller']: raise AssertionError('Native choices were hidden')
            verify(body, {'fomod/ModuleConfig.xml': b'<config/>', 'option1/meshes/test.nif': b'choice'})
        check('fomod_choices_remain_unknown', installer)
        def unknown():
            body = call(request(zipmake('unknown.zip', {'one/data.bin': b'a', 'two/settings.txt': b'b'})))
            if body['layout'] != 'needsReview': raise AssertionError(body)
            verify(body, {'one/data.bin': b'a', 'two/settings.txt': b'b'})
        check('unknown_layout_preserves_files', unknown)
        def sidecars():
            body = call(request(zipmake('sidecars.zip', {'Data/meshes/a.nif': b'a', 'readme.txt': b'b'})))
            verify(body, {'Data/meshes/a.nif': b'a', 'readme.txt': b'b'})
            if body['layout'] != 'needsReview': raise AssertionError(body)
        check('sidecars_not_silently_dropped', sidecars)
        def seven():
            source = work / 'seven-source'; (source / 'meshes').mkdir(parents=True); (source / 'meshes/a.nif').write_bytes(b'lzma2 bytes')
            path = work / 'compressed.7z'
            result = run([helper, 'a', '-t7z', '-m0=lzma2', path, 'meshes'], cwd=source)
            if result.returncode: raise AssertionError(result.stderr)
            originals[path] = sha(path)
            verify(call(request(path)), {'meshes/a.nif': b'lzma2 bytes'})
        check('seven_zip_lzma2_bytes', seven)
        def rar_case(version):
            path = work / f'stored{version}.rar'; payload = f'RAR{version} exact bytes'.encode()
            (rar4 if version == 4 else rar5)(path, 'mesh.nif', payload); originals[path] = sha(path)
            verify(call(request(path)), {'mesh.nif': payload})
        check('rar4_stored_bytes', lambda: rar_case(4))
        check('rar5_stored_bytes', lambda: rar_case(5))
        def bad(name, files):
            req = request(zipmake(name + '.zip', files)); call(req, False)
            if Path(req['stage']).exists(): raise AssertionError('Rejected archive left an extraction stage')
        check('parent_traversal', lambda: bad('traversal', {'../outside.nif': b'no'}))
        check('case_collision', lambda: bad('case', {'Meshes/a.nif': b'a', 'meshes/A.nif': b'b'}))
        check('file_directory_alias', lambda: bad('alias', {'meshes': b'a', 'meshes/a.nif': b'b'}))
        check('alternate_stream', lambda: bad('ads', {'meshes/a.nif:secret': b'no'}))
        check('device_name', lambda: bad('device', {'CON.nif': b'no'}))
        def link():
            path = work / 'symlink.zip'
            info = zipfile.ZipInfo('meshes/link.nif'); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(path, 'w') as zipped: zipped.writestr(info, '../../outside')
            originals[path] = sha(path); req = request(path); call(req, False)
            if Path(req['stage']).exists(): raise AssertionError('Link extraction began')
        check('symbolic_link_before_extract', link)
        def encrypted():
            source = work / 'secret.nif'; source.write_bytes(b'encrypted'); path = work / 'encrypted.zip'
            result = run([helper, 'a', '-tzip', '-pfixture-password', path, source])
            if result.returncode: raise AssertionError(result.stderr)
            originals[path] = sha(path); req = request(path); call(req, False)
            if Path(req['stage']).exists(): raise AssertionError('Encrypted extraction began')
        check('encrypted_requires_manual_handling', encrypted)
        def corrupt():
            path = work / 'corrupt.zip'; path.write_bytes(b'PK\x03\x04not-a-complete-archive'); originals[path] = sha(path)
            req = request(path); call(req, False)
            if Path(req['stage']).exists(): raise AssertionError('Corrupt stage survived')
        check('corrupt_archive_does_not_leave_stage', corrupt)
        check('changed_receipt_hash', lambda: call(request(archive, expectedSha256='0' * 64), False))
        def untrusted():
            fake = work / ('fake.exe' if os.name == 'nt' else 'fake-helper')
            fake.write_bytes(b'#!/bin/sh\necho ran > should-not-exist\n'); fake.chmod(0o700)
            result = call(request(archive), False, dict(env, MODHUNTER_ARCHIVE_HELPER=str(fake)))
            if b'pinned official' not in result.stderr or (ROOT / 'should-not-exist').exists(): raise AssertionError(result.stderr)
        check('untrusted_helper_not_executed', untrusted)
        selected = {}
        def selection():
            result = call({**base_req, 'action': 'prepareSelected', 'selected': ['meshes/test.nif']}); selected.update(result)
            prepared = result['preparedArchive']; path = Path(prepared['path'])
            with zipfile.ZipFile(path) as zipped:
                files = [i for i in zipped.infolist() if not i.is_dir()]
                if len(files) != 1 or files[0].filename != 'meshes/test.nif' or zipped.read(files[0]) != flat['meshes/test.nif']: raise AssertionError('Selected archive has extra/missing bytes')
            if prepared['sha256'] != sha(path) or prepared['originalSha256'] != sha(archive): raise AssertionError('Source/derived provenance mismatch')
            receipt = json.loads((Path(base['stage']) / ('selected-' + prepared['selectionDigest'] + '.json')).read_bytes())
            if receipt != prepared: raise AssertionError('Durable selected receipt differs')
        check('selected_archive_exact_bytes_and_provenance', selection)
        def complete_selection():
            paths = ['meshes/test.nif', 'textures/test.dds', 'readme.txt']
            body = call({**base_req, 'action': 'prepareSelected', 'selected': paths})
            with zipfile.ZipFile(body['preparedArchive']['path']) as zipped:
                files = [i.filename for i in zipped.infolist() if not i.is_dir()]
                if set(files) != set(paths): raise AssertionError('Requested full selection was truncated')
                for name in files:
                    if zipped.read(name) != flat[name]: raise AssertionError(name)
        check('selected_archive_preserves_full_requested_set', complete_selection)
        check('selection_empty', lambda: call({**base_req, 'action': 'prepareSelected', 'selected': []}, False))
        check('selection_unknown', lambda: call({**base_req, 'action': 'prepareSelected', 'selected': ['missing.nif']}, False))
        check('selection_traversal', lambda: call({**base_req, 'action': 'prepareSelected', 'selected': ['../outside.nif']}, False))
        check('selection_duplicate', lambda: call({**base_req, 'action': 'prepareSelected', 'selected': ['meshes/test.nif'] * 2}, False))
        check('fomod_cannot_be_filtered_around_native_choices', lambda: call({**fomod_req, 'action': 'prepareSelected', 'selected': ['option1/meshes/test.nif']}, False))
        def unchanged():
            if any(sha(p) != expected for p, expected in originals.items()): raise AssertionError('Original archive mutated')
            if sha(cli) != proof['cliSha256'] or sha(helper) != proof['helperSha256']: raise AssertionError('Executable changed')
            if {p: sha(ROOT / p) for p in source_files} != sources: raise AssertionError('Source changed during gate')
            if os.name == 'nt' and sha(helper.with_name('7z.dll')) != proof['helperDllSha256']: raise AssertionError('Helper DLL changed')
        check('source_files_and_helpers_stayed_unchanged', unchanged)
    proof['ok'] = len(checks) == 26 and all(c['ok'] for c in checks)
    proof['checkCount'] = len(checks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    print(f"{sum(c['ok'] for c in checks)}/{len(checks)} archive checks passed; proof: {args.output}")
    return 0 if proof['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
