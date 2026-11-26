"""Plan a non-destructive synchronization into a separate wiki checkout."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

LEDGER = ".modhunter-managed.json"
PAGE = re.compile(r"[A-Za-z0-9_-]+\.md")
HASH = re.compile(r"[0-9a-f]{64}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_optional(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Expected a regular file: {path.name}")
    return path.read_bytes() if path.exists() else None


def decode_ledger(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate ownership entry")
            result[key] = value
        return result

    values = json.loads(data, object_pairs_hook=unique) if data is not None else {}
    if not isinstance(values, dict) or any(
        not PAGE.fullmatch(k) or k.lower() == "readme.md"
        or not isinstance(v, str) or not HASH.fullmatch(v)
        for k, v in values.items()
    ) or len({k.lower() for k in values}) != len(values):
        raise ValueError("Invalid wiki ownership ledger")
    return values


def plan(source, target):
    if any(p.is_symlink() or not p.is_dir() for p in (source, target)):
        raise ValueError("Use two existing, non-symlink directories")
    a, b = source.resolve(), target.resolve()
    if a == b or a in b.parents or b in a.parents:
        raise ValueError("Source and target must be separate directories")
    pages = sorted(p for p in source.glob("*.md") if p.name.lower() != "readme.md")
    if not pages:
        raise ValueError("No publishable source pages")
    ledger_path = target / LEDGER
    ledger_bytes = read_optional(ledger_path)
    previous = decode_ledger(ledger_bytes)
    names = {}
    for existing in target.iterdir():
        names.setdefault(existing.name.casefold(), []).append(existing.name)

    updates = []
    ownership = dict(previous)
    conflicts = []
    seen = set()
    for page in pages:
        name = page.name
        folded = name.casefold()
        if not PAGE.fullmatch(name) or folded in seen:
            raise ValueError(f"Invalid or case-colliding source page: {name}")
        seen.add(folded)
        aliases = names.get(folded, [])
        if any(alias != name for alias in aliases) or any(
            old.casefold() == folded and old != name for old in previous
        ):
            conflicts.append(f"{name} (case collision)")
            continue
        incoming = read_optional(page)
        if incoming is None:
            raise ValueError(f"Source page vanished: {name}")
        dest = target / name
        current = read_optional(dest)
        if name in previous and current is None:
            conflicts.append(f"{name} (previously managed page removed)")
            continue
        if current != incoming:
            if current is not None and previous.get(name) != digest(current):
                conflicts.append(f"{name} (unmanaged or independently edited)")
                continue
            updates.append((dest, current, incoming))
        ownership[name] = digest(incoming)

    if conflicts:
        raise ValueError("Wiki conflicts; no files changed: " + ", ".join(conflicts))
    new_ledger = (json.dumps(ownership, indent=2, sort_keys=True) + "\n").encode()
    if new_ledger != ledger_bytes:
        if any(alias != LEDGER for alias in names.get(LEDGER.casefold(), [])):
            raise ValueError("Wiki ownership ledger case collision")
        updates.append((ledger_path, ledger_bytes, new_ledger))
    return updates


def synchronize(source, target, *, apply=False):
    changes = plan(source, target)
    if apply:
        # Never apply a plan to drifted bytes. The caller uses a fresh checkout
        # and an ordinary fast-forward push as the remote concurrency guard.
        if any(read_optional(dest) != before for dest, before, _ in changes):
            raise ValueError("Wiki changed during planning; recheck")
        for dest, _, after in changes:
            dest.write_bytes(after)
    return [dest.name for dest, _, _ in changes]


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("source", type=Path)
    args.add_argument("target", type=Path)
    args.add_argument("--apply", action="store_true", help="Write only after complete preflight")
    options = args.parse_args()
    try:
        changes = synchronize(options.source, options.target, apply=options.apply)
    except (ValueError, OSError) as exc:
        print(f"Wiki sync stopped: {exc}", file=sys.stderr)
        return 2
    print(("Prepared" if options.apply else "Would change") + ": " + (", ".join(changes) or "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
