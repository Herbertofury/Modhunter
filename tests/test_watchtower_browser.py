"""Actual Chromium acceptance for the shipped Watchtower manager.

The command adapter is a deterministic fixture. This suite does not prove
WebView2 cookie isolation, native persistence, a provider login or live MO2.
The compiled Windows Watchtower fixture owns those separate native checks.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "native/ui"
OUTPUT = Path(os.environ.get("MODHUNTER_QA_OUTPUT", ROOT / "native/target/watchtower-browser"))
BOOT = (ROOT / "tests/fixtures/watchtower_native_fixture.js").read_text("utf-8")
SOURCE_FILES = ("native/ui/watchtower.html", "native/ui/watchtower.css", "native/ui/watchtower.js",
                "tests/fixtures/watchtower_native_fixture.js", "tests/test_watchtower_browser.py")


def watch(index=0, *, changed=False):
    now = int(time.time() * 1000)
    latest = {"id": f"observation-{index}", "hash": f"hash-{index}", "title": f"Release page {index}",
              "finalUrl": f"https://mods.example.com/Release/{index}?File=A", "startedAt": now, "checkedAt": now,
              "textBytes": 34, "textPreview": "Release 2.0\nNew stable compatibility", "previewTruncated": False}
    return {"id": f"watch-{index}", "url": latest["finalUrl"], "title": f"Release page {index}",
            "folder": "Skyrim releases", "tags": ["release", "fixture"], "notes": "Check compatibility before installing",
            "enabled": True, "sessionId": None, "requiresSession": False, "selector": "", "ignoreSelectors": [],
            "intervalMinutes": 60, "association": None, "createdAt": now, "updatedAt": now, "addedAt": now,
            "removedAt": None, "generation": 1, "operationId": 1, "state": "changed" if changed else "baseline",
            "lastAttemptAt": now, "lastSuccessAt": now, "nextCheckAt": now + 3600000, "retryAt": 0,
            "consecutiveErrors": 0, "lastError": "", "latest": latest,
            "seenHash": "previous-hash" if changed else latest["hash"],
            "seenObservationId": "previous-observation" if changed else latest["id"], "seenEventId": None,
            "history": [{"observationId": latest["id"], "hash": latest["hash"], "checkedAt": now,
                         "title": latest["title"], "finalUrl": latest["finalUrl"], "kind": "change" if changed else "baseline",
                         "summary": "Release text changed", "added": ["New stable compatibility"], "removed": ["Earlier release"]}],
            "hasChanges": changed, "needsCheck": False, "muted": False}


def snapshot(count=2):
    return {"revision": 1, "version": "0.10.0", "watches": [watch(i, changed=i == 0) for i in range(count)],
            "sessions": [], "history": [], "readOnly": False, "recoveryWarning": "",
            "work": {"active": [], "queued": []}, "settings": {
                "checkOnLaunch": False, "backgroundChecking": False, "notifications": False,
                "defaultIntervalMinutes": 60, "historyLimit": 20, "mutedOrigins": [], "mutedFolders": [],
                "hideTitles": False, "privacyScreen": False}}


class WatchtowerBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = os.environ.get("MODHUNTER_REQUIRE_BROWSER") == "1"
        if sync_playwright is None:
            if required:
                raise RuntimeError("MODHUNTER_REQUIRE_BROWSER=1 but Playwright is unavailable")
            raise unittest.SkipTest("Playwright unavailable; Watchtower browser checks not executed")
        cls.runtime = sync_playwright().start()
        args = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        installed = shutil.which("chromium") or shutil.which("chromium-browser")
        if installed:
            args["executable_path"] = installed
        try:
            cls.browser = cls.runtime.chromium.launch(**args)
        except Exception as error:
            cls.runtime.stop()
            if required:
                raise
            if "Executable doesn't exist" in str(error) or "socket() failed: Operation not permitted" in str(error):
                raise unittest.SkipTest("Chromium unavailable; Watchtower browser checks not executed") from error
            raise
        OUTPUT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        self.page = self.context.new_page()
        self.page.set_default_timeout(6000)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        allowed = {"watchtower.html": "text/html", "watchtower.js": "text/javascript", "watchtower.css": "text/css"}

        def route(request):
            name = request.request.url.split("/")[-1]
            if request.request.url.startswith("http://watchtower.invalid/") and name in allowed:
                request.fulfill(body=(UI / name).read_bytes(), content_type=allowed[name])
            else:
                request.abort()
        self.context.route("**/*", route)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, state=None, *, native=True):
        if native:
            self.context.add_init_script(BOOT.replace("__WATCHTOWER_INITIAL_STATE__", json.dumps(state or snapshot())))
        self.page.goto("http://watchtower.invalid/watchtower.html", wait_until="networkidle")
        if native:
            self.page.wait_for_function("!document.querySelector('#addPage').disabled")
        return self.page

    def navigate(self, view="pages", filter_name="all"):
        selector = f'#navigation [data-view="{view}"]'
        if view == "pages":
            selector += f'[data-filter="{filter_name}"]'
        self.page.locator(selector).click()

    def calls(self, kind):
        return self.page.evaluate("kind => __WATCH_FIX__.calls.filter(c => c.kind === kind)", kind)

    def screenshot(self, name):
        self.page.screenshot(path=str(OUTPUT / name), full_page=False)

    def test_page_add_and_targeted_check(self):
        self.open(snapshot(0))
        self.page.locator("#addPage").click()
        self.page.locator("#pageUrl").fill("http://mods.example.com/ReleaseCase?File=A")
        self.page.locator("#savePage").click()
        self.assertTrue(self.page.locator("#pageError").is_visible())
        self.assertEqual(self.calls("saveWatch"), [])
        self.page.locator("#pageUrl").fill("mods.example.com/ReleaseCase?File=A#download")
        self.page.locator("#pageTitle").fill("Release with exact path case")
        self.page.locator("#pageFolder").fill("Skyrim releases")
        self.page.locator("#pageTags").fill("release, changelog")
        self.page.locator("#pageNotes").fill("Review the release before installing")
        self.page.locator(".advanced summary").click()
        self.page.locator("#pageSelector").fill("#releases")
        self.page.locator("#pageIgnore").fill(".counter\n.advertisement")
        self.page.locator("#savePage").click()
        self.page.wait_for_function("__WATCH_FIX__.calls.some(c => c.kind === 'check')")
        saved = self.calls("saveWatch")[0]["value"]
        self.assertEqual(saved["url"], "https://mods.example.com/ReleaseCase?File=A")
        self.assertEqual(saved["ignoreSelectors"], [".counter", ".advertisement"])
        self.assertEqual(saved["tags"], ["release", "changelog"])
        self.assertEqual(len(self.calls("check")), 1)
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 1)
        self.assertIn("Baseline saved", self.page.locator("#watchList").inner_text())
        self.assertEqual(self.page.locator("#navChanged").inner_text(), "0")
        self.screenshot("watchtower-pages.png")

    def test_sessions_and_reset(self):
        self.open()
        self.navigate("sessions")
        self.page.locator("#addSession").click()
        self.page.locator("#sessionOrigin").fill("https://mods.example.com/login")
        self.page.locator("#sessionLabel").fill("Personal fixture session")
        self.page.locator("#sessionRemember").uncheck()
        self.page.locator("#saveSession").click()
        self.page.wait_for_function("__WATCH_FIX__.calls.some(c => c.kind === 'openSession')")
        self.assertEqual(self.calls("saveSession")[0]["value"]["origin"], "https://mods.example.com")
        self.assertFalse(self.calls("saveSession")[0]["value"]["remember"])
        self.assertIn("Temporary for this app run", self.page.locator("#sessionList").inner_text())
        self.page.locator('[data-action="session-confirm"]').click()
        self.page.locator("#confirmProceed").click()
        self.page.wait_for_function("__WATCH_FIX__.state.sessions[0].state === 'confirmed'")
        self.page.locator('[data-action="session-reset"]').click()
        self.assertEqual(self.calls("resetSession"), [])
        self.page.locator("#confirmProceed").click()
        self.page.wait_for_function("__WATCH_FIX__.state.sessions[0].storageGeneration === 2")
        self.assertIn("Fresh session", self.page.locator("#sessionList").inner_text())
        self.assertEqual(self.page.locator('input[type="password"]').count(), 0)
        self.screenshot("watchtower-sessions.png")
        self.navigate()
        self.page.locator("#addPage").click()
        self.page.locator("#pageUrl").fill("https://mods.example.com/account-page")
        session_id = self.page.evaluate("__WATCH_FIX__.state.sessions[0].id")
        self.page.locator("#pageSession").select_option(session_id)
        self.page.locator("#savePage").click()
        self.page.wait_for_function("__WATCH_FIX__.calls.some(c => c.kind === 'check')")
        account_id = self.calls("saveWatch")[-1]["value"]["sessionId"]
        self.assertEqual(account_id, session_id)
        self.assertIn("Baseline saved", self.page.locator("#watchList").inner_text())
        self.assertNotIn("Choose a session", self.page.locator("#watchList").inner_text())
        # A previous session-bound form must not leak its selection into a new
        # public watch. Changing the site must also clear an incompatible choice.
        self.page.locator("#addPage").click()
        self.page.locator("#pageUrl").fill("https://mods.example.com/public-page")
        self.assertEqual(self.page.locator("#pageSession").input_value(), "")
        self.page.locator("#pageSession").select_option(session_id)
        self.page.locator("#pageUrl").fill("https://other.example.org/release")
        self.assertTrue(self.page.locator('#pageSession option:not([value=""])').is_disabled())
        self.assertEqual(self.page.locator("#pageSession").input_value(), "")

    def test_observation_acknowledgment_and_restore(self):
        self.open()
        self.navigate(filter_name="changed")
        self.page.locator('[data-watch-id="watch-0"] [data-action="seen"]').click()
        self.assertEqual(self.calls("markSeen")[0]["value"], {"id": "watch-0", "observationId": "observation-0"})
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 0)
        self.navigate("history")
        self.page.locator('[data-action="undo-seen"]').click()
        self.assertIn("Acknowledgement undone", self.page.locator("#historyList").inner_text())
        self.navigate(filter_name="changed")
        self.page.locator('[data-watch-id="watch-0"] [data-action="detail"]').first.click()
        self.page.wait_for_selector(".capture-preview")
        self.assertIn("Release 2.0", self.page.locator(".capture-preview").inner_text())
        self.assertEqual(len(self.calls("detail")), 1)
        self.screenshot("watchtower-details.png")
        self.page.locator('#detailBody [data-action="remove"]').click()
        self.navigate(filter_name="removed")
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 1)
        self.page.locator('[data-action="restore"]').click()
        self.navigate(filter_name="changed")
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 1)
        self.assertEqual(len(self.page.evaluate("__WATCH_FIX__.state.watches[0].history")), 1)
        self.page.locator('[data-select="watch-0"]').check()
        self.page.evaluate("__WATCH_FIX__.change(s => {s.watches[0].latest.id = 'unseen-newer-observation'; s.watches[0].latest.hash = 'newer-hash'})")
        self.page.locator('[data-bulk="seen"]').click()
        self.page.wait_for_function("__WATCH_FIX__.calls.filter(c => c.kind === 'markSeen').length === 2")
        self.assertEqual(self.calls("markSeen")[-1]["value"], {"observations": [{"id": "watch-0", "observationId": "observation-0"}]})
        self.assertNotEqual(self.page.evaluate("__WATCH_FIX__.state.watches[0].seenObservationId"), "unseen-newer-observation")
        self.assertTrue(self.page.locator("#selectionBar").is_visible())

    def test_import_preview_and_export(self):
        self.open(snapshot(0))
        self.page.locator("#importOpen").click()
        self.page.locator("#importText").fill("http://localhost/private")
        self.page.locator("#previewImport").click()
        self.page.wait_for_selector("#importPreview:not([hidden])")
        self.assertTrue(self.page.locator("#applyImport").is_disabled())
        self.assertIn("invalid", self.page.locator("#importPreview").inner_text())
        self.page.locator("#importText").fill("https://mods.example.com/import\nhttps://mods.example.com/import")
        self.page.locator("#previewImport").click()
        self.page.wait_for_function("!document.querySelector('#applyImport').disabled")
        self.assertEqual(self.page.evaluate("__WATCH_FIX__.state.watches.length"), 0)
        self.assertIn("1 already tracked", self.page.locator("#importPreview").inner_text())
        self.page.locator("#importText").fill("https://mods.example.com/revised")
        self.assertTrue(self.page.locator("#applyImport").is_disabled())
        self.page.evaluate("__WATCH_FIX__.defer.import = true")
        self.page.locator("#previewImport").click()
        self.page.wait_for_function("__WATCH_FIX__.pending.some(p => p.kind === 'import')")
        self.page.locator("#importText").fill("https://mods.example.com/latest-draft")
        self.page.evaluate("__WATCH_FIX__.release('import')")
        self.page.wait_for_function("__WATCH_FIX__.active.import === 0")
        self.assertTrue(self.page.locator("#applyImport").is_disabled())
        self.assertTrue(self.page.locator("#importPreview").is_hidden())
        self.page.locator("#previewImport").click()
        self.page.locator("#applyImport").click()
        self.page.wait_for_function("__WATCH_FIX__.state.watches.length === 1")
        applied = [c["value"] for c in self.calls("import") if c["value"]["preview"] is False]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["document"]["watches"][0]["url"], "https://mods.example.com/latest-draft")
        self.assertEqual(applied[0]["document"]["kind"], "modhunter-watchtower")
        self.assertEqual(applied[0]["document"]["schemaVersion"], 1)
        self.assertIsInstance(applied[0]["previewRevision"], int)
        self.page.locator("#exportOpen").click()
        self.page.locator("#saveExport").click()
        self.page.wait_for_function("!document.querySelector('#exportDialog').open")
        self.assertEqual(self.calls("exportFile")[0]["value"], {"format": "json", "includeHistory": False})
        self.page.locator("#exportOpen").click()
        self.page.locator("#exportHistory").check()
        self.page.locator("#saveExport").click()
        self.page.wait_for_function("!document.querySelector('#exportDialog').open")
        self.assertEqual(self.calls("exportFile")[1]["value"], {"format": "json", "includeHistory": True})
        self.page.locator("#exportOpen").click()
        self.page.locator("#exportHistory").check()
        self.page.locator("#exportFormat").select_option("csv")
        self.assertTrue(self.page.locator("#exportHistory").is_disabled())
        self.assertFalse(self.page.locator("#exportHistory").is_checked())
        self.page.locator("#saveExport").click()
        self.page.wait_for_function("!document.querySelector('#exportDialog').open")
        self.assertEqual(self.calls("exportFile")[2]["value"], {"format": "csv", "includeHistory": False})

    def test_preferences_and_mutes(self):
        self.open()
        self.navigate("settings")
        self.page.locator("#backgroundChecking").check()
        self.page.locator("#checkOnLaunch").check()
        self.page.locator("#mutedOrigins").fill("mods.example.com")
        self.page.locator("#mutedFolders").fill("Paused sources")
        self.page.locator("#historyLimit").select_option("50")
        # An asynchronous state event must not erase the draft preferences.
        self.page.evaluate("__WATCH_FIX__.change(s => {s.watches[0].lastError = 'Fixture status changed'})")
        self.assertEqual(self.page.locator("#mutedOrigins").input_value(), "mods.example.com")
        self.assertTrue(self.page.locator("#backgroundChecking").is_checked())
        self.page.evaluate("__WATCH_FIX__.defer.settings = true")
        self.page.locator("#saveSettings").click()
        self.page.wait_for_function("__WATCH_FIX__.pending.some(p => p.kind === 'settings')")
        self.page.locator("#historyLimit").select_option("10")
        self.page.evaluate("__WATCH_FIX__.release('settings')")
        self.page.wait_for_function("__WATCH_FIX__.active.settings === 0")
        self.assertEqual(self.page.locator("#historyLimit").input_value(), "10")
        self.assertEqual(self.page.evaluate("__WATCH_FIX__.state.settings.historyLimit"), 50)
        self.page.locator("#saveSettings").click()
        self.page.wait_for_function("__WATCH_FIX__.state.settings.historyLimit === 10")
        self.page.wait_for_function("__WATCH_FIX__.state.settings.backgroundChecking")
        payload = self.calls("settings")[0]["value"]
        self.assertEqual(payload["mutedOrigins"], ["https://mods.example.com"])
        self.assertEqual(payload["historyLimit"], 50)
        self.navigate(filter_name="paused")
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 2)
        self.assertTrue(self.page.locator("#checkAll").is_disabled())
        self.assertEqual(self.page.locator('#watchList [data-action="check"]:disabled').count(), 2)

    def test_native_error_recovery(self):
        self.open(snapshot(0))
        self.page.evaluate("__WATCH_FIX__.failNext.saveWatch = 'The local store could not be saved'")
        self.page.locator("#addPage").click()
        self.page.locator("#pageUrl").fill("https://mods.example.com/retry")
        self.page.locator("#pageTitle").fill("Keep this unsaved draft")
        self.page.locator("#checkAfterSave").uncheck()
        self.page.locator("#savePage").click()
        self.page.wait_for_selector("#pageError:not([hidden])")
        self.assertTrue(self.page.locator("#pageDialog").is_visible())
        self.assertEqual(self.page.locator("#pageTitle").input_value(), "Keep this unsaved draft")
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 0)
        self.page.locator("#savePage").click()
        self.page.wait_for_function("!document.querySelector('#pageDialog').open")
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 1)
        self.assertEqual(self.calls("check"), [])
        self.page.locator("#addPage").click()
        self.page.locator("#pageUrl").fill("https://mods.example.com/late-first")
        self.page.locator("#pageTitle").fill("First queued save")
        self.page.locator("#checkAfterSave").uncheck()
        self.page.evaluate("__WATCH_FIX__.defer.saveWatch = true")
        self.page.locator("#savePage").click()
        self.page.wait_for_function("__WATCH_FIX__.pending.some(p => p.kind === 'saveWatch')")
        self.page.locator('#pageDialog [data-close-dialog="pageDialog"]').last.click()
        self.page.locator("#addPage").click()
        self.page.locator("#pageUrl").fill("https://mods.example.com/newer-draft")
        self.page.locator("#pageTitle").fill("Keep the newer unsaved draft")
        self.page.locator("#checkAfterSave").uncheck()
        self.assertTrue(self.page.locator("#savePage").is_disabled())
        self.page.evaluate("__WATCH_FIX__.release('saveWatch')")
        self.page.wait_for_function("__WATCH_FIX__.active.saveWatch === 0 && !document.querySelector('#savePage').disabled")
        self.assertTrue(self.page.locator("#pageDialog").is_visible())
        self.assertEqual(self.page.locator("#pageUrl").input_value(), "https://mods.example.com/newer-draft")
        self.assertEqual(self.page.locator("#pageTitle").input_value(), "Keep the newer unsaved draft")
        self.assertEqual(self.page.evaluate("__WATCH_FIX__.state.watches.filter(w => w.url.endsWith('/late-first')).length"), 1)
        self.assertEqual(self.page.evaluate("__WATCH_FIX__.state.watches.filter(w => w.url.endsWith('/newer-draft')).length"), 0)
        self.assertEqual(len(self.calls("saveWatch")), 3)
        self.assertEqual(self.calls("check"), [])
        self.page.locator('#pageDialog [data-close-dialog="pageDialog"]').last.click()
        stopped_id = self.page.evaluate("__WATCH_FIX__.state.watches[0].id")
        self.page.evaluate("""__WATCH_FIX__.change(s => {
          s.watches[0].state = 'checking';
          s.work = {active: [], queued: []};
          s.runtimeWarning = 'Fixture result could not be saved; clear the stopped check after storage recovers.';
        })""")
        self.assertIn("check stopped", self.page.locator("#runStatus").inner_text())
        self.assertFalse(self.page.locator("#runDot").evaluate("e => e.classList.contains('busy')"))
        self.assertEqual(self.page.locator("#cancelAll").inner_text(), "Clear stopped checks")
        self.assertTrue(self.page.locator("#runtimeBanner").is_visible())
        self.navigate(filter_name="attention")
        stopped_row = self.page.locator(f'[data-watch-id="{stopped_id}"]')
        self.assertIn("Check stopped", stopped_row.inner_text())
        self.assertEqual(stopped_row.locator('[data-action="cancel"]').inner_text(), "Clear stopped check")
        stopped_row.locator('[data-action="cancel"]').click()
        self.page.wait_for_function("__WATCH_FIX__.state.watches[0].state === 'cancelled'")
        self.assertEqual(self.calls("cancel")[-1]["value"], {"id": stopped_id})
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 0)
        self.assertTrue(self.page.locator("#cancelAll").is_hidden())

    def test_stale_state_and_focus(self):
        self.open()
        self.page.evaluate("""() => {
          window.unchangedRow = document.querySelector('[data-watch-id="watch-1"]');
          window.focusedControl = unchangedRow.querySelector('[data-action="check"]');
          focusedControl.focus();
          __WATCH_FIX__.defer.state = true;
          window.dispatchEvent(new Event('focus'));
        }""")
        self.page.wait_for_function("__WATCH_FIX__.pending.some(p => p.kind === 'state')")
        self.page.evaluate("__WATCH_FIX__.change(s => {s.watches[0].title = 'Newer accepted title'; s.watches[0].generation++})")
        self.assertTrue(self.page.evaluate("unchangedRow === document.querySelector('[data-watch-id=\"watch-1\"]')"))
        self.assertTrue(self.page.evaluate("document.activeElement === focusedControl"))
        self.page.evaluate("__WATCH_FIX__.release('state')")
        self.page.wait_for_function("__WATCH_FIX__.active.state === 0")
        self.assertIn("Newer accepted title", self.page.locator('[data-watch-id="watch-0"]').inner_text())
        self.assertEqual(self.page.evaluate("__WATCH_FIX__.max.state"), 1)

    def test_renderer_boundary(self):
        self.open(native=False)
        self.assertTrue(self.page.locator("#connectionBanner").is_visible())
        self.assertIn("Rust desktop", self.page.locator("#connectionBanner").inner_text())
        self.assertTrue(self.page.locator("#addPage").is_disabled())
        self.assertTrue(self.page.locator("#checkAll").is_disabled())
        self.assertTrue(self.page.locator("#exportOpen").is_disabled())

    def test_private_text_and_untrusted_content(self):
        state = snapshot(1)
        dangerous = '<img src=x onerror="window.fixtureInjected=true">'
        state["watches"][0].update(title=dangerous, notes="Sensitive fixture note", lastError=dangerous)
        state["watches"][0]["latest"]["textPreview"] = dangerous
        self.open(state)
        self.assertEqual(self.page.locator("#watchList img").count(), 0)
        self.assertFalse(self.page.evaluate("Boolean(window.fixtureInjected)"))
        self.navigate("settings")
        self.page.locator("#hideTitles").check()
        self.page.locator("#saveSettings").click()
        self.navigate()
        self.assertEqual(self.page.locator(".page-name").inner_text(), "mods.example.com")
        self.assertNotIn("Sensitive fixture note", self.page.locator("#watchList").inner_text())
        self.page.locator('.page-name').click()
        self.page.wait_for_function("document.querySelector('#detailBody').textContent.includes('Page content is hidden')")
        self.assertEqual(self.page.locator(".capture-preview").count(), 0)

    def test_responsive_complete_list(self):
        self.open(snapshot(2500))
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 2500)
        self.page.locator("#search").fill("Release page 2499")
        self.page.wait_for_function("document.querySelectorAll('#watchList .watch-row').length === 1")
        self.assertEqual(self.page.locator("#watchList .watch-row").get_attribute("data-watch-id"), "watch-2499")
        self.page.locator("#search").fill("")
        self.page.wait_for_function("document.querySelectorAll('#watchList .watch-row').length === 2500")
        self.page.locator("#selectAll").check()
        self.page.locator('[data-bulk="pause"]').click()
        self.page.wait_for_function("__WATCH_FIX__.state.watches.every(w => !w.enabled)")
        pause_calls = self.calls("pauseWatch")
        self.assertEqual(len(pause_calls), 1)
        self.assertEqual(len(pause_calls[0]["value"]["ids"]), 2500)
        self.assertEqual(len(set(pause_calls[0]["value"]["ids"])), 2500)
        self.assertFalse(pause_calls[0]["value"]["enabled"])
        self.assertEqual(self.page.locator("#watchList .watch-row").count(), 2500)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 391)
        self.assertTrue(self.page.locator("#addPage").is_visible())
        self.assertTrue(self.page.locator("#checkAll").is_visible())
        self.screenshot("watchtower-mobile.png")
        self.page.locator("#addPage").click()
        self.assertLessEqual(self.page.locator("#pageDialog").bounding_box()["width"], 390)
        self.page.locator("#pageUrl").fill("https://mods.example.com/mobile")
        self.page.locator("#checkAfterSave").uncheck()
        self.page.locator("#savePage").click()
        self.page.wait_for_function("document.querySelectorAll('#watchList .watch-row').length === 2501")


class ProofResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.checks = []

    def record(self, test, passed):
        self.checks.append({"name": test.id().split(".")[-1].removeprefix("test_"), "passed": passed})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, True)

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.record(test, False)

    def addError(self, test, error):
        super().addError(test, error)
        self.record(test, False)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, False)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WatchtowerBrowserTests)
    result = unittest.TextTestRunner(verbosity=2, resultclass=ProofResult).run(suite)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", *SOURCE_FILES], cwd=ROOT, text=True).strip())
    proof = {"ok": result.wasSuccessful() and result.testsRun == 10 and not result.skipped,
             "realChromium": result.testsRun > 0 and sync_playwright is not None,
             "watchtowerSchema": 1, "candidateCommit": commit, "sourceDirty": dirty,
             "sourceFiles": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
             "scope": "Actual Chromium with native-command fixtures; no WebView2 or real account proof",
             "checks": result.checks, "screenshots": sorted(p.name for p in OUTPUT.glob("watchtower-*.png"))}
    (OUTPUT / "watchtower-browser.json").write_text(json.dumps(proof, indent=2) + "\n", "utf-8")
    sys.exit(0 if proof["ok"] else 1)
