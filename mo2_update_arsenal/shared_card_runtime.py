"""Optional, locally installed card-store runtime. No dependency on another app.

The private runtime is not included in this public source tree. Its manifest is
an installation integrity check, not a signature or publisher-authentication claim.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from .shared_cards import shared_cards_path

REQUEST_BYTES = 64 * 1024 * 1024
REPLY_BYTES = REQUEST_BYTES + 1024


def runtime_installation() -> tuple[Path, dict]:
    folder = shared_cards_path().parent / 'runtime-v1'
    executable = folder / ('gs-card-save.exe' if sys.platform == 'win32' else 'gs-card-save')
    manifest = folder / 'manifest.json'
    if executable.is_symlink() or manifest.is_symlink():
        raise ValueError('Symlink runtime is not supported')
    with manifest.open('rb') as source:
        raw = source.read(4097)
    if len(raw) > 4096:
        raise ValueError('Runtime manifest too large')
    expected = json.loads(raw)
    if expected.get('protocol') != 1 or expected.get('executable') != executable.name:
        raise ValueError('Unsupported runtime manifest')
    with executable.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if expected.get('sha256') != digest:
        raise ValueError('Runtime integrity check failed')
    return executable, expected


def _runtime() -> Path:
    return runtime_installation()[0]


def invoke_shared_card_runtime(data: dict) -> dict:
    """Caller supplies protocol data only, never a path, shell, writer or ruleset.

    Clients MUST durably journal a full pending operation before commit. A lost
    response/timeout has unknown outcome; retry that exact operation, never rebase.
    """
    try:
        action = data.get('action')
        if action == 'load' and set(data) == {'action', 'slot'}:
            request = data
        elif action == 'commit' and set(data) == {'action', 'input'}:
            item = data['input']
            if not isinstance(item, dict) or set(item) != {'slot', 'operation', 'expectedRevision', 'profile'}:
                raise ValueError('Invalid commit fields')
            if type(item['expectedRevision']) is not int or item['expectedRevision'] < 0:
                raise ValueError('Invalid expected revision')
            request = {'action': 'commit', 'input': {'slot': item['slot'], 'operation': item['operation'],
                'expected_revision': item['expectedRevision'], 'profile': item['profile'],
                'writer': 'modhunter', 'ruleset': 'gamesync-cardgame-v3'}}
        else:
            raise ValueError('Unsupported card-store action')
        raw = json.dumps(request, allow_nan=False, ensure_ascii=False).encode('utf-8')
        if len(raw) > REQUEST_BYTES:
            raise ValueError('Card-store request exceeds byte budget')
        executable = _runtime()
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return {'ok': False, 'error': 'Card runtime unavailable or request invalid; nothing started', 'outcome': 'not-started'}
    environment = dict(os.environ)
    environment['GS_CARD_SAVE_REQUEST_MAX_BYTES'] = str(REQUEST_BYTES)
    try:
        # Stream replies to a temporary file rather than unbounded RAM. The helper
        # is trusted installed code; this is not an untrusted executable sandbox.
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=output,
                                     stderr=subprocess.DEVNULL, env=environment, shell=False)
            try:
                child.communicate(raw, timeout=30)
            except subprocess.TimeoutExpired:
                child.kill(); child.communicate()
                return {'ok': False, 'error': 'Card operation timed out; recover the exact pending operation', 'outcome': 'unknown'}
            output.seek(0)
            encoded = output.read(REPLY_BYTES + 1)
            if len(encoded) > REPLY_BYTES:
                raise ValueError('Card reply exceeds byte budget')
            result = json.loads(encoded)
            if not isinstance(result, dict) or type(result.get('ok')) is not bool:
                raise ValueError('Invalid runtime reply')
            if result['ok'] and child.returncode == 0 and set(result) == {'ok', 'result'}:
                return result
            if not result['ok'] and child.returncode == 1 and set(result) == {'ok', 'error'} and isinstance(result['error'], str) and result['error']:
                return {'ok': False, 'error': 'Card operation rejected; preserve pending progress and inspect shared state', 'outcome': 'rejected'}
            return {'ok': False, 'error': 'Card process response or exit was inconsistent; recover the exact pending operation', 'outcome': 'unknown'}
    except (OSError, ValueError, TypeError, RecursionError):
        return {'ok': False, 'error': 'Card operation response unavailable; recover the exact pending operation', 'outcome': 'unknown'}
