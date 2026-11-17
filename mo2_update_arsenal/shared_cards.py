"""Read-only shared-card discovery; independent of either game's executable.

Only metadata is returned. This does not import a game engine, convert saves, award
rewards, fetch popularity, or expose a browser-controlled path/SQL operation.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sys

PROFILE_BUDGET = 64 * 1024 * 1024


def shared_cards_path() -> Path:
    if sys.platform == 'win32':
        raw = os.environ.get('LOCALAPPDATA')
        base = Path(raw) if raw else None
    elif sys.platform == 'darwin':
        raw = os.environ.get('HOME')
        base = Path(raw) / 'Library/Application Support' if raw else None
    else:
        raw = os.environ.get('XDG_DATA_HOME')
        home = os.environ.get('HOME')
        base = Path(raw) if raw is not None else Path(home) / '.local/share' if home else None
    if base is None or not base.is_absolute():
        raise ValueError('No absolute per-user data directory')
    return base / 'Noxviola/SharedGames/cardgame.sqlite3'


def inspect_shared_cards() -> dict:
    """Read one consistent main-slot snapshot, never create or migrate a store."""
    result = {'ok': True, 'mode': 'read-only', 'playable': False}
    # MO2's embedded Python may lack the SQLite DLL. Optional discovery must
    # never prevent the main plugin or independently bundled Rust transport loading.
    try:
        import sqlite3
    except ImportError:
        return {**result, 'state': 'unavailable', 'reason': 'Embedded Python SQLite support unavailable; native card transport remains independent'}
    try:
        path = shared_cards_path()
        if path.is_symlink():
            return {**result, 'state': 'unavailable', 'reason': 'Symlink store is not supported'}
        if not path.exists():
            return {**result, 'state': 'absent'}
        # mode=ro does not create a missing file. No immutable=1: writers may be active.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.25)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN')
            if db.execute('PRAGMA application_id').fetchone()[0] != 0x47534344:
                return {**result, 'state': 'unsupported', 'reason': 'Different database identity'}
            if db.execute('PRAGMA user_version').fetchone()[0] not in (1, 2):
                return {**result, 'state': 'unsupported', 'reason': 'Unknown storage schema'}
            row = db.execute("SELECT revision,ruleset,length(CAST(profile AS BLOB)) FROM snapshots WHERE slot='main' ORDER BY revision DESC LIMIT 1").fetchone()
            if row is None:
                return {**result, 'state': 'absent'}
            revision, ruleset, size = row
            if type(revision) is not int or revision <= 0:
                return {**result, 'state': 'unsupported', 'reason': 'Invalid snapshot revision'}
            if ruleset != 'gamesync-cardgame-v3':
                return {**result, 'state': 'unsupported', 'reason': 'Unknown profile ruleset'}
            if not isinstance(size, int) or size > PROFILE_BUDGET:
                return {**result, 'state': 'unavailable', 'reason': 'Profile exceeds reader byte budget; save unchanged'}
            raw = db.execute("SELECT profile FROM snapshots WHERE slot='main' AND revision=?", (revision,)).fetchone()[0]
            profile = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-JSON number')))
            objects = ('match', 'collection', 'heroes', 'packs', 'progression', 'quests', 'achievements', 'stats', 'cosmetics', 'settings')
            if not isinstance(profile, dict) or type(profile.get('version')) is not int or profile['version'] != 3 or any(not isinstance(profile.get(k), dict) for k in objects) or not isinstance(profile.get('decks'), list):
                return {**result, 'state': 'unsupported', 'reason': 'Invalid or unsupported profile shape'}
            catalog = profile.get('sharedCardCatalog')
            snapshot = catalog.get('sharedCardSnapshot') if isinstance(catalog, dict) else None
            entries = snapshot.get('entries') if isinstance(snapshot, dict) else None
            # Do not imply compatibility/playability merely because a save is readable.
            return {**result, 'state': 'available', 'revision': revision,
                    'catalogSnapshotPresent': isinstance(entries, list),
                    'cardCount': len(entries) if isinstance(entries, list) else None,
                    'reason': 'Shared save detected; ModHunter gameplay adapter is not installed'}
    except (OSError, ValueError, TypeError, sqlite3.Error, RecursionError):
        # Do not leak absolute paths, profile contents or raw database errors to clients.
        return {**result, 'state': 'unavailable', 'reason': 'Shared save could not be read; nothing changed'}


if __name__ == '__main__':
    print(json.dumps(inspect_shared_cards()))
