"""All-in-one Modhunter continuity: a single product and zero-loss entry points."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/'mo2_update_arsenal/web/app.template.html').read_text('utf-8')
JS=(ROOT/'mo2_update_arsenal/web/app.js').read_text('utf-8')
BUILD=(ROOT/'build.py').read_text('utf-8')
RELEASE=(ROOT/'release_v08.py').read_text('utf-8')


class UnifiedProductTests(unittest.TestCase):
    def test_primary_updater_and_all_recovered_modes_in_one_page(self):
        for label in (
            'id="unifiedCommand"', 'id="unifiedScan"', 'id="unifiedReview"',
            'id="updateWorkshop"', 'id="liveAll"', 'id="gameBoard"',
            'id="fxSettingsToggle"', '?realm=1', '?realm=1&amp;siege=1',
        ):
            with self.subTest(label=label):
                self.assertIn(label,PAGE)
        for feature in ('Questbound','AetherFX','Shiverfell','Mythforge','Siege','Atlas'):
            with self.subTest(feature=feature):
                self.assertIn(feature.lower(),PAGE.lower())
        self.assertLess(PAGE.index('id="unifiedCommand"'),PAGE.index('id="gameBoard"'))
        self.assertLess(PAGE.index('id="liveHub"'),PAGE.index('id="gameBoard"'))

    def test_combined_actions_are_live_and_safe_in_offline_mode(self):
        self.assertIn("function refreshUnifiedCommand()",JS)
        self.assertIn("$('unifiedScan').onclick",JS)
        self.assertIn("if(BRIDGE.active&&LIVE_CATALOG)scanLive('all')",JS)
        self.assertIn("$('unifiedReview').onclick",JS)
        self.assertIn("$('unifiedQuest').onclick",JS)
        self.assertIn("$('unifiedFX').onclick",JS)
        self.assertIn("refreshUnifiedCommand();",JS)
        self.assertIn("DATA?.offline",JS)

    def test_three_editions_are_real_shared_bundle_not_different_mockups(self):
        for marker in ('/*__SCRIPT__*/','/*__AETHER_SCRIPT__*/',
                       '/*__REALM_SCRIPT__*/','/*__SIEGE_SCRIPT__*/'):
            self.assertIn(marker,PAGE)
            self.assertIn(marker,BUILD)
        for required in (
            'Standalone:','Questbound','AetherFX','Shiverfell',
            'Mythforge','Modhunter Siege','Atlas',
            "Desktop/Run_ModHunter.bat","MO2/Install_to_MO2.bat",
            "MO2/mo2_update_arsenal/web/index.html",
            "Docs/Unified-Modhunter.md",
            "Docs/Original-Releases-and-Output-Audit.md",
        ):
            with self.subTest(required=required):
                self.assertIn(required,RELEASE)
        self.assertIn("z.read(name)==html.read_bytes()",RELEASE)

    def test_all_in_one_desktop_uses_embedded_html_without_mo2_plugin(self):
        desktop=(ROOT/'desktop/main.cjs').read_text('utf-8')
        self.assertIn("path.join(__dirname,'Modhunter.html')",desktop)
        self.assertIn("fs.existsSync(nearbyHTML)?nearbyHTML:sourceHTML",desktop)
        self.assertIn("Desktop/Modhunter.html",RELEASE)
        self.assertIn("MO2/mo2_update_arsenal/web/index.html",RELEASE)

    def test_no_false_native_windows_compatibility_claims(self):
        self.assertIn("'nativeWindowsElectron':'unverified'",RELEASE)
        self.assertIn("'nativeWindowsMO2':'unverified'",RELEASE)
        self.assertIn("2.5.3 is a target",RELEASE)


if __name__=='__main__':
    unittest.main()
