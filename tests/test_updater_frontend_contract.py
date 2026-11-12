"""Updater UI contract: primary live actions must be present and operable.

The native Windows MO2 UI remains a separate acceptance gate. These
cheap source checks catch disabled-by-omission wiring regressions.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "mo2_update_arsenal/web/app.js").read_text(encoding="utf-8")
PAGE = (ROOT / "mo2_update_arsenal/web/app.template.html").read_text(encoding="utf-8")


class UpdaterFrontendContractTests(unittest.TestCase):
    def test_live_check_all_action_is_rendered_enabled_and_wired(self):
        self.assertIn('id="liveAll" disabled', PAGE)
        self.assertIn(
            "['liveAll','liveQuick','liveDeep','liveLocal','liveRefresh','liveRuntime','liveConnect','liveBackup']",
            APP,
        )
        self.assertIn("$('liveAll').onclick=()=>scanLive('all')", APP)
        self.assertIn("if(v.scan.state==='completed'||v.scan.state==='partial')", APP)

    def test_workshop_uses_same_authenticated_batch_dispatch_for_nexus_and_github(self):
        self.assertIn("async function queueWorkshop()", APP)
        self.assertIn("bridgeRequest('POST','update-queue'", APP)
        self.assertIn('data-source-kind="github"', APP)
        self.assertIn('id="workshopResults"', PAGE)
        self.assertIn('id="workshopQueue"', PAGE)

    def test_step_by_step_command_center_has_real_action_handlers(self):
        for element in ('workshopJourney', 'workshopNextTitle', 'workshopNextHint',
                        'workshopNextAction', 'workshopRefreshDownloads'):
            with self.subTest(element=element):
                self.assertIn(f'id="{element}"', PAGE)
        self.assertIn('function renderWorkshopJourney()', APP)
        self.assertIn("$('workshopNextAction').onclick", APP)
        self.assertIn("if(action==='install'||action==='downloads')", APP)
        self.assertIn("if(action==='queue')return queueWorkshop()", APP)
        self.assertIn("$('workshopRefreshDownloads').onclick", APP)
        self.assertIn("if(signature===WORKSHOP_TICKET_SIGNATURE)return", APP)

    def test_github_and_nexus_files_can_both_appear_in_the_same_picker(self):
        self.assertIn("const github=assets.length?", APP)
        self.assertIn("if(!m.nexusId){$('filePickerFiles').innerHTML=github;return}", APP)
        self.assertIn("'<div class=\"picker-source-title\">NEXUS MODS", APP)
        self.assertIn("'<div class=\"picker-source-title\">GITHUB RELEASE", APP)
        self.assertIn("EXACT FILES</div>'+nexus:'')+github", APP)
        self.assertIn("targetVersion:record.latestVersion", APP)
        self.assertIn("String(m.latestVersion||'')===String(choice.targetVersion||'')", APP)
        self.assertIn("data-retry-item-id", APP)

    def test_desktop_can_import_a_user_selected_mo2_profile_without_plugin(self):
        root = ROOT / "desktop"
        main = (root / "main.cjs").read_text(encoding="utf-8")
        bar = (root / "chrome.html").read_text(encoding="utf-8")
        chrome = (root / "chrome.js").read_text(encoding="utf-8")
        self.assertIn('id="importMo2"', bar)
        self.assertIn("if(kind==='importMo2')return importProfile()", main)
        self.assertIn("window.modhunterImportCatalog", APP)
        self.assertIn("offline_mo2.cjs", main)
        self.assertIn("invoke('importMo2')", chrome)

    def test_packaged_dashboard_is_built_from_original_source_not_a_mockup(self):
        self.assertIn("/*__SCRIPT__*/", PAGE)
        self.assertIn("/*__CATALOG__*/", PAGE)
        self.assertIn("/*__STYLE__*/", PAGE)


if __name__ == "__main__":
    unittest.main()
