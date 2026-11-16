"""Real Chromium controls for the image-forward library; deterministic API only.

No external account or user profile is involved. Native Windows/MO2 receipts
remain separate. All thumbnail requests are intercepted with known fixture bytes.
"""
from __future__ import annotations

import base64
import json
import unittest

import test_frontend_responsiveness as harness

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=')


class GalleryWorkspaceTests(unittest.TestCase):
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

    def open(self, count=9, group_count=1):
        catalog = harness.catalog(count=count)
        if group_count > 1:
            catalog['groups'] = [{'id':f'group-{group}', 'name':f'Fixture chapter {group}', 'startPriority':group,
                                  'count':len(range(group,count,group_count)), 'color':'mint'} for group in range(group_count)]
        for index, item in enumerate(catalog['items']):
            if group_count > 1:
                group = catalog['groups'][index % group_count]
                item.update(groupId=group['id'], group=group['name'])
            provider = ['nexus', 'modrinth', 'github'][index % 3]
            item['source'] = {'type': provider, 'verified': True, 'url': {
                'nexus': f'https://www.nexusmods.com/skyrimspecialedition/mods/{index+1}',
                'modrinth': f'https://modrinth.com/mod/fixture-{index}',
                'github': f'https://github.com/fixture/project-{index}',
            }[provider]}
        boot = harness.BOOT.replace('__CATALOG__', json.dumps(catalog)).replace('__FAIL__', '0').replace('prefs:{}', 'prefs:{autoCheckOnLaunch:false}')
        html = self.html.replace('<!--__MO2_BOOT__-->', boot)
        self.context.route('https://cdn.modrinth.com/**', lambda route: route.fulfill(content_type='image/png', body=PNG))
        self.context.route('http://gallery.fixture.test/', lambda route: route.fulfill(content_type='text/html', body=html))
        self.page.goto('http://gallery.fixture.test/', wait_until='load')
        self.connected()
        self.page.wait_for_function("__RESP__.counts['GET progress']>=1")
        self.page.wait_for_function('n=>document.querySelectorAll("#modList .mod-row").length===n', arg=count)

    def test_presentation_controls_preserve_rows_and_saved_preferences(self):
        self.open()
        self.page.locator('#libraryGallery').click()
        self.assertEqual(self.page.locator('body').get_attribute('data-library-view'), 'gallery')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 9)
        self.page.locator('#densityToggle').click()
        self.assertEqual(self.page.locator('body').get_attribute('data-density'), 'comfy')
        self.page.locator('#railToggle').click()
        self.assertEqual(self.page.locator('body').get_attribute('data-nav'), 'expanded')
        self.page.reload(wait_until='load')
        self.connected()
        self.assertEqual(self.page.locator('body').get_attribute('data-library-view'), 'gallery')
        self.assertEqual(self.page.locator('body').get_attribute('data-density'), 'comfy')
        self.assertEqual(self.page.locator('body').get_attribute('data-nav'), 'expanded')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 9)
        self.page.locator('#libraryList').click()
        self.assertEqual(self.page.locator('#libraryList').get_attribute('aria-pressed'), 'true')
        self.assertEqual(self.page.locator('#libraryGallery').get_attribute('aria-pressed'), 'false')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 9)

    def test_provider_filter_composes_with_search_and_reset(self):
        self.open()
        self.page.locator('#providerFilter').select_option('modrinth')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 3)
        self.page.locator('#search').fill('Unique Fixture 7')
        self.page.wait_for_function('document.querySelectorAll("#modList .mod-row").length===1')
        self.assertEqual(self.page.locator('#modList .mod-row').get_attribute('data-id'), 'item-7')
        self.page.locator('#libraryGallery').click()
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 1)
        self.page.locator('#clearFilters').click()
        self.assertEqual(self.page.locator('#providerFilter').input_value(), 'all')
        self.assertEqual(self.page.locator('#search').input_value(), '')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 9)
        self.assertEqual(self.page.locator('body').get_attribute('data-library-view'), 'gallery')

    def test_search_restore_and_sort_reuse_the_surviving_row(self):
        self.open(count=111)
        self.page.evaluate("window.__GALLERY_SURVIVOR__=document.getElementById('row-item-110')")
        self.page.locator('#search').fill('Unique Fixture 110')
        self.page.wait_for_function('document.querySelectorAll("#modList .mod-row").length===1')
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-110')===__GALLERY_SURVIVOR__"))
        self.page.locator('#search').fill('')
        self.page.wait_for_function('document.querySelectorAll("#modList .mod-row").length===111')
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-110')===__GALLERY_SURVIVOR__"))
        self.page.locator('#sortSelect').select_option('name')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 111)
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-110')===__GALLERY_SURVIVOR__"))
        self.page.locator('#libraryGallery').click()
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-110')===__GALLERY_SURVIVOR__"))
        self.page.locator('#densityToggle').click()
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-110')===__GALLERY_SURVIVOR__"))
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 111)

    def test_gallery_grouping_preserves_collapsed_chapters_and_sorts_all_records(self):
        self.open(count=12, group_count=3)
        self.page.locator('[data-chapter-toggle="group-0"]').click()
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 8)
        self.page.locator('#libraryGallery').click()
        self.assertTrue(self.page.locator('#galleryGrouping').is_visible())
        self.assertEqual(self.page.locator('#galleryGrouping').input_value(), 'all')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 12)
        ids = lambda:self.page.locator('#modList .mod-row').evaluate_all('rows=>rows.map(row=>row.dataset.id)')
        self.assertEqual(ids(),[f'item-{index}' for index in range(12)])
        self.page.locator('#workspaceTitle').click()
        self.page.keyboard.press('j')
        self.assertEqual(self.page.evaluate("document.activeElement.closest('.mod-row')?.dataset.id"),'item-0')
        self.page.keyboard.press('j')
        self.assertEqual(self.page.evaluate("document.activeElement.closest('.mod-row')?.dataset.id"),'item-1')
        self.page.locator('#sortSelect').select_option('name')
        self.assertEqual(ids(),[f'item-{index}' for index in (0,1,10,11,2,3,4,5,6,7,8,9)])
        self.page.locator('#galleryGrouping').select_option('chapters')
        self.assertEqual(self.page.locator('#modList .mod-row').count(),8)
        self.assertEqual(self.page.locator('[data-chapter-toggle="group-0"]').get_attribute('aria-expanded'),'false')
        self.page.reload(wait_until='load');self.connected()
        self.assertEqual(self.page.locator('body').get_attribute('data-library-view'),'gallery')
        self.assertEqual(self.page.locator('#galleryGrouping').input_value(),'chapters')
        self.assertEqual(self.page.locator('#modList .mod-row').count(),8)
        self.page.locator('#galleryGrouping').select_option('all')
        self.assertEqual(self.page.locator('#modList .mod-row').count(),12)
        self.page.locator('#libraryList').click()
        self.assertFalse(self.page.locator('#galleryGrouping').is_visible())
        self.assertEqual(self.page.locator('#modList .mod-row').count(),8)
        self.assertEqual(self.page.locator('[data-chapter-toggle="group-0"]').get_attribute('aria-expanded'),'false')
        self.page.locator('[data-chapter-toggle="group-0"]').click()
        self.assertEqual(self.page.locator('#modList .mod-row').count(),12)

    def test_next_pending_reveals_target_across_provider_filter(self):
        self.open()
        self.page.locator('#providerFilter').select_option('github')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 3)
        self.page.locator('#workspaceTitle').click()
        self.page.keyboard.press('n')
        self.assertEqual(self.page.locator('#providerFilter').input_value(), 'all')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 9)

    def test_context_menu_keyboard_escape_and_bookmark_have_real_actions(self):
        self.open()
        control = self.page.locator('#row-item-0 .inspect-btn')
        control.focus()
        self.page.keyboard.press('Shift+F10')
        menu = self.page.locator('#modContextMenu')
        self.assertTrue(menu.is_visible())
        self.assertTrue(menu.locator('button').first.evaluate('e=>e===document.activeElement'))
        self.page.keyboard.press('End')
        self.assertTrue(menu.locator('button').last.evaluate('e=>e===document.activeElement'))
        self.page.keyboard.press('Escape')
        self.assertFalse(menu.is_visible())
        self.assertTrue(self.page.locator('#row-item-0 [data-act="inspect"]').first.evaluate('e=>e===document.activeElement'))
        self.page.locator('#row-item-0').click(button='right')
        menu.locator('[data-context-act="favorite"]').click()
        self.assertTrue(self.page.evaluate('__ARSENAL_QA__.verifyRow("item-0").state.favorite'))
        self.assertFalse(menu.is_visible())
        self.page.locator('#row-item-0').click(button='right')
        menu.locator('[data-context-act="inspect"]').click()
        self.assertTrue(self.page.locator('#detailDrawer').evaluate('e=>e.classList.contains("open")'))
        self.assertIn('Unique Fixture 0', self.page.locator('#drawerTitle').inner_text())

    def test_profile_replacement_closes_old_context_menu(self):
        self.open()
        self.page.locator('#row-item-0').click(button='right')
        self.assertTrue(self.page.locator('#modContextMenu').is_visible())
        self.page.evaluate("""async()=>{const next=structuredClone(__RESP__.catalog);next.profileId='profile-b';next.profileName='Replacement profile';next.items[0].name='Replacement row';__RESP__.catalog=next;__RESP__.generation++;await __ARSENAL_QA__.setCatalog(next)}""")
        self.assertFalse(self.page.locator('#modContextMenu').is_visible())
        self.assertIn('Replacement row', self.page.locator('#row-item-0').inner_text())
        self.assertFalse(self.page.evaluate('Boolean(__ARSENAL_QA__.verifyRow("item-0").state.favorite)'))
        self.assertEqual(self.page.evaluate("__RESP__.counts['POST install']||0"), 0)

    def test_thumbnails_accept_only_approved_https_metadata_and_preserve_fallbacks(self):
        self.open(count=8)
        self.page.evaluate("""async()=>{
          const urls=['https://cdn.modrinth.com/data/fixture/approved.png','javascript:alert(1)','http://cdn.modrinth.com/unsafe.png','https://cdn.modrinth.com.attacker.invalid/unsafe.png','https://user:secret@cdn.modrinth.com/unsafe.png','file:///private.png','data:image/svg+xml,<svg/>','https://unknown.example/unsafe.png'];
          for(let i=0;i<urls.length;i++)__RESP__.catalog.items[i].update.thumbnailUrl=urls[i];
          __RESP__.catalog.items[0].update.thumbnailKind='icon';
          await __ARSENAL_QA__.setCatalog(structuredClone(__RESP__.catalog));
        }""")
        self.page.locator('#libraryGallery').click()
        self.assertEqual(self.page.locator('#modList img.mod-preview-image').count(), 1)
        image = self.page.locator('#row-item-0 img.mod-preview-image')
        self.assertEqual(image.get_attribute('src'), 'https://cdn.modrinth.com/data/fixture/approved.png')
        self.assertEqual(image.get_attribute('referrerpolicy'), 'no-referrer')
        self.assertEqual(image.get_attribute('loading'), 'lazy')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 8)
        self.assertIn('No preview supplied', self.page.locator('#row-item-7').inner_text())
        self.page.locator('#row-item-0 .mod-art').click()
        self.assertTrue(self.page.locator('#detailDrawer').evaluate('e=>e.classList.contains("open")'))

    def test_thumbnail_failure_keeps_mod_and_all_actions(self):
        self.open(count=1)
        self.page.route('https://cdn.modrinth.com/data/fixture/missing.png', lambda route: route.fulfill(status=404, content_type='text/plain', body='Fixture missing image'))
        self.page.evaluate("async()=>{__RESP__.catalog.items[0].update.thumbnailUrl='https://cdn.modrinth.com/data/fixture/missing.png';await __ARSENAL_QA__.setCatalog(structuredClone(__RESP__.catalog));}")
        self.page.locator('#libraryGallery').click()
        self.page.wait_for_function('document.querySelector("#row-item-0 .mod-preview-image")?.hidden===true')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 1)
        self.assertTrue(self.page.locator('#row-item-0 .inspect-btn').is_visible())
        self.assertIn('No preview supplied', self.page.locator('#row-item-0').inner_text())

    def test_offline_connection_details_are_actionable(self):
        # The public standalone file must describe its state without a native host.
        self.context.route('http://standalone.fixture.test/', lambda route: route.fulfill(content_type='text/html', body=self.html))
        self.page.goto('http://standalone.fixture.test/', wait_until='load')
        self.page.wait_for_function('!!window.__ARSENAL_QA__')
        self.page.locator('#workspaceDetails').click()
        self.assertTrue(self.page.locator('#connectionDetails').is_visible())
        self.assertIn('Standalone', self.page.locator('#connectionDetailsText').inner_text())
        self.page.locator('#connectionDetailsClose').click()
        self.assertFalse(self.page.locator('#connectionDetails').is_visible())


if __name__ == '__main__':
    unittest.main()
