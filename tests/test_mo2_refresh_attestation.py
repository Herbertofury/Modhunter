"""Exercise exact smoke attestation without claiming a native receipt."""
import ast
import hashlib
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest

RUNNER = (Path(__file__).resolve().parents[1]
          / "native/scripts/mo2_smoke_plugin/modhunter_native_smoke/runner.py")
MODULES = ("refresh_work", "snapshot_projection")


class RefreshAttestationTests(unittest.TestCase):
    def setUp(self):
        fn, = [n for n in ast.parse(RUNNER.read_text(encoding="utf-8")).body
               if isinstance(n, ast.FunctionDef) and n.name == "loaded_refresh_source_hashes"]
        scope = {k: v for k, v in globals().items() if k in ("Path", "stat", "hashlib")}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(RUNNER), "exec"), scope)
        self.observe = scope["loaded_refresh_source_hashes"]
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.libs = Path(folder.name) / "fixture-libs"
        self.package = self.libs / "mo2_update_arsenal"
        self.package.mkdir(parents=True)
        self.modules = {}
        self.expected = {}
        for stem in MODULES:
            path = self.package / (stem + ".py")
            body = f"# fixture {stem}\n".encode()
            path.write_bytes(body)
            key = "mo2_update_arsenal." + stem
            self.modules[key] = SimpleNamespace(__file__=str(path), __name__=key)
            self.expected[path.name] = hashlib.sha256(body).hexdigest()

    def test_exact_two_loaded_module_hashes_only(self):
        self.assertEqual(self.observe(self.libs, self.modules), self.expected)
        self.assertTrue(all("/" not in k and "\\" not in k for k in self.expected))

    def test_existing_on_disk_but_not_loaded_is_refused(self):
        del self.modules["mo2_update_arsenal.refresh_work"]
        with self.assertRaisesRegex(RuntimeError, "Expected loaded fixture production module: refresh_work.py"):
            self.observe(self.libs, self.modules)

    def test_same_basename_outside_owned_copy_is_refused(self):
        module = self.modules["mo2_update_arsenal.refresh_work"]
        outside = self.libs.parent / "refresh_work.py"
        outside.write_text("# external fixture")
        module.__file__ = str(outside)
        with self.assertRaisesRegex(RuntimeError, "Expected loaded fixture production module: refresh_work.py"):
            self.observe(self.libs, self.modules)

    def test_linked_source_not_accepted_even_if_bytes_match(self):
        path = self.package / "snapshot_projection.py"
        target = self.libs.parent / path.name
        path.rename(target)
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest("This Windows test account cannot create a fixture symlink")
        with self.assertRaisesRegex(RuntimeError, "Expected loaded fixture production module: snapshot_projection.py"):
            self.observe(self.libs, self.modules)

    def test_missing_on_disk_does_not_leak_path(self):
        (self.package / "refresh_work.py").unlink()
        with self.assertRaises(RuntimeError) as failure:
            self.observe(self.libs, self.modules)
        self.assertEqual(str(failure.exception), "Cannot read loaded fixture production module: refresh_work.py")
