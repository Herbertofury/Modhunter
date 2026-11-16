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

    def open(self, count=9, group_count=1, style_nonce=None, feedback=False):
        catalog = harness.catalog(count=count)
        if group_count > 1:
            catalog['groups'] = [{'id':f'group-{group}', 'name':f'Fixture chapter {group}', 'startPriority':group,
                                  'count':len(range(group,count,group_count)),
                                  'color':(['sky','purple','red'][group % 3] if feedback else 'mint')}
                                 for group in range(group_count)]
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
        if feedback:
            # Keep existing cases/fixture timing unchanged. These additional API
            # responses expose real request, retry and cancellation UI paths.
            boot = boot.replace('prefs:{autoCheckOnLaunch:false}',
                                'prefs:{autoCheckOnLaunch:false,vfxIntensity:"off",vfxShake:false,vfxTrails:false,sound:false}')
            start = boot.index("else if(path==='scan'){")
            end = boot.index("else if(path==='quick-update')", start)
            boot = boot[:start] + """else if(path==='scan'){
              if(method==='POST'){
                if(x.failScanStart)result={ok:false,error:'QA source service unavailable; retry this check'};
                else{x.scan={state:'running',mode:body.mode,checked:0,total:c.items.length,jobId:41};
                  result={ok:true,profileId:c.profileId,...structuredClone(x.scan)};}
              }else if(x.failScanPoll)result={ok:false,error:'QA scan status could not be retrieved'};
              else result={ok:true,profileId:c.profileId,scan:structuredClone(x.scan)};
            }else if(path==='cancel-scan'){
              x.cancelBody=body;x.scan={...x.scan,state:'cancelled'};
              result={ok:true,profileId:c.profileId,scan:structuredClone(x.scan)};
            }""" + boot[end:]
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

    def feedback_capture(self, filename):
        import os
        from pathlib import Path
        output = os.environ.get('MODHUNTER_QA_OUTPUT')
        if output:
            target = Path(output) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(target))

    def chapter_feedback(self):
        return self.page.locator('#modList > .chapterbar').evaluate_all("""bars=>bars.map(bar=>{
          const style=getComputedStyle(bar),diamond=bar.querySelector('.diamond'),track=bar.querySelector('.chapter-mini-progress');
          const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
          const ctx=canvas.getContext('2d');ctx.fillStyle=style.borderLeftColor;ctx.fillRect(0,0,1,1);
          const rect=bar.getBoundingClientRect(),trackRect=track.getBoundingClientRect();
          return {id:bar.id,color:[...ctx.getImageData(0,0,1,1).data],border:parseFloat(style.borderLeftWidth),
            background:style.backgroundImage,expanded:bar.getAttribute('aria-expanded'),
            diamondVisible:getComputedStyle(diamond).display!=='none'&&diamond.getBoundingClientRect().width>0,
            trackVisible:getComputedStyle(track).display!=='none'&&trackRect.width>0&&trackRect.height>0,
            fill:track.firstElementChild.getBoundingClientRect().width,trackWidth:trackRect.width,
            completion:bar.dataset.completion,chevron:getComputedStyle(bar,'::after').transform,
            count:bar.querySelector('.chapter-count').textContent,rect:{left:rect.left,right:rect.right,width:rect.width,height:rect.height}};
        })""")

    def assert_scan_feedback(self, state, *, busy=False, button_state='idle', rows=6):
        self.page.wait_for_function("state=>document.getElementById('workspaceCheckLine').dataset.state===state", arg=state)
        self.assertEqual(self.page.locator('#workspaceDot').get_attribute('data-state'), state)
        self.assertEqual(self.page.locator('#unifiedScan').get_attribute('data-state'), button_state)
        self.assertEqual(self.page.locator('#unifiedScan').get_attribute('aria-busy'), str(busy).lower())
        self.assertEqual(self.page.locator('#workspaceScanProgress').is_visible(), busy)
        self.assertEqual(self.page.locator('#modList > .mod-row').count(), rows)
        for action in ('POST install','POST download','POST update-queue','POST quick-update'):
            self.assertEqual(self.page.evaluate('key=>__RESP__.counts[key]||0', action), 0)

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
        # Exercise genuine alpha artwork in cover-fit mode, as in the separate
        # preview fixture; this is not a provider screenshot metadata claim.
        import hashlib
        import os
        from io import BytesIO
        from pathlib import Path
        from PIL import Image, ImageColor

        fixture = json.loads((harness.ROOT / 'tests/fixtures/visual_public_catalog.json').read_text())
        source = next(record for record in fixture['sources'] if record['slug'] == 'iris')
        artwork = (harness.ROOT / 'tests/fixtures' / source['thumbnailFile']).read_bytes()
        self.assertEqual(len(artwork), source['bytes'])
        self.assertEqual(hashlib.sha256(artwork).hexdigest(), source['sha256'])
        with Image.open(BytesIO(artwork)) as image:
            alpha = image.convert('RGBA').getchannel('A')
            self.assertEqual(alpha.size, (96, 96))
            self.assertEqual(alpha.crop((43, 43, 53, 53)).getextrema(), (0, 0))

        def assert_center_matte(art, loaded):
            # The source has a fully transparent center. Inspect actual rendered
            # pixels instead of testing that a particular CSS declaration exists.
            matte = ImageColor.getcolor(art.evaluate('e=>getComputedStyle(e).backgroundColor'), 'RGBA')
            self.assertEqual(matte[3], 255)
            with Image.open(BytesIO(art.screenshot(animations='allow'))) as image:
                image = image.convert('RGBA')
                center_x, center_y = image.width // 2, image.height // 2
                pixels = [image.getpixel((x, y)) for y in range(center_y - 4, center_y + 5)
                          for x in range(center_x - 4, center_x + 5)]
            if loaded:
                self.assertEqual(set(pixels), {matte}, 'Fallback glyph/text shows through a loaded alpha image')
            else:
                self.assertTrue(any(pixel != matte for pixel in pixels), 'Failed-image fallback pictogram is not painted')

        output = os.environ.get('MODHUNTER_QA_OUTPUT')
        if output:
            output = Path(output)
            output.mkdir(parents=True, exist_ok=True)

        def retain_preview(name, full_page=False):
            if output:
                self.page.screenshot(path=str(output / name), full_page=full_page,
                                     animations='allow', caret='hide')

        self.open(count=1)
        self.page.route(source['thumbnailUrl'], lambda route: route.fulfill(content_type=source['mimeType'], body=artwork))
        self.page.evaluate("""async source=>{
          const item=__RESP__.catalog.items[0];item.name=source.name;
          item.source={type:'modrinth',verified:true,url:source.projectUrl};
          item.update.thumbnailUrl=source.thumbnailUrl;item.update.thumbnailKind='image';
          await __ARSENAL_QA__.setCatalog(structuredClone(__RESP__.catalog));
        }""", source)
        self.page.locator('#libraryGallery').click()
        for width in (1440, 390):
            self.page.set_viewport_size({'width': width, 'height': 900})
            for theme in ('night', 'snow'):
                if self.page.locator('body').get_attribute('data-theme') != theme:
                    self.page.evaluate("document.getElementById('themeToggle').click()")
                self.page.wait_for_function('theme=>document.body.dataset.theme===theme', arg=theme)
                art = self.page.locator('#row-item-0 .mod-art')
                self.page.wait_for_function("""() => {
                  const image=document.querySelector('#row-item-0 .mod-preview-image');
                  return image?.complete && image.naturalWidth===96 && !image.hidden;
                }""")
                self.assertEqual(art.locator('img').evaluate('e=>getComputedStyle(e).objectFit'), 'cover')
                representative = (width, theme) in ((1440, 'night'), (390, 'snow'))
                if representative:
                    retain_preview(f'thumbnail-alpha-library-{width}-{theme}.png', full_page=True)
                assert_center_matte(art, loaded=True)
                art.click()
                drawer = self.page.locator('#drawerContent .mod-art')
                self.page.wait_for_function("""() => {
                  const image=document.querySelector('#drawerContent .mod-preview-image');
                  return image?.complete && image.naturalWidth===96 && !image.hidden;
                }""")
                if representative:
                    retain_preview(f'thumbnail-alpha-inspector-{width}-{theme}.png')
                assert_center_matte(drawer, loaded=True)
                self.page.locator('#drawerClose').click()

        self.page.route('https://cdn.modrinth.com/data/fixture/missing.png', lambda route: route.fulfill(status=404, content_type='text/plain', body='Fixture missing image'))
        self.page.evaluate("async()=>{__RESP__.catalog.items[0].update.thumbnailUrl='https://cdn.modrinth.com/data/fixture/missing.png';await __ARSENAL_QA__.setCatalog(structuredClone(__RESP__.catalog));}")
        self.page.wait_for_function('document.querySelector("#row-item-0 .mod-preview-image")?.hidden===true')
        self.assertEqual(self.page.locator('#modList .mod-row').count(), 1)
        self.assertTrue(self.page.locator('#row-item-0 .inspect-btn').is_visible())
        self.assertIn('No preview supplied', self.page.locator('#row-item-0').inner_text())
        retain_preview('thumbnail-failed-library-390-snow.png', full_page=True)
        assert_center_matte(self.page.locator('#row-item-0 .mod-art'), loaded=False)
        self.page.locator('#row-item-0 .mod-art').click()
        self.page.wait_for_function('document.querySelector("#drawerContent .mod-preview-image")?.hidden===true')
        retain_preview('thumbnail-failed-inspector-390-snow.png')
        assert_center_matte(self.page.locator('#drawerContent .mod-art'), loaded=False)
        self.assertTrue(self.page.locator('#drawerClose').is_visible())

    def test_cold_filtered_rows_restore_identity_order_after_empty_results_and_collapse(self):
        self.open(count=257, group_count=3)
        self.page.evaluate("window.__COLD_ROWS__=[...document.querySelectorAll('#modList > .mod-row')]")
        self.page.locator('#search').fill('"Unique Fixture 256"')
        self.page.wait_for_function("document.querySelectorAll('#modList > .mod-row').length===1")
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-256')===__COLD_ROWS__.find(row=>row.dataset.id==='item-256')"))
        self.assertTrue(self.page.evaluate("__COLD_ROWS__.filter(row=>row.dataset.id!=='item-256').every(row=>!row.isConnected)"))
        self.page.locator('#search').fill('')
        self.page.wait_for_function("document.querySelectorAll('#modList > .mod-row').length===257")
        self.assertTrue(self.page.evaluate("__COLD_ROWS__.every(row=>document.getElementById(row.id)===row)"))
        self.page.locator('#search').fill('no-matching-cache-record')
        self.page.wait_for_function("document.querySelectorAll('#modList > .mod-row').length===0")
        self.assertTrue(self.page.locator('#modList .empty-state').is_visible())
        self.assertTrue(self.page.evaluate('__COLD_ROWS__.every(row=>!row.isConnected)'))
        self.page.locator('#search').fill('')
        self.page.wait_for_function("document.querySelectorAll('#modList > .mod-row').length===257")
        self.assertTrue(self.page.evaluate("__COLD_ROWS__.every(row=>document.getElementById(row.id)===row)"))
        self.page.locator('#section-group-0').click()
        self.assertEqual(self.page.locator('#modList > .mod-row').count(), 171)
        self.assertTrue(self.page.evaluate("__COLD_ROWS__.filter(row=>Number(row.dataset.id.slice(5))%3===0).every(row=>!row.isConnected)"))
        self.page.locator('#section-group-0').click()
        self.assertTrue(self.page.evaluate("""() => {
          const rows=[...document.querySelectorAll('#modList > .mod-row')];
          return rows.length===257&&rows.every((row,index)=>row===__COLD_ROWS__[index]&&
            row.getBoundingClientRect().width>0&&row.getBoundingClientRect().height>0&&getComputedStyle(row).contentVisibility==='visible');
        }"""))

    def test_filtered_cache_uses_new_notes_status_and_catalog_ownership(self):
        self.open(count=12)
        self.page.evaluate("window.__METADATA_ROWS__=[...document.querySelectorAll('#modList > .mod-row')]")
        self.page.locator('#search').fill('"Unique Fixture 11"')
        self.page.on('dialog', lambda dialog: dialog.accept())
        backup={'catalogId':'profile-a','rows':{'item-0':{'note':'Changed while this row was filtered out','status':'review','favorite':True}}}
        self.page.locator('#importFile').set_input_files({'name':'cache-progress.json','mimeType':'application/json','buffer':json.dumps(backup).encode()})
        self.page.wait_for_function("__ARSENAL_QA__.verifyRow('item-0').state.note==='Changed while this row was filtered out'")
        self.page.locator('#search').fill('')
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-0')!==__METADATA_ROWS__[0]"))
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-1')===__METADATA_ROWS__[1]"))
        note=self.page.locator('#row-item-0 .mod-note')
        self.assertEqual(note.text_content(),'Changed while this row was filtered out')
        self.assertEqual(note.get_attribute('title'),'Changed while this row was filtered out')
        self.assertIn('needs-review', self.page.locator('#row-item-0').get_attribute('class'))
        self.assertIn('active', self.page.locator('#row-item-0 .favorite-btn').get_attribute('class'))
        self.page.locator('#row-item-0 [data-act=inspect]').click()
        self.assertEqual(self.page.locator('#drawerNote').input_value(),'Changed while this row was filtered out')
        self.page.locator('#drawerClose').click()
        self.page.locator('#search').fill('"Unique Fixture 11"')
        self.page.evaluate("""async()=>{
          const next=structuredClone(__RESP__.catalog);next.executionEpoch=9;
          next.items=next.items.filter(item=>item.id!=='item-1');next.groups[0].count=11;
          __RESP__.catalog=next;__RESP__.generation++;await __ARSENAL_QA__.setCatalog(next);
        }""")
        self.page.locator('#search').fill('')
        self.assertEqual(self.page.locator('#modList > .mod-row').count(), 11)
        self.assertEqual(self.page.locator('#row-item-1').count(), 0)
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-2')!==__METADATA_ROWS__[2]"))
        self.assertTrue(self.page.evaluate("__METADATA_ROWS__[1].isConnected===false"))

    def test_filtered_cache_does_not_revive_selected_fx_or_steal_surviving_focus(self):
        self.page.emulate_media(reduced_motion='no-preference')
        self.open(count=12)
        result=self.page.evaluate("""() => {
          const intensity=document.getElementById('fxIntensity');intensity.value='subtle';
          intensity.dispatchEvent(new Event('change',{bubbles:true}));
          const row=document.getElementById('row-item-0');window.__OLD_HOT_ROW__=row;
          window.__OTHER_COLD_ROW__=document.getElementById('row-item-1');
          AetherFX.select(row,{force:true});
          const selected=row.classList.contains('aether-target');
          const stage=!!row.querySelector('.aether-selection-stage');
          const search=document.getElementById('search');search.value='"Unique Fixture 11"';
          search.dispatchEvent(new Event('input',{bubbles:true}));
          return {selected,stage,detached:!row.isConnected};
        }""")
        self.assertTrue(result['selected'], result)
        self.assertTrue(result['stage'], result)
        self.assertTrue(result['detached'], result)
        self.page.evaluate("document.querySelector('#row-item-11 [data-act=inspect]').click()")
        self.page.locator('#drawerClose').click()
        self.page.locator('#row-item-11 [data-act=inspect]').focus()
        self.page.evaluate("const search=document.getElementById('search');search.value='';search.dispatchEvent(new Event('input',{bubbles:true}))")
        self.assertEqual(self.page.locator('#modList > .mod-row').count(), 12)
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-0')!==__OLD_HOT_ROW__"))
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-1')===__OTHER_COLD_ROW__"))
        self.assertTrue(self.page.evaluate("document.activeElement===document.querySelector('#row-item-11 [data-act=inspect]')"))
        self.assertFalse(self.page.locator('#row-item-0').evaluate("row=>row.matches('.aether-target,.native-selected,.focus-signal,.shot-hit,.aether-shake,.aether-hover-anchored')||!!row.querySelector('.aether-selection-stage')"))
        self.page.wait_for_function("!__OLD_HOT_ROW__.querySelector('.aether-selection-stage')")
        self.assertFalse(self.page.locator('#row-item-0').evaluate("row=>!!row.querySelector('.aether-selection-stage')"))

    def test_image_failure_while_detached_uses_fresh_image_and_visible_fallback(self):
        self.open(count=12)
        self.page.locator('#libraryGallery').click()
        url='https://cdn.modrinth.com/data/cache-fixture/pending.png'
        requests=[]
        def pending_image(route):
            requests.append(route)
            if len(requests)>1:
                route.fulfill(status=404,content_type='text/plain',body='Fixture missing image',headers={'Cache-Control':'no-store'})
        self.page.route(url,pending_image)
        with self.page.expect_request(url):
            self.page.evaluate("""async url=>{
              const item=__RESP__.catalog.items[1];item.update.thumbnailUrl=url;item.update.thumbnailKind='icon';
              await __ARSENAL_QA__.setCatalog(structuredClone(__RESP__.catalog));
            }""",url)
        # A request event precedes the Python route callback; this actual DOM
        # read also gives Playwright the opportunity to deliver the paused route.
        self.page.evaluate("window.__FAILED_CACHED_ROW__=document.getElementById('row-item-1');window.__PENDING_IMAGE__=__FAILED_CACHED_ROW__.querySelector('img');window.__GOOD_CACHED_ROW__=document.getElementById('row-item-0')")
        self.assertFalse(self.page.evaluate('__PENDING_IMAGE__.complete'))
        self.assertEqual(len(requests),1)
        self.page.locator('#search').fill('"Unique Fixture 11"')
        self.assertFalse(self.page.evaluate('__FAILED_CACHED_ROW__.isConnected'))
        requests[0].fulfill(status=404,content_type='text/plain',body='Fixture missing image',headers={'Cache-Control':'no-store'})
        self.page.wait_for_function('__PENDING_IMAGE__.complete&&__PENDING_IMAGE__.naturalWidth===0')
        self.assertFalse(self.page.evaluate('__PENDING_IMAGE__.hidden'),'The detached event cannot reach the list capture handler')
        self.page.locator('#search').fill('')
        self.page.wait_for_function("document.querySelector('#row-item-1 .mod-preview-image')?.hidden===true")
        self.assertGreaterEqual(len(requests),2)
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-1')!==__FAILED_CACHED_ROW__"))
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-0')===__GOOD_CACHED_ROW__"))
        self.assertEqual(self.page.locator('#modList > .mod-row').count(),12)
        self.assertIn('No preview supplied',self.page.locator('#row-item-1').inner_text())
        self.assertTrue(self.page.locator('#row-item-1 [data-act=inspect]').is_visible())

    def test_locale_change_discards_translated_detached_rows(self):
        self.open(count=12)
        # Execute the shipped translator, including its real connected-DOM
        # observer and English fast path. No translation strings are mocked.
        self.page.add_script_tag(content=(harness.ROOT/'native/ui/locale-catalogs.js').read_text())
        self.page.add_script_tag(content=(harness.ROOT/'native/ui/preferences.js').read_text())
        self.page.evaluate("ModhunterI18n.apply({locale:'es',theme:'dark'})")
        expected=self.page.evaluate("ModhunterI18n.translate('Details')")
        self.assertNotEqual(expected,'Details')
        self.page.wait_for_function("text=>document.querySelector('#row-item-0 .inspect-btn').textContent===text",arg=expected)
        self.page.evaluate("window.__SPANISH_CACHED_ROW__=document.getElementById('row-item-0')")
        self.page.locator('#search').fill('"Unique Fixture 11"')
        self.assertFalse(self.page.evaluate('__SPANISH_CACHED_ROW__.isConnected'))
        self.page.evaluate("ModhunterI18n.apply({locale:'en'})")
        self.page.wait_for_function("document.documentElement.lang==='en'&&document.querySelector('#row-item-11 .inspect-btn').textContent==='Details'")
        self.assertEqual(self.page.evaluate("__SPANISH_CACHED_ROW__.querySelector('.inspect-btn').textContent"),expected)
        self.page.locator('#search').fill('')
        self.assertEqual(self.page.locator('#modList > .mod-row').count(),12)
        self.assertTrue(self.page.evaluate("document.getElementById('row-item-0')!==__SPANISH_CACHED_ROW__"))
        self.assertEqual(self.page.locator('#row-item-0 .inspect-btn').text_content(),'Details')
        self.assertTrue(self.page.locator('#row-item-0 .inspect-btn').get_attribute('aria-label').startswith('Inspect '))

    def test_oled_chapters_keep_color_progress_and_keyboard_collapse_in_both_layouts(self):
        self.open(count=12, group_count=3, feedback=True)
        self.assertEqual(self.page.locator('body').evaluate('e=>getComputedStyle(e).backgroundColor'), 'rgb(0, 0, 0)')
        chapters = self.chapter_feedback()
        self.assertEqual(len(chapters), 3)
        self.assertEqual(len({tuple(c['color']) for c in chapters}), 3, chapters)
        for chapter in chapters:
            self.assertGreaterEqual(chapter['border'], 2, chapter)
            self.assertGreater(max(chapter['color'][:3])-min(chapter['color'][:3]), 35, chapter)
            self.assertNotEqual(chapter['background'], 'none', chapter)
            self.assertTrue(chapter['diamondVisible'] and chapter['trackVisible'], chapter)
            self.assertEqual(chapter['completion'], 'empty')
        first = self.page.locator('[data-chapter-toggle="group-0"]')
        first.focus();self.page.keyboard.press('Enter')
        self.assertEqual(first.get_attribute('aria-expanded'), 'false')
        self.assertTrue(first.evaluate('e=>e===document.activeElement'), 'Collapse must retain keyboard focus')
        self.assertEqual(self.page.locator('#modList > .mod-row').count(), 8)
        self.assertNotEqual(self.chapter_feedback()[0]['chevron'], chapters[0]['chevron'])
        self.page.keyboard.press('Enter')
        self.assertEqual(first.get_attribute('aria-expanded'), 'true')
        self.assertEqual(self.page.locator('#modList > .mod-row').count(), 12)
        self.page.locator('#workspaceTitle').click();self.page.keyboard.press('j');self.page.keyboard.press('Space')
        self.page.wait_for_function('__ARSENAL_QA__.verifyRow("item-0").state.status==="done"')
        progress = self.chapter_feedback()[0]
        self.assertEqual(progress['count'], '1/4')
        self.assertEqual(progress['completion'], 'partial')
        self.assertGreater(progress['fill'], 0)
        self.assertLess(progress['fill'], progress['trackWidth'])
        self.feedback_capture('oled-chapters-1440-dark.png')
        for _ in range(3):
            self.page.keyboard.press('j');self.page.keyboard.press('Space')
        completed = self.chapter_feedback()[0]
        self.assertEqual(completed['count'], '4/4')
        self.assertEqual(completed['completion'], 'complete')
        self.assertAlmostEqual(completed['fill'], completed['trackWidth'], delta=1)
        self.page.locator('#libraryGallery').click()
        self.page.locator('#galleryGrouping').select_option('chapters')
        grouped = self.chapter_feedback()
        self.assertEqual([c['color'] for c in grouped], [c['color'] for c in chapters])
        self.assertTrue(all(c['diamondVisible'] and c['trackVisible'] for c in grouped), grouped)
        self.assertEqual(self.page.locator('#modList > .mod-row').count(), 12)
        self.page.locator('#libraryList').click();self.page.locator('#railTheme').click()
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertEqual(self.page.locator('body').get_attribute('data-theme'), 'snow')
        self.assertNotEqual(self.page.locator('body').evaluate('e=>getComputedStyle(e).backgroundColor'), 'rgb(0, 0, 0)')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 391)
        narrow = self.chapter_feedback()
        self.assertEqual(len(narrow), 3)
        for chapter in narrow:
            self.assertTrue(chapter['diamondVisible'] and chapter['trackVisible'], chapter)
            self.assertGreaterEqual(chapter['rect']['left'], 0)
            self.assertLessEqual(chapter['rect']['right'], 391)
        self.page.locator('#workspaceTitle').hover()
        done_paint = self.page.locator('#row-item-0').evaluate('''e=>({
            hot:e.matches(':hover,:focus-within,.selected,.native-selected,.aether-target'),
            background:getComputedStyle(e).backgroundColor,image:getComputedStyle(e).backgroundImage
        })''')
        self.assertEqual(done_paint, {'hot':False,'background':'rgba(0, 0, 0, 0)','image':'none'},
                         'An unfocused manually cleared light row must not inherit the old mint fill')
        source_title = self.page.locator('#modList > .mod-row:not(.done):not(.aether-target):not(.native-selected):not(.selected) .mod-title.source-title').first
        self.assertEqual(source_title.evaluate('e=>getComputedStyle(e).color'),
                         self.page.locator('body').evaluate('e=>getComputedStyle(e).color'),
                         'Ordinary source-linked titles must use the neutral light text color')
        self.assertFalse(self.page.evaluate("document.getAnimations().some(a=>a.constructor.name==='CSSTransition'&&a.effect?.target?.closest?.('#modList')&&a.playState==='running')"))
        self.feedback_capture('oled-chapters-390-light.png')

    def test_manual_bookmark_hover_focus_and_navigation_feedback_remain_distinct(self):
        self.page.emulate_media(reduced_motion='no-preference')
        self.open(count=6, group_count=3, feedback=True)
        title = self.page.locator('#row-item-0 .mod-title')
        assessment = self.page.locator('#row-item-0 .detected-status').inner_text()
        self.assertEqual(self.page.locator('#row-item-0 .manual-status').count(), 0)
        self.page.locator('#row-item-0 .inspect-btn').click()
        self.page.locator('#drawerStatus').select_option('review');self.page.locator('#saveInspector').click()
        review = self.page.locator('#row-item-0 .manual-status[data-manual="review"]')
        self.assertTrue(review.is_visible())
        self.assertTrue(review.inner_text().strip())
        self.assertIn('needs-review', self.page.locator('#row-item-0').get_attribute('class'))
        self.page.locator('#row-item-0 .inspect-btn').click();self.page.locator('#markInspectorDone').click()
        done = self.page.locator('#row-item-0 .manual-status[data-manual="done"]')
        self.assertTrue(done.is_visible())
        self.assertIn('manual', done.inner_text().lower())
        self.assertIn('line-through', title.evaluate('e=>getComputedStyle(e).textDecorationLine'))
        self.assertEqual(self.page.locator('#row-item-0 .detected-status').inner_text(), assessment)
        self.assertNotIn('Up to date', assessment)
        favorite = self.page.locator('#row-item-0 .favorite-btn')
        inactive = favorite.evaluate('e=>getComputedStyle(e).backgroundColor')
        favorite.click()
        self.assertEqual(favorite.get_attribute('aria-pressed'), 'true')
        self.assertIn('Remove', favorite.get_attribute('aria-label'))
        self.assertEqual(favorite.inner_text(), '★')
        self.assertNotEqual(favorite.evaluate('e=>getComputedStyle(e).backgroundColor'), inactive)
        self.assertIn('Bookmarked', self.page.locator('#toastWrap').inner_text())
        row = self.page.locator('#row-item-1')
        self.page.locator('#workspaceTitle').hover()
        idle = "() => !document.getAnimations().some(a=>a.constructor.name==='CSSTransition'&&a.effect?.target?.closest?.('#modList')&&a.playState==='running')"
        self.page.wait_for_function(idle)
        resting = row.evaluate('e=>getComputedStyle(e).backgroundColor')
        row.hover();self.page.wait_for_function(idle)
        self.assertNotEqual(row.evaluate('e=>getComputedStyle(e).backgroundColor'), resting)
        control = row.locator('.inspect-btn')
        control.focus();self.page.keyboard.press('Tab');self.page.keyboard.press('Shift+Tab')
        focus = control.evaluate('e=>({active:e===document.activeElement,visible:e.matches(":focus-visible"),style:getComputedStyle(e).outlineStyle,width:parseFloat(getComputedStyle(e).outlineWidth)})')
        self.assertTrue(focus['active'] and focus['visible'], focus)
        self.assertNotEqual(focus['style'], 'none', focus)
        self.assertGreaterEqual(focus['width'], 1, focus)
        self.feedback_capture('oled-manual-feedback-1440-dark.png')
        updates = self.page.locator('.workspace-tabs [data-workspace="updates"]')
        inactive_nav = updates.evaluate('e=>getComputedStyle(e).backgroundColor')
        updates.click()
        self.assertEqual(self.page.locator('body').get_attribute('data-workspace'), 'updates')
        self.assertIn('active', updates.get_attribute('class'))
        self.assertNotEqual(updates.evaluate('e=>getComputedStyle(e).backgroundColor'), inactive_nav)
        workshop_backgrounds = self.page.locator('.workshop-filter :is(input,select)').evaluate_all("es=>es.map(e=>getComputedStyle(e).backgroundColor)")
        self.assertEqual(workshop_backgrounds, ['rgb(11, 12, 16)'] * 2, 'Both Workshop filters must use the OLED input surface')
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.game()?.score||0'), 0)
        for action in ('POST install','POST download','POST quick-update','POST update-queue'):
            self.assertEqual(self.page.evaluate('key=>__RESP__.counts[key]||0', action), 0)

    def test_scan_start_and_real_progress_have_visible_accessible_feedback(self):
        self.open(count=12, group_count=3, feedback=True)
        self.page.evaluate('__RESP__.defer.scan=true')
        self.page.locator('#unifiedScan').click()
        self.page.wait_for_function('__RESP__.pending.some(p=>p.path==="scan")')
        self.assert_scan_feedback('busy', busy=True, button_state='starting', rows=12)
        self.assertTrue(self.page.locator('#unifiedScan').is_disabled())
        self.assertIn('Starting', self.page.locator('#workspaceCheckStatus').inner_text())
        self.assertIsNone(self.page.locator('#workspaceScanProgress').get_attribute('value'))
        self.feedback_capture('oled-scan-starting-1440-dark.png')
        self.page.evaluate('__RESP__.release("scan");__RESP__.scan.checked=4')
        self.page.wait_for_function('document.getElementById("workspaceScanProgress").value===4')
        self.assert_scan_feedback('busy', busy=True, button_state='running', rows=12)
        self.assertEqual(self.page.locator('#workspaceScanProgress').evaluate('e=>[e.value,e.max]'), [4,12])
        self.assertIn('4 / 12', self.page.locator('#workspaceCheckDetail').inner_text())
        self.assertTrue(self.page.locator('#workspaceCancel').is_visible())
        self.page.locator('#railTheme').click();self.page.set_viewport_size({'width':390,'height':844})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 391)
        self.feedback_capture('oled-scan-running-390-light.png')
        self.page.evaluate('__RESP__.scan={...__RESP__.scan,state:"completed",checked:12,queried:12}')
        self.assert_scan_feedback('complete', rows=12)
        self.assertIn('Check complete', self.page.locator('#workspaceCheckStatus').inner_text())
        self.assertFalse(self.page.locator('#unifiedScan').is_disabled())
        self.assertFalse(self.page.locator('#workspaceCancel').is_visible())
        self.assertEqual(self.page.locator('#detectedCurrent').inner_text(), '0')

    def test_failed_partial_cancelled_and_read_only_feedback_never_claim_success(self):
        self.open(count=6, group_count=3, feedback=True)
        self.page.evaluate('__RESP__.failScanStart=true')
        self.page.locator('#unifiedScan').focus();self.page.keyboard.press('Enter')
        self.assert_scan_feedback('error')
        self.assertIn('QA source service unavailable', self.page.locator('#workspaceCheckDetail').inner_text())
        self.assertIn('Check failed', self.page.locator('#workspaceCheckStatus').inner_text())
        self.assertFalse(self.page.locator('#unifiedScan').is_disabled())
        failure_toast = self.page.locator('#toastWrap .toast').filter(has_text='QA source service unavailable')
        self.assertTrue(failure_toast.is_visible())
        self.assertEqual(failure_toast.get_attribute('data-state'), 'error')
        self.feedback_capture('oled-scan-failed-1440-dark.png')
        self.page.evaluate('__RESP__.failScanStart=false')
        self.page.locator('#unifiedScan').click()
        self.page.wait_for_function('document.getElementById("unifiedScan").dataset.state==="running"')
        self.page.evaluate('__RESP__.scan={...__RESP__.scan,state:"partial",checked:4,errors:[{reason:"QA provider unavailable"}]}')
        self.assert_scan_feedback('warning')
        self.assertNotIn('Check complete', self.page.locator('#workspaceCheckStatus').inner_text())
        self.page.locator('#unifiedScan').click()
        self.page.wait_for_function('document.getElementById("unifiedScan").dataset.state==="running"')
        self.page.evaluate('__RESP__.failScanPoll=true')
        self.assert_scan_feedback('warning')
        self.assertNotIn('Check complete', self.page.locator('#workspaceCheckStatus').inner_text())
        self.page.evaluate('__RESP__.failScanPoll=false')
        self.page.locator('#unifiedScan').click()
        self.page.wait_for_function('document.getElementById("unifiedScan").dataset.state==="running"')
        self.page.locator('#workspaceCancel').click()
        self.assert_scan_feedback('cancelled')
        self.assertIn('cancelled', self.page.locator('#workspaceCheckStatus').inner_text().lower())
        self.assertEqual(self.page.evaluate('__RESP__.cancelBody.jobId'), 41)
        self.assertEqual(self.page.evaluate('__RESP__.cancelBody.profileId'), 'profile-a')
        self.assertEqual(self.page.locator('#detectedCurrent').inner_text(), '0')
        self.context.route('http://oled-offline.fixture.test/', lambda route: route.fulfill(content_type='text/html', body=self.html))
        self.page.goto('http://oled-offline.fixture.test/', wait_until='load')
        self.page.wait_for_function('!!window.__ARSENAL_QA__')
        self.assertEqual(self.page.locator('#workspaceDot').get_attribute('data-state'), 'offline')
        self.assertEqual(self.page.locator('#workspaceCheckLine').get_attribute('data-state'), 'offline')
        self.assertIn('Read-only', self.page.locator('#workspaceCheckStatus').inner_text())
        self.assertEqual(self.page.locator('#unifiedScan').inner_text(), 'Connect MO2')
        self.assertEqual(self.page.locator('#unifiedScan').get_attribute('aria-busy'), 'false')
        self.assertFalse(self.page.locator('#workspaceScanProgress').is_visible())

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
