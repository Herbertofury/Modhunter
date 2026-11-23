"""Actual Chromium interactions for the native review and maintenance manager.

The bridge here is deterministic. This proves shipped UI behavior and exact
command boundaries; Windows WebView2 and MO2 have separate native receipts.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

SPEC=importlib.util.spec_from_file_location('watchtower_native_ui_base',Path(__file__).with_name('test_watchtower_browser.py'))
base=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(base)
ROOT=base.ROOT
SOURCES=("native/ui/watchtower.html","native/ui/watchtower.css","native/ui/watchtower.js","native/ui/watchtower-review.js",
         "native/ui/watchtower-maintenance.js","native/ui/watchtower-inventory.js","native/ui/watchtower-queue.js","native/ui/watchtower-notices.js","native/ui/watchtower-native.css","native/ui/locale-catalogs.js","native/ui/preferences.js","native/ui/preferences.css",
         "tests/test_watchtower_native_ui.py","tests/test_watchtower_browser.py","tests/fixtures/watchtower_native_fixture.js","tests/prepare_ci_fonts.py")
REQUIRED=("test_review_preserves_complete_selection_and_exact_install_ids",
          "test_new_content_requires_an_explicit_new_target",
          "test_fomod_selection_is_explicitly_advisory",
          "test_review_filters_show_exact_choices_without_changing_the_plan",
          "test_remembered_optional_exclusions_do_not_follow_another_review",
          "test_unknown_native_outcome_remains_visible_and_cannot_be_discarded",
          "test_support_export_uses_the_exact_preview_identity",
          "test_cleanup_requires_a_matching_preview_and_confirmation",
          "test_global_preferences_preserve_unsaved_edits_and_switch_language",
          "test_duplicate_report_preserves_whole_mod_and_file_ownership",
          "test_retained_reviews_remain_visible_without_replay",
          "test_lost_acknowledgement_refreshes_submission_state",
          "test_retention_requires_exact_preview_and_explicit_confirmation",
          "test_application_reset_requires_exact_phrase_and_preview",
          "test_native_substates_update_without_watchstore_revision",
          "test_native_review_fallback_runs_only_for_visible_work",
          "test_native_review_drops_delayed_operations_across_profile_round_trip",
          "test_readiness_identifies_live_and_offline_profiles_without_scanning",
          "test_inventory_caches_complete_results_and_reveals_exact_ids",
          "test_shared_queue_controls_preserve_action_boundaries",
          "test_shared_queue_receipt_is_read_only_and_hides_archive_identifiers",
          "test_shared_queue_clears_rendered_rows_and_rejects_delayed_profile_replies",
          "test_shared_queue_replays_context_delivered_before_overlay_mount",
          "test_four_locales_preserve_names_focus_and_theme",
          "test_notice_list_is_private_and_accepts_native_substate_updates",
          "test_notice_click_uses_only_opaque_id_and_opens_current_native_item",
          "test_stale_notice_rejection_never_opens_a_target")
OVERRIDE=r'''(() => {
 const original=window.__TAURI__.core.invoke,f=window.__WATCH_FIX__;
 f.native={jobs:[],review:null,mode:'plain',savedReports:[],preparedTargets:[]};
 const prefs={theme:'system',locale:'en',privacyPreset:'standard',quickHide:false,captureProtection:false,checkAppUpdates:false,externalPrivate:false,privateBrowser:'edge',retainSignIns:true,clearCacheOnExit:false,backupRetentionDays:30,backupRetentionCount:5,backupRetentionBytes:68719476736};
 const maintenance={settings:prefs,lastChecked:0,release:null,releaseError:''};
 f.state.maintenance=structuredClone(maintenance);
 f.state.notices={rows:[{id:'notice-'+('a'.repeat(64)),kind:'updater',createdAt:1000,seen:false,title:'A version candidate was recorded',body:'Open to recheck and review the exact current item.',actionLabel:'Review current item'},{id:'notice-'+('b'.repeat(64)),kind:'page',createdAt:900,seen:false,title:'A watched page changed',body:'Open to recheck and review the exact current item.',actionLabel:'Review current item'}],status:'Recorded notices are rechecked when you open them.',desktopActionScope:'Windows forwards clicks while the toast is on screen. Notices remain actionable here after the toast expires.'};
 const context={profileId:'profile-1',profileName:'Native review fixture',game:'SkyrimSE',gameRuntime:'1.6.1170',executionEpoch:3,target:{itemId:'mod-1',modName:'Exact existing mod',version:'1.0',exists:true}};
 f.native.queueContext={revision:1,scope:{profileId:'profile-1',executionEpoch:3}};
 const clone=x=>structuredClone(x);
 f.native.switchQueue=(revision,profileId,executionEpoch,name)=>{
  f.native.queueContext={revision,scope:{profileId,executionEpoch}};
  f.native.queue={profileId,executionEpoch,mode:'running',active:{kind:'page',modName:name,state:'installing'},
   pending:[{kind:'page',modName:name,state:'queued'}],history:[{kind:'page',modName:name,state:'complete'}]};
  f.publishInstallQueueContext(f.native.queueContext);
 };
 function finish(value={}){return {ok:true,...clone(value),state:f.snapshot()}}
 window.__TAURI__.core.invoke=async(command,args)=>{
  const kind=args.kind,v=args.value||{};
  const owned=['maintenance','browserChoices','prepareReview','openReview','saveReviewSelection','installReview','contentJobs','discardReview','cancelReview','cancelContentJob','diagnosticsPreview','diagnosticsCopy','diagnosticsSave','globalSettings','storage','previewDownloadCleanup','applyDownloadCleanup','duplicateScan','duplicateRecovery','contentBackups','contentRecovery','contentBatchRestore','checkAppRelease','dismissAppRelease','openAppRelease','previewAppReset','applyAppReset'];
  owned.push('readiness','scanUnattributed','unattributedInventory','cancelUnattributed','revealUnattributed','installQueue','installQueueControl');
  owned.push('notices','refreshNotices','activateNotice','dismissNotice','compareIniSnapshots');
  if(kind==='state')return {...await original(command,args),installQueueContext:clone(f.native.queueContext)};
  if(!owned.includes(kind))return original(command,args);
  f.calls.push(clone(args));
  if(kind==='notices'||kind==='refreshNotices')return finish({notices:f.state.notices});
  if(kind==='activateNotice'||kind==='dismissNotice'){
   if(Object.keys(v).join(',')!=='id')throw Error('Only an opaque native notice identity belongs to the renderer');
   const row=f.state.notices.rows.find(row=>row.id===v.id);if(!row)throw Error('Notice was superseded');
   if(kind==='dismissNotice'){f.state.notices.rows=f.state.notices.rows.filter(row=>row.id!==v.id);return finish()}
   if(f.native.noticesStale)throw Error('Native profile or item evidence changed. Refresh notices.');
   row.seen=true;
   return finish({noticeAction:row.kind==='page'?{kind:'page',watchId:'watch-0',observationId:'observation-0'}:{kind:'updater',itemId:'live-notice-1',profileId:'profile-1',game:'SkyrimSE',itemName:'Private current native mod',installedVersion:'1.0',latestVersion:'2.0',provider:'nexus',checkedAt:1000,sourceUrl:'https://www.nexusmods.com/skyrimspecialedition/mods/12',scope:'A current project-version candidate. Exact file, compatibility and access still require review; nothing was downloaded or installed.'}});
  }
  if(kind==='installQueue'||kind==='installQueueControl'){
   f.native.queue||={...f.native.queueContext.scope,mode:'running',active:{kind:'native',jobId:'api-exact',modName:'API target',state:'installing',nativeStarted:true},pending:[{kind:'page',jobId:'page-exact',modName:'Page target',state:'queued'}],history:[],counts:{pending:1,awaitingDownload:1,active:1}};
   if(kind==='installQueueControl'){if(Object.keys(v).join(',')!=='action')throw Error('Only action belongs to the renderer');f.native.queue.mode=v.action==='resume'?'running':'paused';if(v.action==='cancelPreparation'){f.native.queue.pending=[];f.native.queue.counts.pending=0;f.native.queue.history=[{kind:'page',jobId:'page-exact',modName:'Page target',state:'cancelled',needsReview:false}]}}
   const captured=clone(f.native.queue);
   if(f.native.deferQueue===kind)await new Promise(resolve=>{f.native.resumeQueue=resolve;f.native.deferQueue=null});
   return finish({installQueue:captured,installQueueContext:f.native.queueContext,message:kind==='installQueueControl'?'Queue control applied.':undefined});
  }
  if(kind==='readiness')return finish({readiness:{mode:f.native.offline?'offline':'live',contextKey:f.native.contextKey||'context-fixture',capturedAt:1000,profileId:'profile-1',profileName:'Exact profile',game:'SkyrimSE',gameRuntime:'1.6.1170',endpoint:f.native.offline?null:'http://127.0.0.1:54321/',modsRoot:f.native.offline?null:'D:/Redirected/Mods',profilesRoot:f.native.offline?null:'E:/Profiles',counts:{mods:11207,verifiedSourceMods:11000,unverifiedSourceMods:207,trackedMods:2,observedSignedIn:1,signInOrVerification:1,unobservedSessions:2},mods:Array.from({length:11207},(_,i)=>({id:'mod-'+i,name:'Native mod '+i,sourceVerified:i<11000})),instances:[{profileName:'Exact profile',endpoint:'http://127.0.0.1:54321/'},{profileName:'Separate MO2 instance',endpoint:'http://127.0.0.1:54322/'}],scope:'Observed account sign-in is not proof of access to a particular release.'}});
  if(kind==='unattributedInventory')return finish({inventory:f.native.inventory||null,running:false});
  if(kind==='scanUnattributed'){f.native.inventory={id:'inventory-exact',contextKey:'context-fixture',profileId:'profile-1',profileName:'Captured fixture profile',capturedAt:1000,complete:true,files:1205,bytes:24680,scope:'Read-only mods directory. No Overwrite or game directory scan.',entries:Array.from({length:1205},(_,i)=>({id:'entry-'+i,relativePath:'Unknown/textures/file-'+String(i).padStart(5,'0')+'.dds',modName:'Unknown mod',itemId:'mod-11001',kind:'file',reason:'No verified source in the current catalog.',bytes:20,canReveal:i!==0}))};return finish({inventory:f.native.inventory})}
  if(kind==='revealUnattributed'){if(v.inventoryId!=='inventory-exact'||!/^entry-\d+$/.test(v.entryId))throw Error('Exact inventory identity required');return finish()}
  if(kind==='cancelUnattributed')return finish();
  if(kind==='maintenance')return finish({maintenance});
  if(kind==='browserChoices')return finish({browsers:[{id:'edge',label:'Microsoft Edge'}]});
  if(kind==='globalSettings'){if(f.defer[kind])await new Promise(resolve=>f.pending.push({kind,resolve}));maintenance.settings=clone(v);f.state.maintenance=clone(maintenance);f.publish();return finish({maintenance})}
  if(kind==='prepareReview'){
   f.native.preparedTargets.push(clone(v));const entries=Array.from({length:1205},(_,i)=>({path:'textures/fixture-'+String(i).padStart(5,'0')+'.dds',bytes:i+1,state:i%3===0?'unchanged':'replace',selectable:true,selectedDefault:true,optional:false,renameHints:[]}));
   entries.push({path:'docs/readme.txt',bytes:80,state:'add',selectable:true,selectedDefault:false,optional:true,renameHints:[]},{path:'old/installed-only.nif',bytes:123,state:'notInArchive',selectable:false,selectedDefault:false,removalAuthorized:false,renameHints:[]});
   const target=clone(context);if(v.targetName)target.target={itemId:'',modName:v.targetName,version:'?',exists:false};
   f.native.review={id:'review-1000-1',downloadId:v.downloadId,filename:'Verified fixture.zip',createdAt:Date.now(),context:target,state:'review',submitted:false,selected:entries.filter(e=>e.selectedDefault).map(e=>e.path),entries,requiresInstaller:f.native.mode==='fomod',layout:f.native.mode==='fomod'?'needsReview':'gameData',identical:false,archive:{bytes:1000,sha256:'a'.repeat(64)}};
   f.state.reviews={rows:[clone(f.native.review)],preparing:[]};f.publish();return finish({review:f.native.review});
  }
  if(kind==='openReview')return finish({review:f.native.review});
  if(kind==='saveReviewSelection'){f.native.review.selected=clone(v.selected);return finish({review:f.native.review})}
  if(kind==='installReview'){
   if(f.native.review.submitted)throw Error('Review already submitted; reconcile its result.');
   f.native.review.submitted=true;f.native.review.state=['unknown','lostAck'].includes(f.native.mode)?'unknown':'complete';f.state.reviews.rows=[clone(f.native.review)];
   f.native.jobs=[{jobId:'job-1',reviewId:f.native.review.id,downloadId:f.native.review.downloadId,profileId:'profile-1',itemId:'mod-1',modName:'Exact existing mod',action:'install',state:f.native.review.state,ok:f.native.mode!=='unknown',backupId:'backup-1'}];f.publish();
   if(f.native.mode==='lostAck')throw Error('Connection ended after native acceptance. Refresh operations.');
   return finish({review:f.native.review,operation:f.native.jobs[0],message:'Native operation recorded.'});
  }
  if(kind==='contentJobs'){
   const jobs=clone(f.native.jobs);
   if(f.defer[kind])await new Promise(resolve=>f.pending.push({kind,resolve}));
   return finish({operations:{jobs,nativeAvailable:true,helperAvailable:true}});
  }
  if(kind==='discardReview'){if(f.native.review?.submitted&&['unknown','interrupted'].includes(f.native.review.state))throw Error('Unresolved operation retains this review.');f.state.reviews.rows=[];f.publish();return finish()}
  if(kind==='compareIniSnapshots'){
   if(Object.keys(v).length)throw Error('The UI may request pickers only');
   if(f.defer[kind])await new Promise(resolve=>f.pending.push({kind,resolve}));
   return finish({comparison:f.native.iniComparison??{schemaVersion:1,comparison:'qsettingsIni',orderEvidence:'callerSupplied',profileIdentity:'notVerified',editorAttribution:'unknown',values:'notIncluded',
    byteIdentical:false,effectiveSettingsEqual:false,beforeSettings:4,afterSettings:5,changes:{changed:2,added:2,removed:1},unlabeledChanges:{changed:1,added:1,removed:0},
    labeledChanges:[{setting:'modsDirectory',kind:'changed'},{setting:'gameRuntimeVersion',kind:'added'},{setting:'instanceBaseDirectory',kind:'removed'}]}});
  }
  if(kind==='diagnosticsPreview')return finish({reportId:'report-fixture-exact',text:JSON.stringify({application:'Modhunter',version:f.state.version,redacted:true})+'\n'});
  if(kind==='diagnosticsCopy'||kind==='diagnosticsSave'){if(v.reportId!=='report-fixture-exact')throw Error('Preview changed');f.native.savedReports.push({kind,...clone(v)});return finish({message:'Exact preview exported.'})}
  if(kind==='storage')return finish({storage:{groups:[{id:'downloads',label:'Verified downloads',complete:true,usage:{bytes:1000,files:1}}]}});
  if(kind==='previewDownloadCleanup')return finish({cleanup:{id:'cleanup-fixture-exact',count:1,bytes:1000,scope:'Owned idle downloads only.'}});
  if(kind==='applyDownloadCleanup'){if(v.previewId!=='cleanup-fixture-exact'||v.confirm!=='DELETE DOWNLOADS')throw Error('Wrong cleanup approval');return finish({cleanup:{removed:['download-fixture'],failed:[]}})}
  if(kind==='duplicateScan')return finish({result:{ok:true,state:'complete',report:{digest:'b'.repeat(64),complete:true,completeContentGroups:[{mods:[{name:'Disabled duplicate',enabled:false,profiles:[{id:'Profile A',enabled:false}]},{name:'Retained original',enabled:true,profiles:[{id:'Profile A',enabled:true},{id:'Profile B',enabled:true}]}],files:2,logicalBytesPerMod:100}],fileGroups:[{bytes:50,alreadyHardlinked:true,distinctPhysicalFiles:1,physicalAllocatedBytes:4096,paths:[{modName:'Disabled duplicate',path:'textures/same.dds',allocatedBytes:4096,hardLinks:2,owner:{name:'Disabled duplicate',enabled:false,profiles:[{id:'Profile A',enabled:false}]}},{modName:'Retained original',path:'textures/same.dds',allocatedBytes:4096,hardLinks:2,owner:{name:'Retained original',enabled:true,profiles:[{id:'Profile A',enabled:true},{id:'Profile B',enabled:true}]}}]}]}}});
  if(kind==='contentBackups')return finish({result:{backups:f.native.backups||[]}});
  if(kind==='contentRecovery'&&v.action==='retentionPreview')return finish({result:{ok:true,digest:'c'.repeat(64),eligible:[{id:'backup-old',state:'sealed',bytes:80}],retained:[{id:'backup-new'}],newestBackupRetained:true,incompleteRecordsRetained:true}});
  if(kind==='contentRecovery'&&v.action==='retentionApply'){if(v.expectedDigest!=='c'.repeat(64)||v.confirm!=='DELETE BACKUPS'||v.selected.join(',')!=='backup-old')throw Error('Retention preview changed');return finish({result:{ok:true,deleted:v.selected}})}
  if(kind==='previewAppReset')return finish({reset:{digest:'d'.repeat(64),roots:[{roles:['appData'],path:'C:/Owned/Modhunter',files:4,bytes:200}],scope:'Exact owned folders only.',requiresExit:true}});
  if(kind==='applyAppReset'){if(v.expectedDigest!=='d'.repeat(64)||v.confirm!=='REMOVE MODHUNTER DATA')throw Error('Reset preview changed');return finish({reset:{state:'scheduled',completionReport:'C:/Owned/reset/completion.json',requiresExit:true}})}
  return finish({result:{ok:true,state:'complete'}});
 };
})();'''

class NativeUiTests(base.WatchtowerBrowserTests):
    cjk_font_proof = None

    def record_cjk_font_proof(self):
        # Inspect a real visible heading, not a synthetic span or a canvas width.
        # The pinned font receipt is mandatory and travels in the same artifact.
        expected_text = '外觀、隱私與維護'
        expected_font = {
            'upstreamRepository': 'notofonts/noto-cjk',
            'upstreamCommit': 'f8d157532fbfaeda587e826d4cd5b21a49186f7c',
            'fileName': 'NotoSansCJKtc-Regular.otf',
            'familyName': 'Noto Sans CJK TC',
            'postScriptName': 'NotoSansCJKtc-Regular',
            'bytes': 16435884,
            'sha256': 'dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4',
        }
        proof = {'schema': 1, 'ok': False, 'locale': 'zh-TW',
                 'scope': 'Actual Linux Chromium glyph rendering; Windows WebView2 has separate native evidence.',
                 'selector': '#settingsView .native-workspace:has(> #globalPreferencesForm) > h2',
                 'expectedFont': expected_font, 'expectedGlyphCount': len(expected_text),
                 'browserVersion': self.browser.version}
        type(self).cjk_font_proof = proof
        receipt_bytes = (base.OUTPUT / 'ci-fonts.json').read_bytes()
        receipt = json.loads(receipt_bytes)
        proof['fontReceiptSha256'] = hashlib.sha256(receipt_bytes).hexdigest()
        self.assertIs(receipt.get('ok'), True)
        for key in ('upstreamRepository', 'upstreamCommit'):
            self.assertEqual(receipt.get(key), expected_font[key])
        self.assertEqual(receipt.get('fontFamily'), expected_font['familyName'])
        self.assertEqual(receipt.get('registeredPostScriptName'), expected_font['postScriptName'])
        self.assertIn(expected_font['familyName'], receipt.get('registeredFamily', '').split(','))
        font_records = [row for row in receipt.get('files', [])
                        if row.get('path') == 'Sans/OTF/TraditionalChinese/' + expected_font['fileName']]
        self.assertEqual(len(font_records), 1)
        for key in ('bytes', 'sha256'):
            self.assertEqual(font_records[0].get(key), expected_font[key])
        installed = Path(receipt['registeredFile'])
        self.assertEqual(installed.name, expected_font['fileName'])
        installed_bytes = installed.read_bytes()
        self.assertEqual(len(installed_bytes), expected_font['bytes'])
        self.assertEqual(hashlib.sha256(installed_bytes).hexdigest(), expected_font['sha256'])
        coverage = receipt.get('glyphCoverage', {})
        proof['glyphCoverage'] = coverage
        self.assertEqual(coverage.get('text'), expected_text)
        self.assertEqual(coverage.get('method'), 'fc-query FreeType/fontconfig charset')
        self.assertEqual(coverage.get('glyphs'), [
            {'character': char, 'codepoint': f'U+{ord(char):04X}', 'covered': True}
            for char in dict.fromkeys(expected_text)
        ])
        heading = self.page.locator(proof['selector'])
        self.assertEqual(heading.count(), 1)
        self.assertEqual(heading.inner_text(), expected_text)
        heading.scroll_into_view_if_needed()
        self.page.evaluate('async() => {await document.fonts.ready; await new Promise(requestAnimationFrame)}')
        proof.update(heading.evaluate("""el => {
          const r=el.getBoundingClientRect(),s=getComputedStyle(el);
          const target=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
          return {text:el.textContent.trim(),visible:r.width>0&&r.height>0&&s.visibility==='visible'&&s.display!=='none',
            inViewport:r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,
            receivesPointer:target===el||el.contains(target),
            rect:{x:r.x,y:r.y,width:r.width,height:r.height},computedFontFamily:s.fontFamily};
        }"""))
        self.assertEqual(proof['text'], expected_text)
        self.assertIs(proof['visible'], True)
        self.assertIs(proof['inViewport'], True)
        self.assertIs(proof['receivesPointer'], True)
        session = self.context.new_cdp_session(self.page)
        try:
            session.send('DOM.enable')
            session.send('CSS.enable')
            document = session.send('DOM.getDocument')
            node = session.send('DOM.querySelector', {'nodeId': document['root']['nodeId'],
                                                       'selector': proof['selector']})
            self.assertGreater(node['nodeId'], 0)
            proof['fonts'] = session.send('CSS.getPlatformFontsForNode', {'nodeId': node['nodeId']})['fonts']
        finally:
            session.detach()
        proof['matchedGlyphCount'] = sum(font['glyphCount'] for font in proof['fonts']
            if font.get('familyName') == expected_font['familyName']
            and font.get('postScriptName') == expected_font['postScriptName']
            and font.get('isCustomFont') is False)
        # All eight distinct CJK characters must actually render with the verified
        # face. A family declaration, fallback box or one supported glyph is insufficient.
        self.assertEqual(proof['matchedGlyphCount'], len(expected_text), proof['fonts'])
        self.assertEqual(sum(font['glyphCount'] for font in proof['fonts']), len(expected_text))
        proof['ok'] = True

    def open_native(self,mode='plain',intent='update'):
        state=base.snapshot(1);state['acquisitions']={'revision':1,'jobs':[{'id':'download-fixture','filename':'Verified fixture.zip','state':'ready','bytes':1000,'sha256':'a'*64,'createdAt':1,'binding':{'watchId':'watch-0','watchGeneration':1,'sourceOrigin':'https://mods.example.com','contentIntent':intent,'association':({'profileId':'profile-1','itemId':'mod-1'} if intent=='update' else None)}}],'offers':[]};state['reviews']={'rows':[],'preparing':[]}
        self.context.add_init_script(base.BOOT.replace('__WATCHTOWER_INITIAL_STATE__',json.dumps(state))+OVERRIDE+f';__WATCH_FIX__.native.mode={json.dumps(mode)};')
        self.page.goto('http://watchtower.invalid/watchtower.html',wait_until='networkidle')
        self.page.wait_for_function('Boolean(window.ModhunterWatchtower && document.getElementById("nativeReviewQueue"))')
        self.navigate('downloads')
    def review(self,mode='plain'):
        self.open_native(mode);self.page.locator('[data-native-action="review"]').click();self.page.wait_for_selector('#nativeReviewDialog[open]')
    def test_review_preserves_complete_selection_and_exact_install_ids(self):
        self.review();self.assertEqual(self.page.locator('#nativeReviewFiles tr').count(),100)
        self.assertIn('1207 complete',self.page.locator('#nativeFileSummary').inner_text())
        self.page.locator('#nativeFileSearch').fill('installed-only');self.assertTrue(self.page.locator('#nativeReviewFiles input').is_disabled())
        self.page.locator('#nativeFileSearch').fill('fixture-01204');self.page.locator('#nativeReviewFiles input').uncheck()
        self.page.locator('#nativeSelectionSave').click();self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="saveReviewSelection")')
        selected=self.calls('saveReviewSelection')[-1]['value']['selected'];self.assertEqual(len(selected),1204);self.assertNotIn('textures/fixture-01204.dds',selected);self.assertIn('textures/fixture-00000.dds',selected)
        self.page.locator('#nativeInstallReview').click();self.page.locator('#confirmProceed').click();self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="installReview")')
        payload=self.calls('installReview')[0]['value'];self.assertEqual(payload,{'reviewId':'review-1000-1','confirm':'INSTALL'})
        self.assertTrue(self.page.locator('#nativeInstallReview').is_disabled());self.screenshot('watchtower-file-review.png')
    def test_notice_list_is_private_and_accepts_native_substate_updates(self):
        self.open_native();self.page.locator('#noticeOpen').click();self.page.wait_for_selector('#noticeDialog[open]')
        self.assertEqual(self.page.locator('[data-notice-action]').count(),2)
        public=self.page.locator('#noticeDialog').inner_text()
        self.assertNotIn('Private current native mod',public);self.assertNotIn('nexusmods.com',public)
        revision=self.page.evaluate('ModhunterWatchtower.state().storeRevision')
        self.page.evaluate("__WATCH_FIX__.state.notices.rows.push({id:'notice-'+('c'.repeat(64)),kind:'page',createdAt:2000,seen:false,title:'A watched page changed',actionLabel:'Review current item'});__WATCH_FIX__.publish()")
        self.page.wait_for_function("document.querySelectorAll('[data-notice-action]').length===3")
        self.assertEqual(self.page.evaluate('ModhunterWatchtower.state().storeRevision'),revision)
        self.assertNotIn('Private current native mod',self.page.locator('#noticeList').inner_text())
    def test_notice_click_uses_only_opaque_id_and_opens_current_native_item(self):
        self.open_native();self.page.locator('#noticeOpen').click()
        ident='notice-'+('a'*64);self.page.locator(f'[data-notice-action="{ident}"]').click()
        self.page.wait_for_function("!document.getElementById('noticeItemDetails').hidden")
        self.assertEqual(self.calls('activateNotice')[0]['value'],{'id':ident})
        panel=self.page.locator('#noticeItemDetails');self.assertEqual(panel.get_attribute('data-item-id'),'live-notice-1')
        self.assertEqual(panel.get_attribute('data-profile-id'),'profile-1');self.assertIn('Private current native mod',panel.inner_text());self.assertIn('Installed 1.0',panel.inner_text());self.assertIn('Candidate 2.0',panel.inner_text());self.assertIn('nothing was downloaded or installed',panel.inner_text())
        self.assertEqual(self.calls('installReview'),[]);self.assertEqual(self.calls('openPage'),[]);self.screenshot('watchtower-exact-notice.png')
        self.page.evaluate('__WATCH_FIX__.change(s=>s.settings.hideTitles=true)');self.page.wait_for_function("document.getElementById('noticeItemDetails').hidden");self.assertTrue(panel.is_hidden());self.assertNotIn('Private current native mod',self.page.locator('#noticeDialog').inner_text());self.assertNotIn('nexusmods.com',self.page.locator('#noticeDialog').inner_text())
        self.page.locator(f'[data-notice-action="{ident}"]').click();self.page.wait_for_function("!document.getElementById('noticeItemDetails').hidden");self.assertIn('Verified native updater item',panel.inner_text());self.assertNotIn('Private current native mod',panel.inner_text());self.assertNotIn('nexusmods.com',panel.inner_text())
    def test_stale_notice_rejection_never_opens_a_target(self):
        self.open_native();self.page.evaluate('__WATCH_FIX__.native.noticesStale=true');self.page.locator('#noticeOpen').click()
        ident='notice-'+('a'*64);self.page.locator(f'[data-notice-action="{ident}"]').click()
        self.page.wait_for_function("document.getElementById('noticeStatus').dataset.outcome==='rejected'")
        self.assertTrue(self.page.locator('#noticeItemDetails').is_hidden());self.assertTrue(self.page.locator('#detailDrawer').is_hidden())
        self.assertIn('changed',self.page.locator('#noticeStatus').inner_text());self.assertEqual(len(self.calls('activateNotice')),1)
        self.assertEqual(self.calls('openPage'),[]);self.assertEqual(self.calls('installReview'),[])
    def test_new_content_requires_an_explicit_new_target(self):
        self.open_native(intent='newContent');self.page.locator('[data-native-action="review"]').click();self.assertEqual(self.calls('prepareReview'),[])
        self.page.locator('#nativeNewTargetName').fill('Distinct new variant');self.page.locator('#nativeNewTargetForm button[type=submit]').click();self.page.wait_for_selector('#nativeReviewDialog[open]')
        self.assertEqual(self.calls('prepareReview')[0]['value'],{'downloadId':'download-fixture','targetName':'Distinct new variant'});self.assertIn('Distinct new variant',self.page.locator('#nativeReviewTarget').inner_text())
    def test_fomod_selection_is_explicitly_advisory(self):
        self.review('fomod');text=self.page.locator('#nativeReviewScope').inner_text();self.assertIn('advisory',text);self.assertIn('original verified archive',text)
        self.assertIn('marked for review',self.page.locator('#nativeFileSummary').inner_text())

    def test_review_filters_show_exact_choices_without_changing_the_plan(self):
        self.review()
        view=self.page.locator('#nativeFileView')
        search=self.page.locator('#nativeFileSearch')
        rows=self.page.locator('#nativeReviewFiles tr')
        view.select_option('included')
        search.fill('fixture-01200')
        self.assertEqual(rows.count(),1)
        self.assertIn('fixture-01200.dds',rows.first.inner_text())
        view.select_option('installed')
        self.assertEqual(rows.count(),0)
        search.fill('')
        self.assertEqual(rows.count(),1)
        self.assertIn('old/installed-only.nif',rows.first.inner_text())
        self.assertTrue(rows.first.locator('input').is_disabled())
        view.select_option('included')
        search.fill('fixture-01200')
        rows.first.locator('input').press('Space')
        self.assertEqual(rows.count(),0)
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'nativeFileView')
        view.select_option('excluded')
        self.assertIn('fixture-01200.dds',rows.first.inner_text())
        self.assertEqual(self.calls('saveReviewSelection'),[])
        self.assertEqual(self.calls('installReview'),[])
        self.page.locator('#nativeSelectionSave').click()
        self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="saveReviewSelection")')
        selection=self.calls('saveReviewSelection')[0]['value']
        self.assertEqual(selection['reviewId'],'review-1000-1')
        self.assertNotIn('textures/fixture-01200.dds',selection['selected'])
        self.assertIn('textures/fixture-01204.dds',selection['selected'])
        self.assertEqual(self.calls('installReview'),[])
    def test_remembered_optional_exclusions_do_not_follow_another_review(self):
        self.review()
        dialog=self.page.locator('#nativeReviewDialog')
        remember=dialog.locator('#nativeRememberExclusions')
        remember.check()
        dialog.locator('#nativeFileView').select_option('excluded')
        self.assertTrue(remember.is_checked())
        dialog.locator('#nativeSelectionSave').click()
        self.page.wait_for_function('__WATCH_FIX__.calls.filter(c=>c.kind==="saveReviewSelection").length===1 && !document.getElementById("nativeSelectionSave").disabled')
        self.assertTrue(self.calls('saveReviewSelection')[0]['value']['rememberOptional'])
        dialog.locator('[data-native-close="nativeReviewDialog"]').first.click()
        self.page.evaluate("""() => {
            const f=window.__WATCH_FIX__, previous=f.native.review;
            f.native.review={...previous,id:'review-1000-2',filename:'Second fixture.zip',
                archive:{...previous.archive,sha256:'b'.repeat(64)},
                context:{...previous.context,target:{...previous.context.target,itemId:'mod-2',modName:'Second fixture mod'}},
                selected:[...previous.selected,'docs/readme.txt']};
            f.state.reviews.rows=[f.native.review];f.publish();
        }""")
        self.page.locator('[data-native-action="open-review"][data-review-id="review-1000-2"]').click()
        self.page.wait_for_selector('#nativeReviewDialog[open]')
        dialog.locator('#nativeSelectionSave').click()
        self.page.wait_for_function('__WATCH_FIX__.calls.filter(c=>c.kind==="saveReviewSelection").length===2')
        second=self.calls('saveReviewSelection')[1]['value']
        self.assertEqual(second['reviewId'],'review-1000-2')
        self.assertIn('docs/readme.txt',second['selected'])
        self.assertFalse(second['rememberOptional'])
        self.assertFalse(remember.is_checked())
        self.assertEqual(self.calls('installReview'),[])
        dialog.locator('#nativeInstallReview').click()
        self.page.locator('#confirmProceed').click()
        self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="installReview")')
        self.assertFalse(self.calls('saveReviewSelection')[2]['value']['rememberOptional'])
        self.assertEqual(self.calls('installReview')[0]['value'],{'reviewId':'review-1000-2','confirm':'INSTALL'})

    def test_unknown_native_outcome_remains_visible_and_cannot_be_discarded(self):
        self.review('unknown');self.page.locator('#nativeInstallReview').click();self.page.locator('#confirmProceed').click();self.page.wait_for_function('__WATCH_FIX__.native.review.submitted')
        self.assertTrue(self.page.locator('#nativeInstallReview').is_disabled());self.page.locator('[data-native-close="nativeReviewDialog"]').first.click();self.page.locator('#nativeRefreshJobs').click();self.page.wait_for_selector('[data-native-action="open-review"]')
        self.assertIn('unknown',self.page.locator('#nativeReviewQueue').inner_text());self.assertEqual(self.page.locator('[data-native-action="discard-review"]').count(),0)
    def test_ini_snapshot_review_filters_counts_and_drops_stale_results(self):
        self.open_native()
        self.navigate('settings')
        self.page.locator('#globalIniCompare').click()
        self.page.wait_for_selector('#iniReviewResult:not([hidden])')
        self.assertEqual(self.calls('compareIniSnapshots')[0]['value'],{})
        report=self.page.locator('#iniReviewResult')
        self.assertEqual(report.locator('dd').all_text_contents(),['Unknown','Unknown','Not verified'])
        self.assertEqual(report.locator('#iniReviewRows li').count(),3)
        self.page.locator('#iniReviewFilter').select_option('removed')
        self.assertEqual(report.locator('#iniReviewRows li').all_text_contents(),['Instance base directory · Removed'])
        self.assertEqual(report.locator('#iniReviewCounts tr td').all_text_contents(),['1','0'])
        self.page.evaluate("__WATCH_FIX__.defer.compareIniSnapshots=true")
        self.page.locator('#globalIniCompare').click()
        self.page.wait_for_function("__WATCH_FIX__.pending.some(x=>x.kind==='compareIniSnapshots')")
        self.navigate('history')
        self.navigate('settings')
        self.page.evaluate("__WATCH_FIX__.release('compareIniSnapshots')")
        self.page.wait_for_function("!document.getElementById('globalIniCompare').disabled")
        self.assertTrue(report.is_hidden())
        self.assertEqual(self.page.locator('#iniReviewRows li').count(),0)
        self.assert_explicit_ini_summary_lifetime()

    def assert_explicit_ini_summary_lifetime(self):
        existing_calls = len(self.calls('compareIniSnapshots'))
        compare = self.page.locator('#globalIniCompare')
        keep = self.page.locator('#iniKeepSummary')
        trail = self.page.locator('#iniTrailPanel')
        report = self.page.locator('#iniReviewResult')
        compare.click()
        report.wait_for(state='visible')
        self.assertTrue(trail.is_hidden())
        keep.focus()
        keep.press('Enter')
        self.assertEqual(trail.locator('ol button').all_text_contents(), ['Open comparison 1'])
        self.assertTrue(keep.is_disabled())
        self.assertTrue(trail.get_by_role('button', name='Open comparison 1').evaluate(
            'button => button === document.activeElement'))

        self.page.evaluate("""()=>{__WATCH_FIX__.native.iniComparison={
          schemaVersion:1,comparison:'qsettingsIni',orderEvidence:'callerSupplied',profileIdentity:'notVerified',
          editorAttribution:'unknown',values:'notIncluded',byteIdentical:true,effectiveSettingsEqual:true,
          beforeSettings:4,afterSettings:4,changes:{changed:0,added:0,removed:0},
          unlabeledChanges:{changed:0,added:0,removed:0},labeledChanges:[],
          owner:'PRIVATE_TEST_ONLY',path:'C:/PRIVATE_TEST_ONLY/unrelated.ini'};}""")
        compare.click()
        report.wait_for(state='visible')
        self.assertIn('byte-identical', report.inner_text())
        keep.click()
        self.page.locator('#iniReviewFilter').select_option('changed')
        trail.get_by_role('button', name='Open comparison 1').click()
        self.assertEqual(self.page.locator('#iniReviewFilter').input_value(), 'all')
        self.assertIn('Mods directory · Changed',report.locator('#iniReviewRows li').all_text_contents())
        trail.get_by_role('button', name='Open comparison 2').click()
        self.assertIn('byte-identical',report.inner_text())
        self.assertNotIn('PRIVATE_TEST_ONLY', self.page.locator('#settingsView').inner_text())
        self.assertEqual([x['value'] for x in self.calls('compareIniSnapshots')[existing_calls:]], [{}, {}])

        trail.get_by_role('button', name='Clear kept summaries').click()
        self.assertTrue(trail.is_hidden())
        self.assertTrue(report.is_hidden())
        self.assertTrue(compare.evaluate('button=>document.activeElement===button'))
        compare.click()
        report.wait_for(state='visible')
        keep.click()
        self.page.evaluate("__WATCH_FIX__.defer.compareIniSnapshots=true")
        compare.click()
        self.page.wait_for_function("__WATCH_FIX__.pending.some(x=>x.kind==='compareIniSnapshots')")
        self.assertTrue(trail.get_by_role('button', name='Open comparison 1').is_disabled())
        self.navigate('history')
        self.navigate('settings')
        self.page.evaluate("__WATCH_FIX__.release('compareIniSnapshots')")
        self.page.wait_for_function("!document.getElementById('globalIniCompare').disabled")
        self.assertTrue(trail.is_hidden())
        self.assertTrue(report.is_hidden())
        self.assertEqual(trail.locator('ol button').count(), 0)
        self.assertEqual([x['value'] for x in self.calls('compareIniSnapshots')[existing_calls:]], [{}, {}, {}, {}])

    def test_support_export_uses_the_exact_preview_identity(self):
        self.open_native();self.navigate('settings');self.page.locator('#globalSupport').click();self.page.wait_for_selector('#supportReportDialog[open]');self.assertNotIn('mods.example',self.page.locator('#supportReportText').inner_text())
        self.page.locator('#supportReportCopy').click();self.page.locator('#supportReportSave').click();self.page.wait_for_function('__WATCH_FIX__.native.savedReports.length===2')
        for command in ('diagnosticsCopy','diagnosticsSave'):self.assertEqual(self.calls(command)[0]['value'],{'reportId':'report-fixture-exact'})
        self.screenshot('watchtower-support-preview.png')
    def test_cleanup_requires_a_matching_preview_and_confirmation(self):
        self.open_native();self.navigate('settings');self.assertTrue(self.page.locator('#globalCleanupApply').is_hidden());self.page.locator('#globalCleanupPreview').click();self.page.locator('#globalCleanupApply').click();self.assertEqual(self.calls('applyDownloadCleanup'),[])
        self.page.locator('#confirmProceed').click();self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="applyDownloadCleanup")');self.assertEqual(self.calls('applyDownloadCleanup')[0]['value'],{'previewId':'cleanup-fixture-exact','confirm':'DELETE DOWNLOADS'})
    def test_global_preferences_preserve_unsaved_edits_and_switch_language(self):
        self.open_native();self.navigate('settings');self.page.wait_for_function('document.getElementById("globalTheme").value==="system"');self.page.evaluate('__WATCH_FIX__.defer.globalSettings=true')
        self.page.locator('#globalTheme').select_option('light');self.page.locator('#globalPreferencesSave').click();self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="globalSettings")');self.page.locator('#globalLocale').select_option('es');self.page.evaluate('__WATCH_FIX__.release("globalSettings")');self.page.wait_for_function('document.getElementById("globalPreferencesStatus").textContent.includes("Newer edits")')
        self.assertEqual(self.page.locator('#globalLocale').input_value(),'es');self.page.locator('#globalPreferencesSave').click()
        # The locale attribute changes before the next-frame DOM translation.
        # Await the actual visible control, rather than the scheduling signal.
        self.page.wait_for_function('document.documentElement.lang==="es" && document.getElementById("globalPreferencesSave").textContent==="Guardar preferencias de la app"')
        self.assertEqual(self.page.locator('#globalPreferencesSave').inner_text(),'Guardar preferencias de la app');self.assertEqual(self.page.locator('html').get_attribute('data-theme'),'light')
        self.page.set_viewport_size({'width':780,'height':1000});self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth>innerWidth'));self.screenshot('watchtower-preferences-es.png')
    def test_duplicate_report_preserves_whole_mod_and_file_ownership(self):
        self.open_native();self.page.locator('#nativeOpenDuplicates').click();self.page.wait_for_selector('#nativeDuplicateDialog[open]');self.page.locator('#nativeDuplicateList details summary').first.click();self.page.wait_for_function('document.getElementById("nativeDuplicateList").textContent.includes("Retained original")')
        text=self.page.locator('#nativeDuplicateList details').nth(0).inner_text();self.assertIn('Disabled duplicate',text);self.assertIn('Retained original',text);self.assertIn('Profile B: enabled',text)
        self.page.locator('#nativeDuplicateList details summary').nth(1).click();self.page.wait_for_selector('#nativeDuplicateList details:nth-child(2) pre')
        files=self.page.locator('#nativeDuplicateList details').nth(1).inner_text();self.assertIn('textures/same.dds',files);self.assertIn('Disabled duplicate',files);self.assertIn('Retained original',files);self.assertIn('Profile A: disabled',files);self.assertIn('Profile B: enabled',files);self.assertIn('allocated 4.0 KiB',files);self.assertIn('hard links 2',files)
        self.assertIn('1',self.page.locator('#nativeDuplicateSummary').inner_text());self.screenshot('watchtower-duplicates.png')
    def test_retained_reviews_remain_visible_without_replay(self):
        self.open_native();self.page.evaluate("__WATCH_FIX__.change(s=>s.reviews.rows=[{id:'review-old-1',downloadId:'old',filename:'Retained.zip',state:'unknown',submitted:true,recoveryOnly:true,canDiscard:false},{id:'review-old-2',downloadId:'unused',filename:'Unused.zip',state:'review',submitted:false,recoveryOnly:true,canDiscard:true}])")
        self.page.wait_for_function("document.getElementById('nativeReviewQueue').textContent.includes('Retained.zip')")
        self.assertEqual(self.page.locator('[data-native-action="open-review"]').count(),0);self.assertEqual(self.page.locator('[data-native-action="discard-review"]').count(),1)
        self.assertEqual(self.page.locator('[data-native-action="discard-review"]').get_attribute('data-review-id'),'review-old-2')
    def test_lost_acknowledgement_refreshes_submission_state(self):
        self.review('lostAck');self.page.locator('#nativeInstallReview').click();self.page.locator('#confirmProceed').click()
        self.page.wait_for_function("document.getElementById('nativeReviewError').textContent.includes('native acceptance')")
        self.assertTrue(self.page.locator('#nativeInstallReview').is_disabled());self.assertTrue(self.calls('openReview'));self.assertEqual(len(self.calls('installReview')),1)
    def test_retention_requires_exact_preview_and_explicit_confirmation(self):
        self.open_native();self.page.evaluate("__WATCH_FIX__.native.backups=[{id:'backup-old',itemId:'mod-1',modName:'Exact existing mod',state:'sealed',bytes:80}]")
        self.page.locator('#nativeAllBackups').click();self.page.get_by_role('button',name='Preview retention cleanup',exact=True).click();self.page.wait_for_selector('#nativeRetentionDialog[open]')
        self.assertIn('backup-old',self.page.locator('#nativeRetentionList').inner_text());self.page.locator('#nativeRetentionApply').click()
        self.assertFalse(any(c['value'].get('action')=='retentionApply' for c in self.calls('contentRecovery')))
        self.page.locator('#confirmProceed').click();self.page.wait_for_function("__WATCH_FIX__.calls.some(c=>c.kind==='contentRecovery'&&c.value.action==='retentionApply')")
        request=[c['value'] for c in self.calls('contentRecovery') if c['value'].get('action')=='retentionApply'][0]
        self.assertEqual(request,{'itemId':'mod-1','retentionDays':30,'retentionCount':5,'retentionBytes':68719476736,'action':'retentionApply','expectedDigest':'c'*64,'selected':['backup-old'],'confirm':'DELETE BACKUPS'})
    def test_application_reset_requires_exact_phrase_and_preview(self):
        self.open_native();self.navigate('settings');self.page.locator('#globalResetPreview').click();self.page.wait_for_selector('#applicationResetDialog[open]')
        self.page.locator('#applicationResetConfirmation').fill('remove data');self.page.locator('#applicationResetSubmit').click();self.assertEqual(self.calls('applyAppReset'),[])
        self.page.locator('#applicationResetConfirmation').fill('REMOVE MODHUNTER DATA');self.page.locator('#applicationResetSubmit').click();self.page.wait_for_function("__WATCH_FIX__.calls.some(c=>c.kind==='applyAppReset')")
        self.assertEqual(self.calls('applyAppReset')[0]['value'],{'expectedDigest':'d'*64,'confirm':'REMOVE MODHUNTER DATA'})


    def test_native_review_fallback_runs_only_for_visible_work(self):
        self.page.clock.install()
        self.open_native()
        self.page.clock.run_for(10001)
        self.assertEqual(self.calls('contentJobs'),[])
        self.navigate('pages')
        self.page.evaluate("""() => {
          const f=__WATCH_FIX__,job={jobId:'visible-job',reviewId:'external-review',downloadId:'external-archive',
            profileId:'profile-1',action:'install',modName:'External active operation',state:'installing'};
          f.native.pendingNativeEnumeration=job;
          f.change(s=>{s.reviews.rows=[{id:job.reviewId,jobId:job.jobId,downloadId:job.downloadId,
            context:{profileId:job.profileId},filename:'External active fixture.zip',state:'installing',submitted:true}];});
          f.native.activeReviewSnapshot=f.snapshot();
        }""")
        self.page.clock.run_for(6001)
        self.assertEqual(self.calls('contentJobs'),[])
        self.navigate('downloads')
        self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="contentJobs")')
        self.assertNotIn('External active operation',self.page.locator('#nativeJobs').inner_text())
        self.page.evaluate('__WATCH_FIX__.native.jobs=[__WATCH_FIX__.native.pendingNativeEnumeration]')
        self.page.clock.run_for(2100)
        self.page.wait_for_function('document.getElementById("nativeJobs").textContent.includes("External active operation")')
        count=len(self.calls('contentJobs'))
        self.page.evaluate("""() => {
          Object.defineProperty(document,'hidden',{configurable:true,value:true});
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        self.page.clock.run_for(6001)
        self.assertEqual(len(self.calls('contentJobs')),count)
        self.page.evaluate("""() => {
          delete document.hidden;
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        self.page.wait_for_function('count=>__WATCH_FIX__.calls.filter(c=>c.kind==="contentJobs").length>count',arg=count)
        self.page.evaluate('__WATCH_FIX__.native.jobs[0].state="cancelled"')
        self.page.clock.run_for(2100)
        self.assertIn('cancelled',self.page.locator('#nativeJobs').inner_text())
        stopped=len(self.calls('contentJobs'))
        self.page.evaluate('__WATCH_FIX__.publish(__WATCH_FIX__.native.activeReviewSnapshot)')
        self.page.clock.run_for(6001)
        self.assertEqual(len(self.calls('contentJobs')),stopped)
        self.page.locator('#nativeRefreshJobs').click()
        self.page.wait_for_function('count=>__WATCH_FIX__.calls.filter(c=>c.kind==="contentJobs").length===count+1',arg=stopped)
        self.assertIn('cancelled',self.page.locator('#nativeJobs').inner_text())

    def test_native_review_drops_delayed_operations_across_profile_round_trip(self):
        self.open_native()
        self.page.evaluate("""() => {
          const f=__WATCH_FIX__;
          f.native.receipt=(profileId,modName)=>({jobId:'same-job',reviewId:'same-review',downloadId:'same-archive',
            profileId,modName,state:'complete',action:'install'});
          f.native.jobs=[f.native.receipt('profile-1','Private earlier native result')];
          f.defer.contentJobs=true;
        }""")
        refresh=self.page.locator('#nativeRefreshJobs')
        refresh.click()
        self.page.wait_for_function('__WATCH_FIX__.pending.some(p=>p.kind==="contentJobs")')
        self.page.evaluate("""() => {
          const f=__WATCH_FIX__;
          f.native.switchQueue(2,'profile-2',4,'Unrelated queue target');
          f.native.switchQueue(3,'profile-1',5,'Current queue target');
          f.native.jobs=[f.native.receipt('profile-2','Private wrong-profile result'),
            f.native.receipt('profile-1','Exact current native result')];
        }""")
        refresh.click()
        self.assertEqual(len(self.calls('contentJobs')),1)
        self.assertNotIn('Private',self.page.locator('#nativeJobs').inner_text())
        self.page.evaluate('__WATCH_FIX__.release("contentJobs")')
        self.page.wait_for_function('document.getElementById("nativeJobs").textContent.includes("Exact current native result")')
        self.assertNotIn('Private',self.page.locator('#nativeJobs').inner_text())
        self.assertEqual(len(self.calls('contentJobs')),2)

    def test_readiness_identifies_live_and_offline_profiles_without_scanning(self):
        self.open_native();self.navigate('settings');self.page.locator('#readinessRefresh').click()
        self.page.wait_for_function("document.getElementById('readinessCounts').textContent.includes('11207')")
        self.assertIn('Exact profile',self.page.locator('#readinessFacts').inner_text());self.assertIn('1.6.1170',self.page.locator('#readinessFacts').inner_text())
        self.assertIn('D:/Redirected/Mods',self.page.locator('#readinessFacts').inner_text())
        self.page.locator('#readinessInstances > summary').click();self.assertTrue(self.page.locator('#readinessInstanceList').is_visible())
        self.assertIn('Separate MO2 instance',self.page.locator('#readinessInstanceList').inner_text())
        self.assertEqual(self.calls('scanUnattributed'),[]);self.assertIn('not proof',self.page.locator('#readinessScope').inner_text())
        self.page.evaluate('__WATCH_FIX__.native.offline=true');self.page.locator('#readinessRefresh').click();self.page.wait_for_function("document.getElementById('readinessStatus').textContent.includes('Read-only')")
        self.page.locator('#inventoryOpen').click();self.page.wait_for_selector('#unattributedDialog[open]');self.assertTrue(self.page.locator('#inventoryScan').is_disabled());self.assertEqual(self.calls('scanUnattributed'),[])

    def test_inventory_caches_complete_results_and_reveals_exact_ids(self):
        self.open_native();self.navigate('settings');self.page.locator('#inventoryOpen').click();self.page.wait_for_selector('#unattributedDialog[open]')
        self.page.wait_for_function('!document.getElementById("inventoryScan").disabled');self.assertEqual(self.calls('scanUnattributed'),[])
        self.assertLessEqual(self.page.locator('#inventoryScope option').count(),101)
        self.page.locator('#inventoryModSearch').fill('Native mod 11001');self.page.locator('#inventoryScope').select_option('mod-11001');self.page.locator('#inventoryScan').click()
        self.page.wait_for_function('document.querySelectorAll("#inventoryRows tr").length===100');self.assertEqual(self.calls('scanUnattributed')[0]['value'],{'itemId':'mod-11001'})
        self.page.locator('#inventorySearch').fill('file-01204');self.page.locator('#inventoryRows button').click();self.page.wait_for_function('__WATCH_FIX__.calls.some(c=>c.kind==="revealUnattributed")')
        self.assertEqual(self.calls('revealUnattributed')[0]['value'],{'inventoryId':'inventory-exact','entryId':'entry-1204'})
        self.page.locator('#inventoryDone').click();self.page.locator('#inventoryOpen').click();self.page.wait_for_function('!document.querySelector("#inventoryRows button").disabled');self.assertEqual(len(self.calls('scanUnattributed')),1)
        self.page.locator('#inventorySearch').fill('file-00000');self.assertTrue(self.page.locator('#inventoryRows button').is_disabled())
        self.page.evaluate('__WATCH_FIX__.change(s=>s.settings.hideTitles=true)');self.assertNotIn('Unknown/textures',self.page.locator('#inventoryRows').inner_text());self.screenshot('watchtower-file-inventory.png')
        self.page.locator('#inventoryDone').click();self.page.evaluate('__WATCH_FIX__.native.contextKey="changed-native-context"');self.page.locator('#inventoryOpen').click();self.page.wait_for_function('document.getElementById("inventoryStale").textContent.includes("earlier native context")');self.page.locator('#inventorySearch').fill('file-01204');self.assertTrue(self.page.locator('#inventoryRows button').is_disabled());self.assertEqual(len(self.calls('scanUnattributed')),1)

    def test_shared_queue_controls_preserve_action_boundaries(self):
        self.open_native();self.page.wait_for_function('!document.getElementById("installQueueStop").disabled')
        self.page.locator('#installQueueStop').click();self.page.wait_for_function('!document.getElementById("installQueueResume").disabled')
        self.assertEqual(self.calls('installQueueControl')[0]['value'],{'action':'stopAfterCurrent'})
        self.assertIn('Page target',self.page.locator('#installQueuePending').inner_text());self.assertIn('Native installer active',self.page.locator('#installQueueStatus').inner_text())
        self.page.locator('#installQueueCancel').click();self.assertEqual(len(self.calls('installQueueControl')),1)
        self.page.locator('#confirmProceed').click();self.page.wait_for_function('__WATCH_FIX__.native.queue.pending.length===0')
        self.page.locator('#installQueueResume').click();self.page.wait_for_function('__WATCH_FIX__.native.queue.mode==="running"')
        self.assertEqual([x['value'] for x in self.calls('installQueueControl')],[{'action':'stopAfterCurrent'},{'action':'cancelPreparation'},{'action':'resume'}])
        self.assertEqual(self.page.evaluate('__WATCH_FIX__.native.queue.active.jobId'),'api-exact')
        self.page.get_by_text('Recent queue outcomes',exact=True).click();self.assertTrue(self.page.locator('#installQueueHistory').is_visible())
        self.assertIn('cancelled',self.page.locator('#installQueueHistory').inner_text())

    def test_shared_queue_replays_context_delivered_before_overlay_mount(self):
        # Hold the real queue module until an actual startup reply has been
        # forwarded by Watchtower, independently of script download speed.
        self.page.add_init_script("""window.addEventListener('modhunter-install-queue-context',event=>{
          if (event.detail?.scope?.profileId==='profile-1')window.__initialQueueScopeForwarded=true;
        });""")
        source=(ROOT/"native/ui/watchtower-queue.js").read_text("utf-8")
        wrapper="""(() => {
          const mount=()=>{
            if(!window.__initialQueueScopeForwarded)throw Error('Queue mounted before startup scope');
            window.__queueMountedAfterStartupScope=true;
        """+source+"""
          };
          const schedule=()=>queueMicrotask(mount);
          if(window.__initialQueueScopeForwarded)schedule();
          else window.addEventListener('modhunter-install-queue-context',schedule,{once:true});
        })();"""
        self.page.route("**/watchtower-queue.js",lambda route: route.fulfill(body=wrapper,content_type="text/javascript"))
        self.open_native()
        self.page.wait_for_function("""window.__queueMountedAfterStartupScope &&
          document.getElementById('installQueueStatus').textContent.includes('API target')""")
        self.assertIn('Page target',self.page.locator('#installQueuePending').inner_text())
        self.assertEqual([call['value'] for call in self.calls('installQueue')],[{}])
        self.assertEqual(self.calls('installQueueControl'),[])

    def test_shared_queue_clears_rendered_rows_and_rejects_delayed_profile_replies(self):
        self.open_native()
        self.page.wait_for_function('document.getElementById("installQueueStatus").textContent.includes("API target")')
        self.page.get_by_text('Recent queue outcomes',exact=True).click()
        self.page.evaluate('__WATCH_FIX__.native.deferQueue="installQueue"')
        self.page.locator('#installQueueRefresh').click()
        self.page.wait_for_function('Boolean(__WATCH_FIX__.native.resumeQueue)')
        cleared=self.page.evaluate("""() => {
          const f=__WATCH_FIX__, rows=()=>['Status','Pending','History'].map(k=>document.getElementById('installQueue'+k).textContent).join(' ');
          f.native.switchQueue(2,'profile-2',4,'Private B');
          const whileB=rows();
          f.native.switchQueue(3,'profile-1',5,'Private current A');
          const whileA=rows();
          f.publishInstallQueueContext({revision:2,scope:{profileId:'profile-2',executionEpoch:4}});
          f.native.resumeQueue();f.native.resumeQueue=null;
          return [whileA,whileB];
        }""")
        for view in cleared:
            self.assertNotIn('API target',view)
            self.assertNotIn('Page target',view)
            self.assertNotIn('Private',view)
        self.page.wait_for_function('document.getElementById("installQueueHistory").textContent.includes("Private current A")')
        self.page.evaluate('__WATCH_FIX__.native.deferQueue="installQueueControl"')
        self.page.locator('#installQueueStop').click()
        self.page.wait_for_function('Boolean(__WATCH_FIX__.native.resumeQueue)')
        self.page.evaluate('__WATCH_FIX__.native.switchQueue(4,"profile-2",6,"Private current B")')
        self.page.wait_for_function('document.getElementById("installQueueHistory").textContent.includes("Private current B")')
        self.page.evaluate('__WATCH_FIX__.native.resumeQueue();__WATCH_FIX__.native.resumeQueue=null')
        self.page.wait_for_function('!document.getElementById("installQueueStop").disabled')
        self.assertNotIn('Private current A',self.page.locator('#installQueueHistory').inner_text())
        self.assertEqual(self.calls('installQueueControl')[-1]['value'],{'action':'stopAfterCurrent'})
        self.navigate('pages')
        previous=len(self.calls('installQueue'))
        self.page.evaluate('__WATCH_FIX__.native.switchQueue(5,"profile-1",7,"Private hidden A")')
        self.assertEqual(self.page.locator('#installQueueHistory').text_content(),'')
        self.assertEqual(len(self.calls('installQueue')),previous)
        self.navigate('downloads')
        self.page.wait_for_function('document.getElementById("installQueueHistory").textContent.includes("Private hidden A")')

    def test_shared_queue_receipt_is_read_only_and_hides_archive_identifiers(self):
        self.open_native()
        self.page.wait_for_function('Boolean(__WATCH_FIX__.native.queue)')
        self.page.evaluate("""() => {
          const queue=__WATCH_FIX__.native.queue;queue.active=null;queue.activeInOtherProfile=true;
          queue.history=[
            {kind:'native',modName:'Exact queue target',state:'complete',sourceArchive:{state:'reviewedApiSelection',
             provider:'nexus',filename:'Exact fixture.zip',modId:19,fileId:43},fomodChoices:'unknown'},
            {kind:'page',modName:'Exact page target',state:'complete',sourceArchive:{state:'verifiedOriginalPage',
             bytes:42,sha256:'abcdef12'.repeat(8)}},
            {kind:'page',modName:'Legacy target',state:'failed',error:'Private fixture diagnostic'}];
        }""")
        self.page.locator('#installQueueRefresh').click()
        self.page.wait_for_function('document.getElementById("installQueueStatus").textContent.includes("active in another profile")')
        self.page.get_by_text('Recent queue outcomes',exact=True).click()
        history=self.page.locator('#installQueueHistory')
        visible=history.inner_text()
        for evidence in ('Exact fixture.zip','Nexus project 19 / file 43','42 bytes','abcdef12'*8,
                         'Archive evidence unavailable','FOMOD choices not captured'):
            self.assertIn(evidence,visible)
        self.assertEqual(self.calls('installQueueControl'),[])
        self.page.evaluate('__WATCH_FIX__.change(s=>s.settings.hideTitles=true)')
        hidden=history.inner_text()
        for private in ('Exact fixture.zip','Exact queue target','Exact page target','Nexus project 19',
                        'abcdef12'*8,'Private fixture diagnostic'):
            self.assertNotIn(private,hidden)
        self.assertIn('Archive identifiers hidden',hidden)
        self.assertIn('FOMOD choices not captured',hidden)

    def test_four_locales_preserve_names_focus_and_theme(self):
        self.open_native();self.navigate('settings');self.page.get_by_text('Recovery snapshot retention',exact=True).click()
        self.assertTrue(self.page.locator('#globalBackupDays').is_visible());self.page.locator('#globalBackupDays').click()
        self.assertEqual(self.page.evaluate('document.activeElement.id'),'globalBackupDays')
        self.page.evaluate('''() => {
          const probe=document.createElement('div');probe.id='localeDisplayProbe';probe.hidden=true;
          probe.innerHTML='<button id="localeProviderBadge" data-provider="lookup" data-source-id="opaque-source-fixture" data-provider-label="Source needed"></button><span id="localeAuthorName" data-no-translate>Source needed</span><span id="localeAuthorFallback">Source needed</span>';
          document.body.append(probe);
        }''')
        for locale,label,provider in [('es','Guardar preferencias de la app','Se necesita una fuente'),('it','Salva preferenze dell’app','Fonte necessaria'),('zh-TW','儲存應用程式設定','需要來源'),('en','Save app preferences','Source needed')]:
            self.page.evaluate('(locale)=>ModhunterI18n.apply({locale,theme:"light"})',locale)
            self.page.wait_for_function('(label)=>document.getElementById("globalPreferencesSave").textContent===label',arg=label)
            self.page.wait_for_function('(label)=>document.getElementById("localeProviderBadge").getAttribute("data-provider-label")===label && document.getElementById("localeAuthorFallback").textContent===label',arg=provider)
            self.assertEqual(self.page.locator('#localeProviderBadge').get_attribute('data-provider'),'lookup')
            self.assertEqual(self.page.locator('#localeProviderBadge').get_attribute('data-source-id'),'opaque-source-fixture')
            self.assertEqual(self.page.locator('#localeAuthorName').text_content(),'Source needed')
            self.assertEqual(self.page.evaluate('document.activeElement.id'),'globalBackupDays')
            self.assertEqual(self.page.locator('html').get_attribute('data-theme'),'light')
            self.assertEqual(self.page.evaluate('ModhunterI18n.translate("{count} files",{count:1205}).includes("1205")'),True)
        self.page.evaluate('ModhunterI18n.apply({locale:"es"})')
        self.page.wait_for_function('document.getElementById("localeProviderBadge").getAttribute("data-provider-label")==="Se necesita una fuente"')
        mutation=self.page.evaluate('''async() => {
          const badge=document.getElementById('localeProviderBadge');let mutations=0;
          const observer=new MutationObserver(records=>{mutations+=records.length});observer.observe(badge,{attributes:true,attributeFilter:['data-provider-label']});
          badge.setAttribute('data-provider-label','Original provider');
          const settle=async()=>{for(let i=0;i<3;i++)await new Promise(requestAnimationFrame)};
          await settle();const settled=mutations;await settle();observer.disconnect();
          return {settled,mutations,label:badge.getAttribute('data-provider-label')};
        }''')
        self.assertEqual(mutation,{'settled':2,'mutations':2,'label':'Proveedor original'})
        for locale,label in [('it','Servizio originale'),('en','Original provider')]:
            self.page.evaluate('(locale)=>ModhunterI18n.apply({locale})',locale)
            self.page.wait_for_function('(label)=>document.getElementById("localeProviderBadge").getAttribute("data-provider-label")===label',arg=label)
        self.page.evaluate('document.getElementById("localeDisplayProbe").remove()')
        self.page.evaluate('async()=>{await ModhunterWatchtower.command("globalSettings",{...ModhunterWatchtower.state().maintenance.settings,locale:"zh-TW",theme:"system"})}');self.page.emulate_media(color_scheme='dark',forced_colors='active')
        self.page.wait_for_function('document.documentElement.dataset.theme==="dark" && document.getElementById("globalPreferencesSave").textContent==="儲存應用程式設定"');self.assertEqual(self.page.locator('#globalBackupDays').input_value(),'30')
        self.page.emulate_media(forced_colors='none');self.record_cjk_font_proof();self.page.locator('#globalTheme').scroll_into_view_if_needed();self.screenshot('watchtower-preferences-zh.png')
        self.page.evaluate('__WATCH_FIX__.change(s=>{s.watches[0].title="Storage";s.watches[0].notes="Preferences"})');self.navigate('pages');self.assertEqual(self.page.locator('.page-name').first.inner_text(),'Storage');self.page.locator('.page-name').first.click();self.page.wait_for_function('document.getElementById("detailTitle").textContent==="Storage"');self.assertIn('Preferences',self.page.locator('#detailBody [data-no-translate]').all_text_contents())
        self.page.evaluate('ModhunterWatchtower.showToast("OS diagnostic 12345",null,"Undo",true)');self.page.wait_for_function('document.getElementById("toastText").textContent.includes("技術詳細資料")');self.assertIn('OS diagnostic 12345',self.page.locator('#toastText').inner_text())

    def test_native_substates_update_without_watchstore_revision(self):
        self.open_native()
        persisted=self.page.evaluate('__WATCH_FIX__.state.revision')
        self.page.locator('[data-native-action="review"]').click();self.page.wait_for_selector('#nativeReviewDialog[open]')
        self.assertEqual(self.page.evaluate('__WATCH_FIX__.state.revision'),persisted)
        self.page.locator('[data-native-close="nativeReviewDialog"]').first.click()
        self.page.evaluate("""() => {
          const f=__WATCH_FIX__,old=f.snapshot();
          f.state.reviews.rows[0].state='unknown';f.state.reviews.rows[0].submitted=true;
          f.state.maintenance.releaseError='Newest native maintenance result';
          f.publish();f.publish(old);
        }""")
        # Inspect the accepted state before another command can replace it with
        # a fresh snapshot and hide a stale-event regression.
        current=self.page.evaluate('ModhunterWatchtower.state()')
        self.assertEqual(current['storeRevision'],persisted)
        self.assertEqual(current['reviews']['rows'][0]['state'],'unknown')
        self.assertEqual(current['maintenance']['releaseError'],'Newest native maintenance result')
        self.page.wait_for_function("document.getElementById('nativeReviewQueue').textContent.includes('unknown')")
        self.assertIn('Verified fixture.zip',self.page.locator('#nativeReviewQueue').inner_text())
        self.assertEqual(self.page.locator('[data-native-action="discard-review"]').count(),0)


def main():
    suite=unittest.TestSuite(NativeUiTests(name) for name in REQUIRED)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    # Keep the versioned release-receipt interaction list and count intact.
    # Also exercise the INI browser scenario, which unittest discovery sees
    # but the executable's explicit release test list does not enumerate.
    additional=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        NativeUiTests('test_ini_snapshot_review_filters_counts_and_drops_stale_results')
    ]))
    ini_ok=additional.testsRun==1 and additional.wasSuccessful() and not additional.skipped
    checks=[{'name':name.removeprefix('test_'),'passed':result.testsRun>0 and not result.skipped and not any(getattr(test,'_testMethodName','')==name for test,_ in result.errors+result.failures)} for name in REQUIRED]
    proof={'schema':1,'kind':'native-manager-ui','ok':result.wasSuccessful() and not result.skipped and ini_ok and bool(NativeUiTests.cjk_font_proof and NativeUiTests.cjk_font_proof.get('ok')),'realChromium':not bool(result.skipped),'scope':'Deterministic native-command bridge fixture. Separate receipts prove Windows WebView2 and real MO2.',
           'cjkFontProof':NativeUiTests.cjk_font_proof,
           'candidateCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'sourceDirty':bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=ROOT)),
           'sourceFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES},'checks':checks,'testsRun':result.testsRun,'skipped':len(result.skipped)}
    base.OUTPUT.mkdir(parents=True,exist_ok=True);(base.OUTPUT/'native-manager-ui.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    return 0 if proof['ok'] else 1
if __name__=='__main__':sys.exit(main())
