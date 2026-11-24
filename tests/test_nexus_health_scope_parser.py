"""Nexus public health evidence keeps exact DOM scopes after incremental parsing."""
import unittest

from mo2_update_arsenal.mod_health import _NexusMarkup, parse_nexus_page
from test_mod_health import GAME, MID, NOW, URL, markup


def parse(body):
    return parse_nexus_page(body, game=GAME, mod_id=MID, checked_at=NOW)


class NexusScopeParserTest(unittest.TestCase):
    def test_balanced_ordinary_html_is_parsed_without_ancestry_walks(self):
        class CountingStack(list):
            walks = 0

            def __reversed__(self):
                self.walks += 1
                return super().__reversed__()

            def __iter__(self):
                self.walks += 1
                return super().__iter__()

        body = "<title>Fixture page</title>" + "<section><div><p>ordinary &amp; text</p></div></section>" * 48
        parser = _NexusMarkup(GAME, MID, URL)
        stack = parser.stack = CountingStack()
        parser.feed(body)
        parser.close()
        self.assertEqual(parser.nodes, 145)
        self.assertEqual(parser.title, "Fixture page")
        self.assertFalse(stack)
        self.assertEqual(stack.walks, 0, "Balanced ordinary tags still traversed uncaptured ancestry")

    def test_mismatched_close_and_unrelated_alert_cannot_inherit_bug_scope(self):
        valid = markup(count="3")
        # Mis-nested </div> removes the previously open scoped section. A
        # later outsider alert must remain mere page content.
        scoped = (f'<div id="tab-modbugs"><section><h2 id="bugs_tab_h2" data-mod-id="{MID}">'
                  'Bug reports</h2></div><p role="alert">Bug reports are locked for this mod.</p>')
        prefix = valid.split('<div id="tab-modbugs">', 1)[0]
        result = parse(prefix + scoped + '</body></html>')
        self.assertEqual(result["fetch"]["state"], "ok")
        self.assertEqual(result["bugs"]["state"], "available")
        self.assertEqual(result["bugs"]["displayedCount"], 3)

    def test_nested_overrides_restore_nearest_ancestor_and_ignored_text(self):
        root = f'https://www.nexusmods.com/{GAME}/mods/{MID}'
        body = (f'<title>fixture</title><link rel="canonical" href="{root}">'
                '<section id="mod-page-tab-bugs" aria-hidden="true">'
                f'<div id="mod-page-tab-description"><a href="{root}?tab=description">Description</a></div>'
                f'<a href="{root}?tab=bugs"><span>Bugs</span><script>bug reports are hidden</script>'
                '<span class="alert"><strong>2</strong></span></a></section>')
        result = parse(body)
        self.assertEqual(result["page"]["status"], "available")
        self.assertEqual(result["bugs"]["state"], "hidden")
        self.assertIsNone(result["bugs"]["displayedCount"])

    def test_unclosed_count_nodes_and_self_closes_keep_observed_badge(self):
        root = f'https://www.nexusmods.com/{GAME}/mods/{MID}'
        body = (f'<title>fixture</title><link rel="canonical" href="{root}">'
                f'<ul><li id="mod-page-tab-bugs"><a href="{root}?tab=bugs">Bugs'
                '<span class="alert"><em>4<small /></li></ul>'
                '<p role="alert">The bugs section is locked.</p>')
        result = parse(body)
        self.assertEqual(result["bugs"]["state"], "available")
        self.assertEqual(result["bugs"]["displayedCount"], 4)


if __name__ == "__main__":
    unittest.main()
