"""Read-only file inventory for comparing the protected scope of an MO2 acceptance run."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from itertools import islice


def readonly(database):
    return database.resolve(strict=True).as_uri() + "?mode=ro"


def fingerprint(file_stat):
    return file_stat.st_size, file_stat.st_mtime_ns, file_stat.st_mode, getattr(
        file_stat, "st_file_attributes", 0
    )


def entries(root, full):
    stack = [(root, "", root.lstat())]
    while stack:
        path, relative, before = stack.pop()
        size, mtime, mode, attributes = fingerprint(before)
        digest = ""
        target = ""
        if stat.S_ISLNK(mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            kind = "link"
            target = os.readlink(path)
        elif stat.S_ISDIR(mode):
            kind = "directory"
            with os.scandir(path) as children:
                for child in children:
                    name = relative + "/" + child.name if relative else child.name
                    stack.append((Path(child.path), name, child.stat(follow_symlinks=False)))
        elif stat.S_ISREG(mode):
            kind = "file"
            if full or path.name.lower() == "meta.ini":
                with path.open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                if fingerprint(path.lstat()) != fingerprint(before):
                    raise RuntimeError(f"File changed while hashing: {path}")
        else:
            raise ValueError(f"Unsupported entry in protected scope: {path}")
        yield str(root), relative, kind, size, mtime, mode, attributes, digest, target


def capture(database, full, metadata):
    roots = [(Path(path).absolute(), mode) for mode, paths in
             (("full", full), ("metadata", metadata)) for path in paths]
    if not roots:
        raise ValueError("Specify at least one --full or --metadata protected root")
    destination = database.resolve()
    canonical = []
    for path, _ in roots:
        root = path.resolve(strict=True)
        if root != path:
            raise ValueError(f"Use the resolved protected root: {root}")
        if destination.is_relative_to(root):
            raise ValueError(f"Receipt must be outside the protected root: {root}")
        if any(root.is_relative_to(other) or other.is_relative_to(root) for other in canonical):
            raise ValueError(f"Overlapping protected root: {root}")
        canonical.append(root)
    with database.open("xb"):
        pass
    started = time.monotonic()
    with contextlib.closing(sqlite3.connect(database)) as db:
        db.executescript("""
            CREATE TABLE roots(root TEXT PRIMARY KEY, scope TEXT);
            CREATE TABLE run(version INTEGER, finished INTEGER);
            INSERT INTO run VALUES(1,0);
            CREATE TABLE entries(root TEXT, path TEXT, kind TEXT, size INTEGER,
               mtime_ns INTEGER, mode INTEGER, attributes INTEGER, sha256 TEXT, target TEXT,
               PRIMARY KEY(root,path)) WITHOUT ROWID;
        """)
        db.executemany("INSERT INTO roots VALUES(?,?)", [(str(path), scope) for path, scope in roots])
        db.commit()
        total_files = 0
        hashed_bytes = 0
        progress_at = started
        for root, scope in roots:
            rows = entries(root, scope == "full")
            while batch := list(islice(rows, 512)):
                with db:
                    db.executemany("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)", batch)
                total_files += sum(row[2] == "file" for row in batch)
                hashed_bytes += sum(row[3] for row in batch if row[7])
                now = time.monotonic()
                if now - progress_at >= 30:
                    print(json.dumps({"files": total_files, "hashedBytes": hashed_bytes,
                                      "elapsedSeconds": now - started}), flush=True)
                    progress_at = now
            print(json.dumps({"captured": str(root), "elapsedSeconds": time.monotonic() - started}), flush=True)
        db.execute("UPDATE run SET finished=1")
        db.commit()
        counts = db.execute("""
            SELECT count(*),sum(kind='file'),sum(sha256!=''),
                   sum(CASE WHEN kind='file' THEN size ELSE 0 END) FROM entries
        """).fetchone()
        print(json.dumps(dict(zip(("entries", "files", "hashedFiles", "logicalBytes"), counts))))


def compare(before, after):
    different = False
    with contextlib.closing(sqlite3.connect(readonly(before), uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS later", (readonly(after),))
        for schema in ("main", "later"):
            if db.execute(f"SELECT version, finished FROM {schema}.run").fetchall() != [(1, 1)]:
                raise ValueError(f"Incomplete or incompatible capture: {schema}")
        if db.execute("SELECT * FROM main.roots ORDER BY root").fetchall() != db.execute(
                "SELECT * FROM later.roots ORDER BY root").fetchall():
            raise ValueError("Protected roots and hashing scope must match")
        for old, new, label in (("main", "later", "before"), ("later", "main", "after")):
            for row in db.execute(f"SELECT * FROM {old}.entries EXCEPT SELECT * FROM {new}.entries"):
                print(json.dumps({"state": label, "entry": row}))
                different = True
    print(json.dumps({"equal": not different}))
    return 1 if different else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("capture", help="Write a new private SQLite receipt outside the protected scope")
    new.add_argument("database", type=Path)
    new.add_argument("--full", action="append", default=[], help="Hash every file under this root")
    new.add_argument("--metadata", action="append", default=[],
                     help="Record every path, length, timestamps and attributes; hash meta.ini files only")
    diff = commands.add_parser("compare", help="Print every changed entry as private JSON lines")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    args = parser.parse_args()
    if args.command == "compare":
        return compare(args.before, args.after)
    capture(args.database, args.full, args.metadata)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
