"""Chromium interaction tests; native downloads have separate Windows receipts."""
import importlib.util
from pathlib import Path
import unittest
import json

SPEC=importlib.util.spec_from_file_location('watchtower_fixture_tests',Path(__file__).with_name('test_watchtower_browser.py'))
base=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(base)
class AcquisitionUiTests(base.WatchtowerBrowserTests):
    def open_downloads(self, state='pending'):
        snapshot=base.snapshot(1)
        snapshot['acquisitions']={'revision':1,'jobs':[{'id':'download-fixture','filename':'mod.zip','state':state,'received':0,'total':1024,'bytes':0,'binding':{'watchId':'watch-0','sourceOrigin':'https://mods.example.com','sessionId':None},'message':'Ready for explicit approval'}],'offers':[]}
        override="""(() => {
         const original=window.__TAURI__.core.invoke;
         window.__TAURI__.core.invoke=async (command,args)=>{
          if(['approveDownload','rejectDownload','cancelDownload','removeDownload','verifyDownload'].includes(args.kind)){
           const f=window.__WATCH_FIX__;f.calls.push(structuredClone(args));
           if(args.kind==='approveDownload'&&args.value.confirm!=='DOWNLOAD')throw Error('No consent');
           const job=f.state.acquisitions.jobs[0];
           if(args.kind==='approveDownload')job.state='downloading';
           if(['rejectDownload','cancelDownload'].includes(args.kind))job.state='cancelled';
           if(args.kind==='removeDownload')f.state.acquisitions.jobs=[];
           f.state.acquisitions.revision++;f.change(()=>{});
           return {ok:true};
          } return original(command,args);
         };
        })();"""
        html=(base.UI/'watchtower.html').read_text('utf-8')
        html=html.replace('<link rel="stylesheet" href="watchtower.css">','<style>'+(base.UI/'watchtower.css').read_text('utf-8')+'</style>')
        scripts=base.BOOT.replace('__WATCHTOWER_INITIAL_STATE__',json.dumps(snapshot))+override+(base.UI/'watchtower.js').read_text('utf-8')
        html=html.replace('<script src="watchtower.js"></script>','<script>'+scripts+'</script>')
        self.page.set_content(html,wait_until='domcontentloaded')
        self.page.wait_for_function('!document.getElementById("addPage").disabled')
    def test_approval_is_explicit_and_never_installs(self):
        self.open_downloads()
        self.assertEqual(self.calls('approveDownload'),[])
        self.page.locator('#reviewPageDownloads').click()
        self.page.locator('[data-action="download-approve"]').click()
        self.page.wait_for_function("__WATCH_FIX__.state.acquisitions.jobs[0].state === 'downloading'")
        self.assertEqual(self.calls('approveDownload')[0]['value'],{'id':'download-fixture','confirm':'DOWNLOAD'})
        self.assertEqual(self.calls('install'),[])
    def test_cancel_and_remove_are_distinct(self):
        self.open_downloads('downloading')
        self.navigate('downloads')
        self.page.locator('[data-action="download-cancel"]').click()
        self.page.wait_for_function("__WATCH_FIX__.state.acquisitions.jobs[0].state === 'cancelled'")
        self.page.locator('[data-action="download-remove"]').click()
        self.assertEqual(self.calls('removeDownload'),[])
        self.page.locator('#confirmCancel').click()
        self.assertEqual(self.page.locator('.download-row').count(),1)
    def test_stale_transfer_state_does_not_replace_newer_status(self):
        self.open_downloads('ready')
        self.navigate('downloads')
        self.page.evaluate("__WATCH_FIX__.change(s => {s.acquisitions.revision=0;s.acquisitions.jobs[0].state='downloading'})")
        self.assertIn('Ready for review',self.page.locator('.download-row').inner_text())

# Only the new cases run here; the inherited acceptance suite has its own job.
for name in list(base.WatchtowerBrowserTests.__dict__):
    if name.startswith('test_') and name not in AcquisitionUiTests.__dict__:
        setattr(AcquisitionUiTests,name,None)
if __name__=='__main__':unittest.main()
