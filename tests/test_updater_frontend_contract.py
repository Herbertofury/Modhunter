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
        self.assertIn("function queueWorkshop()", APP)
        self.assertIn("async function performQueuedWorkshop(", APP)
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
        self.assertIn("if(!m.nexusId){$('filePickerFiles').innerHTML=github;filterFilePicker();return}", APP)
        self.assertIn("'<div class=\"picker-source-title\">NEXUS MODS", APP)
        self.assertIn("'<div class=\"picker-source-title\">GITHUB RELEASE", APP)
        self.assertIn("EXACT FILES</div>'+nexus:'')+github", APP)
        self.assertIn("targetVersion:record.latestVersion", APP)
        self.assertIn("String(m.latestVersion||'')===String(choice.targetVersion||'')", APP)
        self.assertIn("data-retry-item-id", APP)
        self.assertIn("data-workshop-act=\"remove\"", APP)
        self.assertIn("WORKSHOP_CHOICES.delete(b.dataset.id)", APP)
        self.assertIn("Choose the file you want; no extra confirmation screens.", APP)

    def test_sss_reviewer_keeps_full_file_list_searchable_and_guided(self):
        for element in ('workshopReviewNext', 'filePickerSearch', 'filePickerMatchCount'):
            with self.subTest(element=element):
                self.assertIn(f'id="{element}"', PAGE)
        for feature in ('function filterFilePicker()', 'function nextWorkshopCandidate()',
                        'function reviewNextWorkshopCandidate()', 'releaseVersionMatch',
                        'reviewAdvice?.hasUniqueMainMatch', 'targetVersion:record.latestVersion',
                        'const ticketsByMod=new Map()'):
            with self.subTest(feature=feature):
                self.assertIn(feature, APP)
        self.assertIn("'files?modId='+m.nexusId+'&itemId='+encodeURIComponent(m.id)", APP)

    def test_mod_names_open_original_site_in_every_mode_and_preserve_mo2_reveal(self):
        for fragment in (
            'class="mod-title source-title',
            'class="mod-name-actions"',
            'class="workshop-item-title',
            'class="activity-mod-link',
            'class="mod-inline-locate"',
            'class="workshop-inline-locate"',
            'data-act="source"',
            'data-act="locate"',
            'data-workshop-act="source"',
            'data-workshop-act="locate"',
            'target="_blank" rel="noopener noreferrer"',
            'Number.isSafeInteger(nexusId)',
            "nexusId>0&&/^[a-z0-9_-]+$/.test(game)",
            "return; // Native anchor behavior",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, APP)
        self.assertNotIn('class="mod-title locate-title"', APP)
        self.assertIn('class="workshop-item-title', APP)
        self.assertIn('title="Reveal this mod in Mod Organizer 2"', APP)

    def test_one_click_updates_need_no_extra_confirmation_while_offering_optional_details(self):
        for element in ("workshopQuickAll", "workshopInspectQueue"):
            with self.subTest(element=element):
                self.assertIn(f'id="{element}"', PAGE)
        for marker in ("function fastUpdateOne(id)", "async function fastUpdateAll()",
                       "bridgeRequest('POST','quick-update'", "if(action==='quick')return fastUpdateAll()",
                       'data-workshop-act="quick"', "if(fast)return performQueuedWorkshop",
                       "function inspectQueue()", "return performQueuedWorkshop(entries,plan.profileId)",
                       "data-download-details", "showInstallPreview(Number(details.dataset.downloadDetails))"):
            with self.subTest(marker=marker):
                self.assertIn(marker, APP)
        self.assertNotIn("Have you reviewed the description, requirements, runtime", APP)
        self.assertNotIn("Compatibility watch:\\n", APP)

    def test_aaa_operations_require_reviewed_queue_and_live_install_preflight(self):
        for marker in ('queueReviewVeil', 'queueReviewRows', 'queueReviewConfirm',
                       'installPreviewVeil', 'installBriefTitle', 'installBriefDetails',
                       'installPreviewConfirm', 'workshopVisibleCount'):
            with self.subTest(marker=marker):
                self.assertIn(f'id="{marker}"', PAGE)
        self.assertIn("install-preview?downloadId=", APP)
        self.assertIn("function showInstallPreview(", APP)
        self.assertIn("function closeInstallPreview()", APP)
        self.assertIn("data-lane", APP)
        self.assertIn("EXACT FILES TO REQUEST", PAGE)

    def test_durable_activity_ui_is_read_only_and_profile_scoped(self):
        for element in ('activityPanel', 'activityTitle', 'activitySearch',
                        'activityFilter', 'activityResults', 'activityExport',
                        'activityRefresh', 'activityCount', 'activityWarning'):
            with self.subTest(element=element):
                self.assertIn(f'id="{element}"', PAGE)
        for marker in ("function renderActivity()", "async function refreshActivity()",
                       "function exportActivity()", "bridgeRequest('GET','activity')",
                       "ACTIVITY_RECORDS=[]", "if(record.needsReview)", "sessionActive"):
            with self.subTest(marker=marker):
                self.assertIn(marker, APP)

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
