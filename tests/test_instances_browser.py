"""Actual Chromium coverage of the bundled MO2 Instances UI with native fixtures.

This proves renderer behavior and command boundaries. Disposable Windows/MO2
installation transactions are verified separately by the native gate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from browser_receipt import EvidenceResult, chromium_identity, screenshot_identity, source_identity

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "native/ui"
OUTPUT = Path(os.environ.get("MODHUNTER_QA_OUTPUT", ROOT / "native/target/instances-browser"))
VERSION = json.loads((ROOT / "native/crates/modhunter-desktop/tauri.conf.json").read_text())["version"]
BOOT = (ROOT / "tests/fixtures/instances_native_fixture.js").read_text("utf-8")
SOURCES = (
    "native/ui/instances.html", "native/ui/instances.css", "native/ui/instances.js",
    "native/ui/locales/instances.json", "native/ui/locale-catalogs.js",
    "native/ui/preferences.js", "native/ui/preferences.css",
    "tests/fixtures/instances_native_fixture.js", "tests/test_instances_browser.py",
    "tests/browser_receipt.py", "native/crates/modhunter-desktop/tauri.conf.json",
)


def snapshot():
    apps = [
        {"id": "app-a", "directory": "D:/MO2/Aurora", "executable": "D:/MO2/Aurora/ModOrganizer.exe", "version": "2.5.2", "architecture": "x64", "portableLocked": False, "plugin": {"version": "0.12.0", "status": "different"}, "runningPids": [], "sources": ["saved root"]},
        {"id": "app-b", "directory": "E:/Modding/旅程 Ranger", "executable": "E:/Modding/旅程 Ranger/ModOrganizer.exe", "version": "2.5.2", "architecture": "x64", "portableLocked": False, "plugin": {"version": None, "status": "missing"}, "runningPids": [], "sources": ["shortcut"]},
        {"id": "app-c", "directory": "F:/MO2/Long-archive-" + "complete-path-" * 12, "executable": "F:/MO2/Long-archive-" + "complete-path-" * 12 + "/ModOrganizer.exe", "version": "2.5.2", "architecture": "x64", "portableLocked": True, "plugin": {"version": VERSION, "status": "matching"}, "runningPids": [4321], "sources": ["running executable"]},
    ]
    instances = [
        {"id": "global", "name": "Shared Skyrim setup", "kind": "global", "configPath": "C:/Users/Fixture/AppData/Local/ModOrganizer/Shared Skyrim/ModOrganizer.ini", "baseDirectory": "G:/Instance data/Shared", "gameName": "Skyrim Special Edition", "gamePath": "G:/Games/Skyrim Special Edition", "selectedProfile": "Profile 0", "profilesDirectory": "H:/Profiles/Shared", "modsDirectory": "H:/Mods/Shared", "profiles": [{"name": f"Profile {i}", "path": f"H:/Profiles/Shared/{i}", "modlistPath": f"H:/Profiles/Shared/{i}/modlist.txt", "selected": i == 0} for i in range(1205)], "sources": ["global configuration"]},
        {"id": "portable", "name": "Source needed", "kind": "portable", "configPath": "D:/MO2/Aurora/ModOrganizer.ini", "baseDirectory": "D:/MO2/Aurora", "gameName": "Fallout 4", "selectedProfile": "Survival", "profilesDirectory": "D:/MO2/Aurora/profiles", "modsDirectory": "D:/MO2/Aurora/mods", "profiles": [{"name": "Survival", "path": "D:/MO2/Aurora/profiles/Survival", "selected": True}, {"name": "<script>fixtureOnly()</script>", "path": "D:/MO2/Aurora/profiles/Safe literal", "selected": False}], "sources": ["adjacent configuration"]},
        {"id": "orphan", "name": "Unassociated Oblivion setup", "kind": "external", "configPath": "I:/Old instance/ModOrganizer.ini", "gameName": "Oblivion", "selectedProfile": "Travel", "profiles": [{"name": "Travel", "path": "I:/Old instance/profiles/Travel", "selected": True}], "sources": ["added folder"]},
    ]
    return {"revision": 10, "version": VERSION, "package": {"available": True, "valid": True, "version": VERSION, "commit": "a" * 40, "message": "Verified bundled plugin fixture."},
        "inventory": {"installations": apps, "instances": instances, "associations": [{"installationId": "app-a", "instanceId": "portable", "kind": "portable", "evidence": "adjacent INI"}, {"installationId": "app-a", "instanceId": "global", "kind": "availableGlobal", "evidence": "unlocked app"}, {"installationId": "app-b", "instanceId": "global", "kind": "availableGlobal", "evidence": "unlocked app"}], "issues": [{"path": "J:/Protected folder", "kind": "access", "message": "This folder could not be read; other locations were retained."}], "scope": "known", "complete": True},
        "scan": {"id": "scan-complete", "status": "complete", "mode": "known", "directoriesVisited": 98, "installationsFound": 3, "instancesFound": 3, "currentPath": "", "elapsedMs": 324, "roots": [], "message": "Known-location discovery completed."},
        "roots": [{"path": "D:/", "label": "Modding drive", "kind": "drive"}, {"path": "H:/Saved roots/旅程", "label": "Saved modding folder", "kind": "remembered"}],
        "sessions": [{"id": "session-c", "endpoint": "http://127.0.0.1:54321/", "profileId": "live-profile", "profileName": "Verified running profile", "version": VERSION, "pid": 4321, "installationId": "app-c"}, {"id": "session-loose", "endpoint": "http://127.0.0.1:54322/", "profileId": "other-profile", "profileName": "Unassociated live profile", "version": VERSION}],
        "install": {"id": None, "status": "idle", "total": 0, "completed": 0, "currentId": None, "stopRequested": False, "results": []}, "settings": {"theme": "dark", "locale": "en"}, "error": ""}


class InstancesBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captured_screenshots = {}
        cls.browser_identity = None
        required = os.environ.get("MODHUNTER_REQUIRE_BROWSER") == "1"
        if sync_playwright is None:
            if required:
                raise RuntimeError("Required Instances browser checks need Playwright")
            raise unittest.SkipTest("Playwright unavailable; Instances browser checks not executed")
        cls.runtime = sync_playwright().start()
        options = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        installed = shutil.which("chromium") or shutil.which("chromium-browser")
        if installed:
            options["executable_path"] = installed
        try:
            cls.browser = cls.runtime.chromium.launch(**options)
        except Exception as error:
            cls.runtime.stop()
            if not required and ("Executable doesn't exist" in str(error) or "socket() failed: Operation not permitted" in str(error)):
                raise unittest.SkipTest("Chromium unavailable; Instances browser checks not executed") from error
            raise
        OUTPUT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1020}, reduced_motion="reduce")
        self.page = self.context.new_page()
        type(self).browser_identity = chromium_identity(self.browser, self.page)
        self.page.set_default_timeout(6000)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        csp = json.loads((ROOT / "native/crates/modhunter-desktop/tauri.conf.json").read_text())["app"]["security"]["csp"]
        allowed = {"instances.html": "text/html", "instances.js": "text/javascript", "instances.css": "text/css", "preferences.js": "text/javascript", "preferences.css": "text/css", "locale-catalogs.js": "text/javascript"}
        def route(request):
            name = request.request.url.split("/")[-1]
            if request.request.url.startswith("http://instances.invalid/") and name in allowed:
                request.fulfill(body=(UI / name).read_bytes(), content_type=allowed[name], headers={"Content-Security-Policy": csp})
            else:
                request.abort()
        self.context.route("**/*", route)

    def tearDown(self):
        result = self._outcome.result
        failed = self.errors or any(test is self for test, _ in result.errors + result.failures)
        if failed:
            try:
                diagnostics=self.page.evaluate("() => ({messages:[...document.querySelectorAll('.notice,.form-error')].filter(n=>!n.hidden&&n.getClientRects().length).map(n=>({id:n.id,text:n.textContent})),dialogs:[...document.querySelectorAll('dialog[open]')].map(n=>n.id),calls:window.__INSTANCES_FIX__?.calls,state:window.ModhunterInstances?.snapshot()})")
                print("\nInstances failure: " + json.dumps(diagnostics,ensure_ascii=False)[:12000],flush=True)
                self.screenshot("instances-failed-" + self._testMethodName.removeprefix("test_") + ".png")
            except Exception as error:
                print("Instances diagnostics unavailable: " + str(error), flush=True)
        self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, state=None, *, native=True):
        if native:
            self.context.add_init_script(BOOT.replace("__INSTANCES_INITIAL_STATE__", json.dumps(state or snapshot())))
        self.page.goto("http://instances.invalid/instances.html", wait_until="networkidle")
        if native:
            expect(self.page.locator("#installationCount")).to_have_text(str(len((state or snapshot())["inventory"]["installations"])), timeout=6000)
            self.assertGreaterEqual(self.page.evaluate("ModhunterInstances.snapshot().revision"), 10)
        else:
            expect(self.page.locator("#connectionBanner")).to_be_visible(timeout=6000)
        self.assertTrue(self.page.evaluate("Boolean(window.ModhunterInstances)"))

    def choose(self, *ids):
        for installation_id in ids:
            self.page.locator(f'[data-installation-select="{installation_id}"]').check()

    def review(self):
        self.page.locator("#reviewSelected").click()
        self.page.wait_for_selector("#installPreview[open] .preview-target")

    def deferred_review(self):
        self.page.locator("#reviewSelected").click()
        expect(self.page.locator("#previewLoading")).to_be_visible(timeout=6000)
        self.assertEqual(self.page.evaluate("__INSTANCES_FIX__.pending.length"), 1)

    def calls(self, kind):
        return self.page.evaluate("kind => __INSTANCES_FIX__.calls.filter(call=>call.kind===kind)", kind)

    def screenshot(self, name):
        data = self.page.screenshot(path=str(OUTPUT / name), full_page=False)
        type(self).captured_screenshots[name] = screenshot_identity(data)

    def test_inventory_groups_apps_and_retains_every_profile(self):
        self.open()
        self.assertEqual(self.page.locator(".installation-card").count(), 3)
        self.assertEqual(self.page.locator("[data-installation-select]").count(), 3)
        self.page.locator('[data-installation-id="app-a"] > details > summary').click()
        self.assertEqual(self.page.locator('[data-installation-id="app-a"] .profile-list li').count(), 1207)
        self.assertIn("Shared global instance", self.page.locator('[data-installation-id="app-a"]').inner_text())
        self.assertIn("<script>fixtureOnly()</script>", self.page.locator('[data-installation-id="app-a"]').inner_text())
        self.assertEqual(self.page.locator("#instanceCount").inner_text(), "3")
        self.page.locator("#instanceSearch").fill("Profile 1204")
        expect(self.page.locator(".installation-card")).to_have_count(2, timeout=6000)
        self.assertEqual(self.page.locator('[data-installation-id="app-a"] .profile-list li').count(), 1207)
        self.assertFalse(self.page.locator("#unassociatedSection").is_visible())

    def test_exact_selection_review_and_terminal_receipts(self):
        self.open();self.choose("app-a", "app-b")
        self.page.locator("#instanceSearch").fill("旅程")
        expect(self.page.locator(".installation-card")).to_have_count(1, timeout=6000)
        self.assertIn("outside the current filters", self.page.locator("#selectionHint").inner_text())
        self.review()
        self.assertEqual(self.page.locator(".preview-target").count(), 2)
        self.assertIn("D:/MO2/Aurora/plugins/mo2_update_arsenal", self.page.locator("#previewTargets").inner_text())
        self.screenshot("instances-exact-review.png")
        self.page.locator("#confirmInstall").click()
        expect(self.page.locator('#installResults [data-status="installing"]')).to_have_count(1, timeout=6000)
        self.assertEqual(self.page.evaluate("__INSTANCES_FIX__.state.install.status"), "running")
        self.assertEqual(set(self.calls("install")[0]["value"]), {"token"})
        self.assertEqual(self.page.locator('#installResults [data-status="installed"]').count(), 0)
        self.assertEqual(self.page.locator('#installResults [data-status="installing"]').count(), 1)
        self.page.evaluate("__INSTANCES_FIX__.completeInstall()")
        self.page.wait_for_selector('#installResults [data-status="updated"]')
        self.page.locator("#installResults details summary").click()
        self.assertIn("ModhunterBackups/exact-receipt", self.page.locator("#installResults").inner_text())
        self.assertIn("Existing plugin data preserved.", self.page.locator("#installResults").inner_text())
        self.screenshot("instances-results.png")

    def test_blocked_target_prevents_partial_unreviewed_commit(self):
        self.open();self.page.evaluate("__INSTANCES_FIX__.blocked=['app-b']");self.choose("app-a", "app-b");self.review()
        self.assertTrue(self.page.locator("#confirmInstall").is_disabled())
        self.assertEqual(self.page.locator(".preview-target").count(), 2)
        self.assertIn("Close this MO2", self.page.locator("#previewTargets").inner_text())
        self.assertEqual(self.calls("install"), [])
        self.page.locator("#cancelPreview").click();self.page.locator('[data-installation-select="app-b"]').uncheck();self.review()
        self.assertEqual(self.page.locator(".preview-target").count(), 1)
        self.assertTrue(self.page.locator("#confirmInstall").is_enabled())

    def test_stale_state_and_deferred_preview_never_restore_obsolete_work(self):
        self.open();self.choose("app-a")
        self.page.evaluate("() => {const old=__INSTANCES_FIX__.snapshot();old.revision=9;old.inventory.installations=[];__INSTANCES_FIX__.publishRaw(old);__INSTANCES_FIX__.defer.previewInstall=true}")
        self.assertEqual(self.page.locator(".installation-card").count(), 3)
        self.deferred_review()
        self.page.locator("#cancelPreview").click();self.page.evaluate("__INSTANCES_FIX__.resolve('previewInstall')")
        self.assertEqual(self.page.evaluate("__INSTANCES_FIX__.pending.length"), 0)
        self.assertFalse(self.page.locator("#installPreview").is_visible())
        self.deferred_review()
        self.page.evaluate("() => {__INSTANCES_FIX__.state.inventory.installations[0].executable='D:/Changed/ModOrganizer.exe';__INSTANCES_FIX__.publish();__INSTANCES_FIX__.resolve('previewInstall')}")
        expect(self.page.locator("#previewError")).to_contain_text("changed", timeout=6000)
        self.assertTrue(self.page.locator("#confirmInstall").is_disabled())
        self.assertEqual(self.calls("install"), [])

    def test_scan_cancellation_preserves_prior_and_new_findings(self):
        self.open();self.choose("app-a");self.page.locator("#scanKnown").click()
        self.page.evaluate("() => {const f=__INSTANCES_FIX__;f.state.scan.partialInventory={installations:[{id:'found',directory:'K:/New MO2',executable:'K:/New MO2/ModOrganizer.exe',plugin:{status:'missing'},runningPids:[]}],instances:[],associations:[],issues:[]};f.state.scan.directoriesVisited=200;f.publish()}")
        self.page.wait_for_selector('[data-installation-select="found"]')
        self.assertEqual(self.page.locator(".installation-card").count(), 4)
        self.assertTrue(self.page.locator('[data-installation-select="found"]').is_disabled())
        self.assertTrue(self.page.locator('[data-installation-select="app-a"]').is_checked())
        self.page.locator("#cancelScan").click()
        operation=self.calls("cancelScan")[0]["value"]["operationId"]
        self.assertEqual(operation, self.page.evaluate("__INSTANCES_FIX__.state.scan.id"))
        self.assertIn("Canceling", self.page.locator("#scanHeading").inner_text())
        self.page.evaluate("() => {const f=__INSTANCES_FIX__;f.state.inventory.installations.push(...f.state.scan.partialInventory.installations);f.state.inventory.complete=false;f.state.scan.status='cancelled';f.publish()}")
        expect(self.page.locator('[data-installation-select="found"]')).to_be_enabled(timeout=6000)
        self.choose("found")
        self.assertEqual(self.page.evaluate("ModhunterInstances.selection().sort()"), ["app-a", "found"])
        self.assertIn("Partial inventory", self.page.locator("#inventoryScope").inner_text())

    def test_native_process_refresh_does_not_discard_its_pending_preflight(self):
        state=snapshot();state["inventory"]["installations"][0]["runningPids"]=[3456]
        self.open(state);self.choose("app-a")
        self.page.evaluate("__INSTANCES_FIX__.defer.previewInstall=true")
        self.deferred_review()
        # Native preflight updates process observations and emits its snapshot
        # before resolving the fresh review (e.g. after the user closed MO2).
        self.page.evaluate("() => {const f=__INSTANCES_FIX__;f.state.inventory.installations[0].runningPids=[];f.publish();f.resolve('previewInstall')}")
        self.page.wait_for_selector("#installPreview[open] .preview-target")
        self.assertEqual(len(self.calls("previewInstall")),1)
        self.assertEqual(self.page.locator("#previewError").inner_text(),"")
        self.assertTrue(self.page.locator("#confirmInstall").is_enabled())
        # An observation that changes after the review is displayed still
        # invalidates it; native pre-commit process checks remain independent.
        self.page.evaluate("() => {__INSTANCES_FIX__.state.inventory.installations[0].runningPids=[9876];__INSTANCES_FIX__.publish()}")
        expect(self.page.locator("#previewError")).to_contain_text("changed", timeout=6000)
        self.assertTrue(self.page.locator("#confirmInstall").is_disabled())
        self.assertEqual(self.calls("install"),[])

    def test_stop_waits_for_current_transaction_and_uses_exact_operation(self):
        self.open();self.choose("app-a", "app-b");self.review();self.page.locator("#confirmInstall").click()
        self.page.wait_for_selector('#installResults [data-status="installing"]');self.page.locator("#cancelInstall").click()
        self.assertEqual(self.calls("cancelInstall")[0]["value"], {"operationId": self.page.evaluate("__INSTANCES_FIX__.state.install.id")})
        self.assertEqual(self.page.locator('#installResults [data-status="installing"]').count(), 1)
        self.assertEqual(self.page.locator('#installResults [data-status="queued"]').count(), 1)
        self.assertTrue(self.page.locator("#cancelInstall").is_disabled())
        self.page.evaluate("() => {const f=__INSTANCES_FIX__;f.state.install.status='cancelled';f.state.install.completed=2;f.state.install.currentId=null;f.state.install.results[0].status='updated';f.state.install.results[1].status='cancelled';f.publish()}")
        self.page.wait_for_selector('#installResults [data-status="cancelled"]')
        self.assertEqual(self.page.locator('#installResults [data-status="updated"]').count(), 1)
        self.assertEqual(self.page.locator('#installResults [data-status="installed"]').count(), 0)

    def test_live_actions_and_native_root_choices_keep_exact_authority(self):
        self.open()
        self.page.locator('[data-focus-key="connect:session-c"]').click()
        self.page.locator('[data-focus-key="connect:session-loose"]').click()
        self.assertEqual([call["value"] for call in self.calls("connect")], [{"sessionId": "session-c"}, {"sessionId": "session-loose"}])
        self.page.locator('[data-focus-key="openFolder:app-a"]').click()
        self.page.locator('[data-installation-id="app-a"] > details > summary').click()
        self.page.locator('[data-focus-key="launch-instance:app-a:global"]').click()
        self.assertEqual(self.calls("launch")[0]["value"], {"installationId": "app-a", "instanceId": "global"})
        self.page.locator("#scanDeep").click();self.page.locator('#deepRoots input[value="H:/Saved roots/旅程"]').check();self.page.locator("#startDeepScan").click()
        self.assertEqual(self.calls("scan")[0]["value"], {"mode": "deep", "roots": ["H:/Saved roots/旅程"]})

    def test_themes_four_locales_keyboard_and_narrow_geometry(self):
        self.open()
        self.assertEqual(self.page.evaluate("getComputedStyle(document.body).backgroundColor"), "rgb(0, 0, 0)")
        self.screenshot("instances-oled.png")
        first=self.page.locator('[data-installation-select="app-a"]');first.focus();first.press("Space")
        self.assertTrue(first.is_checked())
        self.page.keyboard.press("Control+f");self.assertEqual(self.page.evaluate("document.activeElement.id"), "instanceSearch")
        original=self.page.locator('[data-installation-id="app-b"] .installation-main > code').text_content()
        for locale,label in [("es","Instancias de MO2"),("it","Istanze MO2"),("zh-TW","MO2 執行個體"),("en","MO2 Instances")]:
            self.page.evaluate("locale => ModhunterI18n.apply({locale,theme:'light'})",locale)
            expect(self.page.locator("h1")).to_have_text(label, timeout=6000)
            self.assertEqual(self.page.locator('[data-installation-id="app-b"] .installation-main > code').text_content(),original)
            self.assertTrue(self.page.locator('[data-installation-select="app-a"]').is_checked())
        self.page.set_viewport_size({"width":1060,"height":850});self.screenshot("instances-light.png")
        self.page.set_viewport_size({"width":390,"height":844})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"))
        self.review();self.assertTrue(self.page.evaluate("document.getElementById('installPreview').getBoundingClientRect().right<=innerWidth"))
        self.assertTrue(self.page.evaluate("document.getElementById('installPreview').scrollWidth<=document.getElementById('installPreview').clientWidth+1"))
        self.screenshot("instances-narrow-review.png")
        self.page.keyboard.press("Escape");self.assertFalse(self.page.locator("#installPreview").is_visible())

    def test_unavailable_native_or_invalid_package_cannot_install(self):
        self.open(native=False)
        self.assertTrue(self.page.locator("#connectionBanner").is_visible());self.assertTrue(self.page.locator("#scanKnown").is_disabled());self.assertTrue(self.page.locator("#reviewSelected").is_disabled())
        state=snapshot();state["package"]={"available":True,"valid":False,"version":VERSION,"message":"The packaged helper hash does not match."};state["error"]="Package verification failed."
        self.open(state);self.choose("app-a")
        self.assertTrue(self.page.locator("#reviewSelected").is_disabled());self.assertIn("hash does not match",self.page.locator("#packageMessage").inner_text());self.assertEqual(self.calls("install"),[])

    def test_interrupted_install_acknowledgement_never_replays_token(self):
        self.open();self.page.evaluate("__INSTANCES_FIX__.failAfterInstall=true");self.choose("app-a");self.review();self.page.locator("#confirmInstall").click()
        expect(self.page.locator("#previewError")).to_contain_text("interrupted", timeout=6000)
        self.assertTrue(self.page.locator("#confirmInstall").is_disabled());self.assertTrue(self.page.locator("#previewAgain").is_disabled())
        self.assertEqual(len(self.calls("install")),1);self.assertEqual(self.page.locator('#installResults [data-status="installing"]').count(),1)
        self.assertEqual(self.page.locator('#installResults [data-status="installed"]').count(),0)

    def test_expired_review_requires_fresh_preflight(self):
        self.open();self.page.evaluate("__INSTANCES_FIX__.previewLifetime=300");self.choose("app-a");self.review()
        expect(self.page.locator("#previewError")).to_contain_text("expired", timeout=6000)
        self.assertTrue(self.page.locator("#confirmInstall").is_disabled());self.assertEqual(self.calls("install"),[])
        self.page.evaluate("__INSTANCES_FIX__.previewLifetime=60000");self.page.locator("#previewAgain").click()
        expect(self.page.locator("#confirmInstall")).to_be_enabled(timeout=6000)
        self.assertEqual(len(self.calls("previewInstall")),2)


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    before = source_identity(ROOT, SOURCES)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(InstancesBrowserTests)
    expected=suite.countTestCases()
    result=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = source_identity(ROOT, SOURCES)
    unchanged = before["commit"] == after["commit"] and before["sourceSha256"] == after["sourceSha256"]
    browser_identity = getattr(InstancesBrowserTests, "browser_identity", None)
    proof={"schema":1,"kind":"instances-browser","ok":result.wasSuccessful() and result.testsRun==expected and not result.skipped and unchanged,"version":VERSION,
           "commit":after["commit"],"commitAtStart":before["commit"],"sourceUnchanged":unchanged,
           "dirty":before["dirty"] or after["dirty"],
           "testsRun":result.testsRun,"requiredTests":expected,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),
           "realChromium":bool(browser_identity and browser_identity["engine"]=="chromium"),"browserIdentity":browser_identity,
           "nativeMO2":False,"nativeWebView2":False,"checks":result.passed_checks,
           "scope":"Actual Chromium with deterministic native-command fixtures; Windows/MO2 transactions have separate native receipts.",
           "sourceSha256":after["sourceSha256"],"sourceSha256AtStart":before["sourceSha256"],
           "screenshots":sorted(getattr(InstancesBrowserTests,"captured_screenshots",{})),
           "screenshotEvidence":getattr(InstancesBrowserTests,"captured_screenshots",{})}
    (OUTPUT/"instances-browser.json").write_text(json.dumps(proof,indent=2)+"\n",encoding="utf-8")
    sys.exit(0 if proof["ok"] else 1)
