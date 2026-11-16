"""Actual Chromium manual-completion paths with a deterministic live bridge.

These checks exercise shipped controls and state persistence. They do not claim
native installation, private-account access, or authority to award ranked XP.
"""
from __future__ import annotations

import json
import unittest

import test_frontend_responsiveness as harness


class CompactCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.FrontendResponsivenessTests.setUpClass()
        cls.browser = harness.FrontendResponsivenessTests.browser
        cls.html = harness.build_page()

    @classmethod
    def tearDownClass(cls):
        harness.FrontendResponsivenessTests.tearDownClass()

    setUp = harness.FrontendResponsivenessTests.setUp
    tearDown = harness.FrontendResponsivenessTests.tearDown
    connected = harness.FrontendResponsivenessTests.connected

    def open(self, *, effects=False):
        fixture = harness.catalog(count=4)
        fixture['executionEpoch'] = 7
        boot = harness.BOOT.replace('__CATALOG__', json.dumps(fixture)).replace('__FAIL__', '0')
        prefs = {'autoCheckOnLaunch': False, 'vfxIntensity': 'subtle' if effects else 'off',
                 'vfxShake': False, 'vfxTrails': False, 'effect': 'shatter', 'sound': False}
        boot = boot.replace('gameScore:0', 'gameScore:540,posts:[],progress:{rows:{},prefs:' + json.dumps(prefs) + '}')
        boot = boot.replace("let result={ok:true};", "if(method==='POST')x.posts.push({path,body});let result={ok:true};")
        original = "else if(path==='progress')result={ok:true,profileId:c.profileId,progress:{rows:{},prefs:{}}};"
        replacement = """else if(path==='progress'){
          if(method==='POST')x.progress={rows:structuredClone(body.rows),prefs:structuredClone(body.prefs)};
          result={ok:true,profileId:c.profileId,progress:structuredClone(x.progress)};
        }"""
        self.assertIn(original, boot)
        boot = boot.replace(original, replacement)
        html = self.html.replace('<!--__MO2_BOOT__-->', boot)
        self.context.route('http://completion.fixture.test/', lambda route: route.fulfill(content_type='text/html', body=html))
        self.page.emulate_media(reduced_motion='no-preference' if effects else 'reduce')
        self.page.goto('http://completion.fixture.test/', wait_until='load')
        self.connected()
        self.page.wait_for_function("__RESP__.counts['GET progress']>=1 && __ARSENAL_QA__.game()?.score===540")
        self.page.wait_for_function("__ARSENAL_QA__.aether()?.options.intensity===__RESP__.progress.prefs.vfxIntensity")
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 4)

    def wait_status(self, value, *, item='item-0'):
        self.page.wait_for_function("([id,status])=>__ARSENAL_QA__.verifyRow(id).state.status===status", arg=[item, value])
        self.page.wait_for_function("([id,status])=>__RESP__.progress.rows[id]?.status===status", arg=[item, value])
        stored = self.page.evaluate("JSON.parse(localStorage.getItem('mo2-update-arsenal:v2:profile-a'))")
        self.assertEqual(stored['rows'][item]['status'], value)

    def assert_manual_authority(self):
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.game().score'), 540)
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.game().updates'), 0)
        posts = self.page.evaluate('__RESP__.posts')
        # Inspector may request a read-only health refresh. Manual completion
        # must never dispatch downloads, installation or game-reward commands.
        self.assertTrue({post['path'] for post in posts}.issubset({'progress', 'mod-health'}), posts)
        self.assertTrue(any(post['path'] == 'progress' for post in posts))
        for post in posts:
            self.assertEqual(post['body']['profileId'], 'profile-a')
            self.assertEqual(post['body']['executionEpoch'], 7)

    def test_compact_keyboard_inspector_and_undo_keep_persisted_manual_state(self):
        self.open()
        self.assertEqual(self.page.locator('#modList .mod-status, #modList .legacy-assessment').count(), 0)
        self.page.locator('#workspaceTitle').click()
        self.page.keyboard.press('j')
        self.assertTrue(self.page.locator('#row-item-0 .inspect-btn').evaluate('e=>e===document.activeElement'))
        self.page.keyboard.press('j')
        self.assertTrue(self.page.locator('#row-item-1 .inspect-btn').evaluate('e=>e===document.activeElement'))
        self.page.keyboard.press('k')
        self.assertTrue(self.page.locator('#row-item-0 .inspect-btn').evaluate('e=>e===document.activeElement'))

        self.page.keyboard.press('Space')
        self.wait_status('done')
        self.assertIn('manual, no ranked XP', self.page.locator('#toastWrap').inner_text())
        self.page.keyboard.press('Space')
        self.wait_status('pending')
        self.page.keyboard.press('Space')
        self.wait_status('done')
        self.page.locator('#toastWrap button').last.click()
        self.wait_status('pending')

        self.page.locator('#row-item-0 .inspect-btn').click()
        self.page.locator('#drawerNote').fill('Keep exact FOMOD choices and runtime evidence')
        self.page.locator('#drawerStatus').select_option('review')
        self.page.locator('#saveInspector').click()
        self.wait_status('review')
        self.assertEqual(self.page.evaluate('__RESP__.progress.rows["item-0"].note'),
                         'Keep exact FOMOD choices and runtime evidence')
        self.page.locator('#row-item-0 .inspect-btn').click()
        self.page.locator('#markInspectorDone').click()
        self.wait_status('done')
        self.assertEqual(self.page.evaluate('__RESP__.progress.rows["item-0"].note'),
                         'Keep exact FOMOD choices and runtime evidence')
        self.page.locator('#row-item-0 .inspect-btn').click()
        self.page.locator('#drawerStatus').select_option('skipped')
        self.page.locator('#saveInspector').click()
        self.wait_status('skipped')

        self.page.locator('#libraryGallery').click()
        self.page.locator('#workspaceTitle').click()
        self.page.keyboard.press('j')
        self.page.keyboard.press('k')
        self.page.keyboard.press('Space')
        self.wait_status('done')
        self.page.locator('#toastWrap button').last.click()
        self.wait_status('skipped')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 4)
        self.assertEqual(self.page.locator('.aether-completion-stage').count(), 0,
                         'Effects disabled/reduced motion must remain respected')
        self.assert_manual_authority()

    def test_real_fx_and_noncompact_host_fallback_remain_available(self):
        self.open(effects=True)
        self.page.locator('#workspaceTitle').click()
        self.page.keyboard.press('j')
        self.page.keyboard.press('Space')
        self.page.wait_for_function("document.querySelectorAll('.aether-completion-stage').length>0")
        self.assertGreater(self.page.locator('.aether-fragment').count(), 0)
        self.wait_status('done')
        self.page.locator('#toastWrap button').last.click()
        self.wait_status('pending')

        # A legacy host can explicitly opt out of compact presentation. Changing
        # layout without replacing the catalog must invalidate cached markup.
        self.page.evaluate("document.body.dataset.layout='classic'")
        self.page.locator('#sortSelect').select_option('name')
        self.assertEqual(self.page.locator('#modList .complete-btn').count(), 4)
        self.assertEqual(self.page.locator('#modList .legacy-assessment').count(), 4)
        self.assertTrue(self.page.locator('#row-item-0 .complete-btn').is_visible())
        self.page.locator('#row-item-0 .complete-btn').click()
        self.wait_status('done')
        self.page.locator('#toastWrap button').last.click()
        self.wait_status('pending')
        self.page.evaluate("document.body.dataset.layout='compact'")
        self.page.locator('#sortSelect').select_option('priority')
        self.assertEqual(self.page.locator('#modList .mod-status, #modList .legacy-assessment').count(), 0)
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 4)
        self.assertTrue(self.page.locator('#row-item-0 .inspect-btn').is_visible())
        self.assert_manual_authority()


if __name__ == '__main__':
    unittest.main()
