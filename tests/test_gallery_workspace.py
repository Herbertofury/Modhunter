"""Real Chromium controls for the image-forward library; deterministic API only.

No external account or user profile is involved. Native Windows/MO2 receipts
remain separate. All thumbnail requests are intercepted with known fixture bytes.
"""
from __future__ import annotations

import base64
import json
import re
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

    def install_motion_probe(self, motion):
        self.page.emulate_media(reduced_motion=motion)
        self.context.add_init_script("""(() => {
          const probe=window.__LIBRARY_MOTION_QA__={guardAdditions:0,guardChanges:[],backgroundTransitions:0,samples:[]};
          new MutationObserver(records=>{for(const record of records){
            if(record.target.id!=='modList')continue;
            if(record.oldValue===null)probe.guardAdditions++;
            if(probe.guardChanges.length<32)probe.guardChanges.push({old:record.oldValue,current:record.target.getAttribute('data-layout-change')});
          }}).observe(document,{subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['data-layout-change']});
          document.addEventListener('transitionrun',event=>{
            if(!event.target.matches?.('#modList > .mod-row')||event.pseudoElement||event.propertyName!=='background-color')return;
            probe.backgroundTransitions++;
            if(probe.samples.length<8)probe.samples.push({id:event.target.dataset.id,property:event.propertyName});
          },true);
          probe.clearTransitions=()=>{probe.backgroundTransitions=0;probe.samples=[]};
          probe.snapshot=()=>{
            const list=document.getElementById('modList'),rows=[...list.querySelectorAll(':scope > .mod-row')],style=getComputedStyle(rows[0]);
            return {guard:list.getAttribute('data-layout-change'),view:document.body.dataset.libraryView,
              theme:document.body.dataset.theme,density:document.body.dataset.density,
              ids:rows.map(row=>row.dataset.id),allLaidOut:rows.every(row=>{const box=row.getBoundingClientRect();return box.width>0&&box.height>0&&getComputedStyle(row).contentVisibility==='visible'}),
              transition:{property:style.transitionProperty,duration:style.transitionDuration,delay:style.transitionDelay},
              guardAdditions:probe.guardAdditions,backgroundTransitions:probe.backgroundTransitions,samples:[...probe.samples]};
          };
        })()""")

    def motion_endpoint(self):
        # Read inside the same second-rAF callback used by the performance
        # harness. A later poll could conceal a guard that restores too late.
        return self.page.evaluate("""() => new Promise(resolve =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve(__LIBRARY_MOTION_QA__.snapshot()))))""")

    def assert_motion_endpoint(self, state, expected_ids, original_transition):
        self.assertIsNone(state['guard'], state)
        self.assertEqual(state['ids'], expected_ids)
        self.assertTrue(state['allLaidOut'], state)
        self.assertEqual(state['transition'], original_transition, state)

    def assert_live_row_hover(self, row='#row-item-0'):
        self.page.locator('#workspaceTitle').hover()
        idle="() => !document.getAnimations().some(animation=>animation.constructor.name==='CSSTransition'&&animation.effect?.target?.closest?.('#modList')&&animation.playState==='running')"
        self.page.wait_for_function(idle)
        self.page.evaluate('__LIBRARY_MOTION_QA__.clearTransitions()')
        self.page.locator(row).hover()
        self.page.wait_for_function('__LIBRARY_MOTION_QA__.backgroundTransitions>0')
        self.page.locator('#workspaceTitle').hover()
        self.page.wait_for_function(idle)
        self.page.evaluate('__LIBRARY_MOTION_QA__.clearTransitions()')

    def open(self, count=9, group_count=1, style_nonce=None):
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
        headers = {}
        if style_nonce:
            # Model the packaged Tauri style nonce in a real Chromium response.
            # Script/API fixtures stay unchanged; styles receive no unsafe-inline
            # exception, so a newly inserted unnonced style must be rejected.
            html = re.sub(r'<style\b', '<style nonce="'+style_nonce+'"', html)
            headers['Content-Security-Policy'] = "style-src 'nonce-"+style_nonce+"'"
        self.context.route('https://cdn.modrinth.com/**', lambda route: route.fulfill(content_type='image/png', body=PNG))
        self.context.route('http://gallery.fixture.test/', lambda route: route.fulfill(content_type='text/html', body=html, headers=headers))
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

    def test_gallery_geometry_works_with_packaged_style_nonce_policy(self):
        nonce = 'bW9kaHVudGVyLXN0eWxlLXFh'
        self.open(count=12, style_nonce=nonce)
        # Prove the browser enforces the response policy; a missing nonce in
        # the product's dynamic stylesheet must not pass this test by accident.
        self.assertTrue(self.page.evaluate("""() => {
          const probe=document.createElement('style');probe.textContent='.nonce-policy-probe{color:red}';
          document.head.append(probe);const blocked=probe.sheet===null;probe.remove();return blocked;
        }"""))

        def geometry(expected_columns):
            self.page.wait_for_function("""columns => {
              const rows=[...document.querySelectorAll('#modList .mod-row')];
              if(rows.length!==12)return false;const top=rows[0].getBoundingClientRect().top;
              return rows.filter(row=>Math.abs(row.getBoundingClientRect().top-top)<1).length===columns;
            }""", arg=expected_columns)
            state = self.page.evaluate("""() => {
              const style=document.querySelector('style[data-library-geometry]'),list=document.getElementById('modList');
              const rows=[...list.querySelectorAll('.mod-row')],box=list.getBoundingClientRect();
              return {nonce:style?.nonce,rule:style?.sheet?.cssRules[0]?.style.flexBasis,
                rules:style?.sheet?.cssRules.length,rows:rows.length,
                allLaidOut:rows.every(row=>row.getClientRects().length>0),
                contained:rows.every(row=>{const bounds=row.getBoundingClientRect();return bounds.left>=box.left-1&&bounds.right<=box.right+1;}),
                inheritedColumns:list.style.getPropertyValue('--library-columns'),
                inheritedGap:list.style.getPropertyValue('--library-gap-total'),
                overflow:document.documentElement.scrollWidth>innerWidth+1};
            }""")
            self.assertEqual(state['nonce'], nonce)
            self.assertEqual(state['rules'], 1)
            self.assertTrue(state['rule'].startswith('calc('), state)
            self.assertEqual(state['rows'], 12)
            self.assertTrue(state['allLaidOut'] and state['contained'], state)
            self.assertFalse(state['overflow'], state)
            self.assertEqual(state['inheritedColumns'], '')
            self.assertEqual(state['inheritedGap'], '')

        self.page.locator('#libraryGallery').click()
        geometry(6)
        self.page.locator('#densityToggle').click()
        geometry(4)
        self.page.set_viewport_size({'width':900,'height':900})
        geometry(2)
        self.page.set_viewport_size({'width':390,'height':844})
        geometry(1)
        self.page.locator('#densityToggle').click()
        geometry(2)
        self.page.locator('#libraryList').click()
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 12)

    def test_reduced_motion_does_not_create_library_transitions(self):
        self.install_motion_probe('reduce')
        self.open(count=12)
        self.assertTrue(self.page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"))
        # Listen before the real controls run. A near-zero duration still creates
        # CSS transitions (including on elements whose property defaults to all),
        # so checking a settled screenshot or getAnimations() alone misses them.
        self.page.evaluate("""async () => {
          for (let frame=0; frame<3; frame++) await new Promise(requestAnimationFrame);
          window.__LIBRARY_TRANSITIONS__={count:0,samples:[]};
          document.getElementById('modList').addEventListener('transitionrun',event=>{
            const record=window.__LIBRARY_TRANSITIONS__;
            record.count++;
            if(record.samples.length<8)record.samples.push({
              target:event.target.getAttribute('class')||event.target.tagName,
              property:event.propertyName,pseudo:event.pseudoElement
            });
          },true);
        }""")

        def state():
            return self.page.evaluate("""async () => {
              for (let frame=0; frame<3; frame++) await new Promise(requestAnimationFrame);
              const rows=[...document.querySelectorAll('#modList > .mod-row')];
              const source=rows[0].querySelector('.source-btn'),priority=rows[0].querySelector('.mod-priority');
              const sourceStyle=getComputedStyle(source),priorityStyle=getComputedStyle(priority);
              return {
                view:document.body.dataset.libraryView,density:document.body.dataset.density,theme:document.body.dataset.theme,
                ids:rows.map(row=>row.dataset.id),
                allLaidOut:rows.every(row=>{const box=row.getBoundingClientRect();
                  return box.width>0&&box.height>0&&getComputedStyle(row).visibility==='visible';}),
                sourcePadding:sourceStyle.paddingTop,priorityFont:priorityStyle.fontSize,
                firstHeight:rows[0].getBoundingClientRect().height,
                transitions:structuredClone(window.__LIBRARY_TRANSITIONS__)
              };
            }""")

        initial = state()
        self.page.locator('#libraryGallery').click()
        gallery = state()
        self.page.locator('#densityToggle').click()
        comfortable = state()
        self.page.locator('#libraryList').click()
        restored = state()
        self.page.locator('#railTheme').click()
        themed = state()

        self.assertEqual(gallery['view'], 'gallery')
        self.assertEqual(comfortable['density'], 'comfy')
        self.assertEqual(restored['view'], 'list')
        self.assertNotEqual(restored['theme'], themed['theme'])
        # These are genuine changes to properties that the former universal
        # duration rule implicitly animated, rather than a no-op control test.
        self.assertNotEqual((initial['sourcePadding'], initial['priorityFont']),
                            (gallery['sourcePadding'], gallery['priorityFont']))
        self.assertNotEqual(initial['firstHeight'], restored['firstHeight'])
        for snapshot in (initial, gallery, comfortable, restored, themed):
            self.assertEqual(snapshot['ids'], [f'item-{index}' for index in range(12)])
            self.assertTrue(snapshot['allLaidOut'], snapshot)
            self.assertEqual(snapshot['transitions']['count'], 0, snapshot['transitions'])
        self.assertEqual(self.page.evaluate('__LIBRARY_MOTION_QA__.guardAdditions'), 0)
        self.assertIsNone(self.page.locator('#modList').get_attribute('data-layout-change'))

    def test_normal_motion_bulk_changes_restore_hover_at_the_existing_frame_endpoint(self):
        self.install_motion_probe('no-preference')
        self.open(count=24)
        self.assertFalse(self.page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"))
        initial=self.motion_endpoint()
        ids=[f'item-{index}' for index in range(24)]
        original=initial['transition']
        self.assertEqual(original,{'property':'background','duration':'0.12s','delay':'0s'})
        self.assertEqual(initial['guardAdditions'],0,'Startup armed the bulk-change guard')
        self.assert_motion_endpoint(initial,ids,original)
        self.assert_live_row_hover()
        previous=initial
        for selector,expected_view in (('#libraryGallery','gallery'),('#railTheme','gallery'),('#libraryList','list')):
            self.page.evaluate('__LIBRARY_MOTION_QA__.clearTransitions()')
            self.page.locator(selector).click()
            current=self.motion_endpoint()
            self.assert_motion_endpoint(current,ids,original)
            self.assertEqual(current['view'],expected_view)
            self.assertGreater(current['guardAdditions'],previous['guardAdditions'],current)
            self.assertEqual(current['backgroundTransitions'],0,current)
            if selector=='#railTheme':self.assertNotEqual(current['theme'],previous['theme'])
            if selector=='#libraryGallery':
                additions=current['guardAdditions']
                self.page.locator('#densityToggle').click()
                density=self.motion_endpoint()
                self.assert_motion_endpoint(density,ids,original)
                self.assertEqual(density['guardAdditions'],additions,'Density-only change armed the guard')
                self.assertNotEqual(density['density'],current['density'])
                current=density
            previous=current
        self.assert_live_row_hover()
        # Keyboard selection still reaches the real row and Aether selection
        # after the structural guard has restored ordinary interaction styles.
        options=self.page.evaluate('__ARSENAL_QA__.aether().options')
        self.page.locator('#workspaceTitle').click()
        self.page.keyboard.press('j')
        self.assertTrue(self.page.locator('#row-item-0 .inspect-btn').evaluate('element=>element===document.activeElement'))
        self.assertTrue(self.page.locator('#row-item-0').evaluate('element=>element.classList.contains("aether-target")'))
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.aether().selected'),'item-0')
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.aether().options'),options)
        self.assertIsNone(self.page.locator('#modList').get_attribute('data-layout-change'))

    def test_profile_replacement_restores_real_row_styles_after_a_pending_view_change(self):
        self.install_motion_probe('no-preference')
        self.open(count=12)
        initial=self.motion_endpoint()
        original=initial['transition']
        changed=self.page.evaluate("""async()=>{
          const list=document.getElementById('modList'),oldRow=list.querySelector('.mod-row');
          document.getElementById('libraryGallery').click();
          const oldToken=list.getAttribute('data-layout-change');
          const next=structuredClone(__RESP__.catalog);
          next.profileId='motion-profile-b';next.profileName='Motion replacement profile';next.executionEpoch=9;
          next.items.forEach((item,index)=>{item.id='replacement-'+index;item.name='Replacement '+index});
          __RESP__.catalog=next;__RESP__.generation++;
          await __ARSENAL_QA__.setCatalog(next);
          return {oldToken,currentToken:list.getAttribute('data-layout-change'),oldRowConnected:oldRow.isConnected};
        }""")
        self.assertIsNotNone(changed['oldToken'])
        self.assertNotEqual(changed['oldToken'],changed['currentToken'],'Previous-profile guard token survived replacement')
        self.assertFalse(changed['oldRowConnected'])
        replacement_ids=[f'replacement-{index}' for index in range(12)]
        self.assert_motion_endpoint(self.motion_endpoint(),replacement_ids,original)
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.getCatalog().profileId'),'motion-profile-b')
        # A new change must still work after cleanup, with no old callback
        # disabling the replacement profile's guard or restoring it late.
        self.page.evaluate('__LIBRARY_MOTION_QA__.clearTransitions()')
        self.page.locator('#railTheme').click()
        final=self.motion_endpoint()
        self.assert_motion_endpoint(final,replacement_ids,original)
        self.assertEqual(final['backgroundTransitions'],0,final)

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
