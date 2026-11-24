import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from native.scripts import export_nexus_history as report


class HistoricalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.catalog, self.db, self.html = root / "catalog.json", root / "public.sqlite", root / "report.html"
        self.first = {
            "id": "a",
            "name": "A </script><img src=x>",
            "sourceGame": "skyrimspecialedition",
            "nexusId": 0,
            "installedVersion": "1",
            "source": {"url": "javascript:alert(1)"},
            "notes": "private notes",
        }
        self.second = {
            "id": "b",
            "name": "B",
            "sourceGame": "skyrimspecialedition",
            "nexusId": 7,
            "installedVersion": "2",
            "mo2InternalName": "private path",
        }
        self.save_catalog([self.first, self.second])
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE projects(game,mod_id,payload,errors,seen)")
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?)",
                ("skyrimspecialedition", 7, "[null,null]", '["Mod not found"]', "2026-11-24T10:00:00+00:00"),
            )

    def save_catalog(self, items):
        self.catalog.write_text(
            json.dumps(
                {
                    "catalog": {
                        "offline": True,
                        "profileId": "example",
                        "profileName": "QA",
                        "importedAt": "2026-11-24T11:00:00Z",
                        "items": items,
                    }
                }
            ),
            encoding="utf-8",
        )

    def render(self, *args):
        return report.export(self.catalog, self.db, self.html, *args)

    def test_local_report_preserves_uncertainty_source_time_and_escaped_text(self):
        original = hashlib.sha256(self.db.read_bytes()).hexdigest()
        summary = self.render()
        self.assertEqual(summary["verdicts"], {"unverified": 1, "unavailable": 1})
        page = self.html.read_text(encoding="utf-8")
        self.assertNotIn(self.first["name"], page)
        self.assertNotIn("private notes", page)
        self.assertNotIn("private path", page)
        self.assertNotIn("javascript:alert", page)
        data = json.loads(page.split('id="data">', 1)[1].split("</script>", 1)[0])
        self.assertEqual(data["catalogSHA256"], hashlib.sha256(self.catalog.read_bytes()).hexdigest())
        self.assertEqual(data["rows"][0]["name"], self.first["name"])
        self.assertIsNone(data["rows"][0]["seen"])
        self.assertEqual(data["rows"][1]["seen"], "2026-11-24T10:00:00+00:00")
        self.assertEqual(
            data["rows"][1]["providerUrl"], "https://www.nexusmods.com/skyrimspecialedition/mods/7?tab=files"
        )
        self.assertEqual(original, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_catalog_and_prior_decisions_must_bind_before_replacing_output(self):
        summary = self.render()
        original = self.html.read_bytes()
        path = self.catalog.with_suffix(".jsonl")
        saved = []
        for item, verdict, why, seen, url in [
            (self.first, "unverified", "No Nexus project ID", None, None),
            (
                self.second,
                "unavailable",
                "Provider: Mod not found",
                "2026-11-24T10:00:00+00:00",
                "https://www.nexusmods.com/skyrimspecialedition/mods/7?tab=files",
            ),
        ]:
            saved.append(
                {
                    "name": item["name"],
                    "sourceGame": item["sourceGame"],
                    "nexusId": item["nexusId"],
                    "installed": item["installedVersion"],
                    "mo2Newest": None,
                    "observedAt": seen,
                    "sourceUrl": url,
                    "failure": why,
                    "decision": {"verdict": verdict},
                }
            )
        for rows in [
            saved[:1],
            [saved[0], saved[0]],
            [dict(saved[0], installed="different"), saved[1]],
            [saved[0], dict(saved[1], decision={"verdict": "current"})],
        ]:
            path.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.render(path, summary["catalogSHA256"])
            self.assertEqual(self.html.read_bytes(), original)
        path.write_text("\n".join(map(json.dumps, reversed(saved))), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.render(path, "0" * 64)
        self.assertEqual(self.render(path, summary["catalogSHA256"])["rows"], 2)
        self.save_catalog([self.first, dict(self.first, name="Different")])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.render()

    def test_desktop_json_binds_the_installed_archive_without_exposing_its_path(self):
        output = self.html.with_suffix(".json")

        def snapshot():
            report.export(self.catalog, self.db, output)
            return json.loads(output.read_bytes())

        original = snapshot()
        self.assertEqual(original["format"], "modhunter-nexus-history-v1")
        changed = dict(self.first, installationArchive="different-private-archive.zip")
        self.save_catalog([changed, self.second])
        fresh = snapshot()
        self.assertNotEqual(original["rows"][0]["binding"], fresh["rows"][0]["binding"])
        self.assertEqual(original["rows"][1]["binding"], fresh["rows"][1]["binding"])
        self.assertNotIn("different-private-archive", output.read_text(encoding="utf-8"))

    def test_mismatched_provider_identity_and_duplicate_projects_fail_closed(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE projects SET errors='[]',payload=?", (json.dumps([{"modId": 99}, []]),))
        with self.assertRaisesRegex(ValueError, "Invalid observation"):
            self.render()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO projects SELECT * FROM projects")
        with self.assertRaisesRegex(ValueError, "Duplicate observations"):
            self.render()
        self.assertFalse(self.html.exists())
