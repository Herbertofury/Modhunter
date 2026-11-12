"""Code-level UI contract: ensure premium overrides never sacrifice full access."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "mo2_update_arsenal" / "web"


class AtelierQualityTests(unittest.TestCase):
    def test_atelier_is_final_style_layer(self):
        source = (ROOT / "build.py").read_text("utf-8")
        self.assertIn("(WEB/'atelier.css').read_text", source)
        self.assertLess(source.index("(WEB/'siege.css').read_text"), source.index("(WEB/'atelier.css').read_text"))
        layer = (WEB / "atelier.css").read_text("utf-8")
        for selector in (".hero", ".unified-grid", ".update-workshop", ".workshop-item",
                         ".workshop-results", ".mod-row", '.workshop-summary',
                         'body[data-theme="snow"]', 'prefers-reduced-motion'):
            self.assertIn(selector, layer)
        for prohibited in ("content-visibility:auto", "virtual-scroll", "max-height:600px"):
            self.assertNotIn(prohibited, layer)

    def test_accessible_operational_landmarks_and_copy(self):
        document = (WEB / "app.template.html").read_text("utf-8")
        for id_ in ("updateWorkshop", "workshopResults", "workshopQueue", "unifiedScan",
                    "unifiedReview", "liveProfile", "liveScanProgress", "workbench"):
            self.assertIn('id="' + id_ + '"', document)
        self.assertIn('class="skip-link" href="#updateWorkshop"', document)
        self.assertIn('Nothing changes without your approval.', document)
        self.assertIn('The Update <em>Workshop</em>', document)
        self.assertNotIn("GLORIOUSLY CLEARED", document)

    def test_desktop_tabs_have_two_accessible_controls(self):
        source = (ROOT / "desktop" / "chrome.js").read_text("utf-8")
        self.assertIn("document.createElement('div');tab.className='tab'", source)
        self.assertIn("open.setAttribute('aria-label','Open '+t.title)", source)
        self.assertIn("close.setAttribute('aria-label','Close '+t.title)", source)
        self.assertNotIn("x.setAttribute('role','button')", source)
        css = (ROOT / "desktop" / "chrome.css").read_text("utf-8")
        self.assertIn("overflow-x:auto", css)
        self.assertIn("max-width:none", css)

    def test_design_contract_is_discoverable(self):
        doc = (ROOT / "wiki" / "Design-System-and-Quality-Bar.md").read_text("utf-8")
        self.assertIn("No cropped/mod-count-limited lists", doc)
        self.assertIn("Light and dark themes", doc)
        self.assertIn("native Windows", doc)
        for file in ("README.md", "wiki/Home.md", "wiki/_Sidebar.md"):
            self.assertIn("Design-System-and-Quality-Bar", (ROOT / file).read_text("utf-8"))


    def test_chromium_qa_breakpoints_are_guarded(self):
        layer = (WEB / "atelier.css").read_text("utf-8")
        self.assertIn('body[data-theme="night"] .hero,body[data-theme="snow"] .hero', layer)
        self.assertIn("body .topbar .top-actions { flex:1 1 560px", layer)
        self.assertIn('body .hero .hero-main { display:block; width:100%', layer)

if __name__ == "__main__":
    unittest.main()
