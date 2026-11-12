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

    def test_packaged_dashboard_is_built_from_original_source_not_a_mockup(self):
        self.assertIn("/*__SCRIPT__*/", PAGE)
        self.assertIn("/*__CATALOG__*/", PAGE)
        self.assertIn("/*__STYLE__*/", PAGE)


if __name__ == "__main__":
    unittest.main()
