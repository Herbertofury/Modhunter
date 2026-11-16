"""Prepare a complete additional workload; do not launch a browser or benchmark.

Only item group/groupId and the top-level groups metadata differ from the
canonical 11,207-record fixture. This builder performs no runtime measurement;
the comparison harness may explicitly select these same bytes for both arms.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import subprocess

BASE_COMMIT = "6ca747be97b98e17ea66c0fea9ef51be9b934e30"
RECORDS = 11207
GROUPS = 115
CANONICAL_BYTES = 4696493
CANONICAL_SHA256 = "afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0"
SOURCE_CATALOG_SHA256 = "7b63ff731bf4f66ff7c3ee6c2fa58732ca804ae2754d9a5d2b7ceade4375337e"
GENERATOR_SHA256 = "9dd5cc7cd0b1c491669e4e139c0a3a481e3a578652f3700c16569213d0b5c8a5"
EXPECTED_BYTES = 4848803
EXPECTED_SHA256 = "2bb436aefd65a8431da61b5a21b1e198f813c6ad835a4d268969fa205dee4562"


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def fixture_bytes(fixture: dict) -> bytes:
    # Identical serialization to measure_native_library.cjs's Python generator.
    return json.dumps(fixture, sort_keys=True).encode("utf-8")


def build_fixture(source_root: Path) -> dict:
    """Return the deterministic complete workload, with source and byte guards."""
    source_root = Path(source_root)
    web = source_root / "mo2_update_arsenal/web"
    catalog_bytes = (web / "catalog.json").read_bytes()
    generator_bytes = (source_root / "tests/test_frontend_responsiveness.py").read_bytes()
    assert sha256(catalog_bytes) == SOURCE_CATALOG_SHA256
    assert sha256(generator_bytes) == GENERATOR_SHA256
    source = json.loads(catalog_bytes)
    groups = source["groups"]
    assert len(groups) == GROUPS
    assert len({group["id"] for group in groups}) == GROUPS
    assert len({group["name"] for group in groups}) == GROUPS

    # Compile only the exact existing catalog() function. Importing or running
    # the test module, its test classes, Playwright or a browser is unnecessary.
    tree = ast.parse(generator_bytes)
    definitions = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "catalog"]
    assert len(definitions) == 1
    namespace = {"copy": copy, "json": json, "WEB": web}
    exec(compile(ast.Module(body=definitions, type_ignores=[]),
                 "canonical-catalog-generator", "exec"), namespace)
    canonical = namespace["catalog"](count=RECORDS)
    canonical["offline"] = True
    original_bytes = fixture_bytes(canonical)
    assert len(original_bytes) == CANONICAL_BYTES
    assert sha256(original_bytes) == CANONICAL_SHA256

    result = copy.deepcopy(canonical)
    quotient, remainder = divmod(RECORDS, GROUPS)
    assigned, offset = [], 0
    for index, original_group in enumerate(groups):
        count = quotient + (index < remainder)
        group = {**original_group, "count": count,
                 "startPriority": result["items"][offset]["priority"]}
        assigned.append(group)
        for item in result["items"][offset:offset + count]:
            item["groupId"] = group["id"]
            item["group"] = group["name"]
        offset += count
    result["groups"] = assigned

    assert offset == len(result["items"]) == RECORDS
    assert sum(group["count"] for group in assigned) == RECORDS
    assert max(group["count"] for group in assigned) - min(group["count"] for group in assigned) == 1
    assert [group["name"] for group in assigned] == [group["name"] for group in groups]
    assert {key: value for key, value in result.items() if key not in ("items", "groups")} == {
        key: value for key, value in canonical.items() if key not in ("items", "groups")}
    for index, (before, after) in enumerate(zip(canonical["items"], result["items"], strict=True)):
        assert before.keys() == after.keys()
        assert before["id"] == after["id"] == f"item-{index}"
        assert before["priority"] == after["priority"] == index
        assert {key: value for key, value in before.items() if key not in ("group", "groupId")} == {
            key: value for key, value in after.items() if key not in ("group", "groupId")}
    body = fixture_bytes(result)
    assert len(body) == EXPECTED_BYTES
    assert sha256(body) == EXPECTED_SHA256
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = build_fixture(args.source_root)
    body = fixture_bytes(fixture)
    provider_types = sorted({item["source"]["type"] for item in fixture["items"]})
    # Read the real shipped label function; this is module loading only, not a
    # browser, a test suite or a performance measurement.
    provider_source = args.source_root / "mo2_update_arsenal/web/source_insights.js"
    command = ["node", "-e", "const api=require(process.argv[1]);process.stdout.write(JSON.stringify(JSON.parse(process.argv[2]).map(type=>api.label(type))))",
               str(provider_source.resolve()), json.dumps(provider_types)]
    labels = json.loads(subprocess.run(command, check=True, capture_output=True,
                                       text=True, timeout=10).stdout)
    assert provider_types == ["nexus"] and labels == ["Nexus Mods"]
    chapter_labels = [group["name"] for group in fixture["groups"]]
    metadata = {
        "schema": 1,
        "kind": "prepared-multi-label-workload",
        "status": "prepared-only; no browser, performance run or acceptance claim",
        "preparedAgainstCommit": BASE_COMMIT,
        "sourceRepository": "https://github.com/Herbertofury/Modhunter",
        "canonicalFixture": {"records": RECORDS, "bytes": CANONICAL_BYTES,
                             "sha256": CANONICAL_SHA256,
                             "generator": "tests/test_frontend_responsiveness.py:catalog(count=11207)",
                             "generatorFileSha256": GENERATOR_SHA256,
                             "offline": True},
        "chapterSource": {"path": "mo2_update_arsenal/web/catalog.json",
                          "sha256": SOURCE_CATALOG_SHA256,
                          "bytes": (args.source_root / "mo2_update_arsenal/web/catalog.json").stat().st_size,
                          "allGroupsUsed": GROUPS,
                          "order": "original catalog group array order; no sorting or sampling"},
        "derivation": {
            "recordChanges": ["group", "groupId"],
            "topLevelChanges": ["items (only the two permitted fields)", "groups"],
            "distribution": "contiguous and balanced; first 52 groups have 98 records, remaining 63 have 97",
            "groupMetadata": "retain every original group id/name/color; recompute count/startPriority from assigned records",
            "preserved": "all 11,207 IDs, priority/order and every source/version/update/author/flags/audit/other record field; all other top-level fields",
            "serialization": "json.dumps(fixture, sort_keys=True).encode('utf-8'); default ensure_ascii and separators; no final newline"},
        "fixture": {"file": "fixture-11207-115-chapters.json", "records": RECORDS,
                    "bytes": len(body), "sha256": sha256(body)},
        "labelInventory": {"locale": "canonical English, before native translation",
                           "providerTypes": provider_types, "providerLiterals": labels,
                           "providerLiteralCount": len(set(labels)),
                           "chapterLiterals": chapter_labels,
                           "chapterLiteralCount": len(set(chapter_labels)),
                           "combinedDistinctLiteralCount": len(set(labels) | set(chapter_labels)),
                           "expectedLiteralRuleCount": len(set(labels)) + len(set(chapter_labels)),
                           "providerLabelSource": "mo2_update_arsenal/web/source_insights.js:label",
                           "providerLabelSourceSha256": sha256(provider_source.read_bytes())},
        "groups": fixture["groups"],
        "builderSha256": sha256(Path(__file__).read_bytes()),
        "nextAction": "Only if canonical full ABBA wins, root may authorize a separate unchanged-endpoint control/candidate comparison using these same complete bytes on both arms. Do not substitute this fixture for canonical release acceptance."}
    args.output.mkdir(parents=True, exist_ok=True)
    outputs = {metadata["fixture"]["file"]: body,
               "derivation.json": (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode("utf-8")}
    for name, content in outputs.items():
        target = args.output / name
        assert not target.exists() or target.read_bytes() == content, f"Refusing to replace different prepared bytes: {name}"
        target.write_bytes(content)
    print(json.dumps({"status": "prepared-only", "fixture": metadata["fixture"],
                      "providerLiteralCount": metadata["labelInventory"]["providerLiteralCount"],
                      "chapterLiteralCount": metadata["labelInventory"]["chapterLiteralCount"],
                      "expectedLiteralRuleCount": metadata["labelInventory"]["expectedLiteralRuleCount"]}, indent=2))


if __name__ == "__main__":
    main()
