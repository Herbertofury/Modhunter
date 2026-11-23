"""Focused Node execution of the existing exact-modules fixture, not browser timing."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "frontend_runtime.test.cjs"
SCENARIOS = r'''
function normalLibraryLayout(h){
 h.context.document.body.dataset.layout='compact';
 h.context.matchMedia=()=>({matches:false});
 const list=h.nodes.get('modList'),query=list.querySelector;
 list.isConnected=true;
 list.querySelector=selector=>selector===':scope > .mod-row'?{isConnected:true}:query(selector);
 return {list,frames:()=>[...h.timers.values()].filter(timer=>timer.ms==='frame')};
}
test('fresh Gallery entry consumes width before marking rows, with identical same-task UI state',async()=>{
 for(const [count,viewport,width,basis] of [[11207,1028,911,'calc((100% - 42px) / 4)'],[24,390,284,'calc((100% - 10px) / 2)'],[24,1920,1300,'calc((100% - 70px) / 6)']]){
  const h=harness({live:false,count});await h.settle();
  const {list,frames}=normalLibraryLayout(h),read=[],pressed=[],values=[];
  h.context.innerWidth=viewport;
  const gallery=h.nodes.get('libraryGallery');
  Object.defineProperty(list,'clientWidth',{get(){read.push({token:list.dataset.layoutChange,view:h.context.document.body.dataset.libraryView});return width;}});
  gallery.setAttribute=(key,value)=>{if(key==='aria-pressed')pressed.push({value,token:list.dataset.layoutChange});};
  h.context.document.head={append(style){
   const value=style.textContent.match(/flex-basis:([^}]+)/)[1];
   values.push(value);
   style.sheet={cssRules:[{style:{setProperty(name,next){assert.equal(name,'flex-basis');values.push(next);}}}]};
  }};
  gallery.onclick();
  const token=list.dataset.layoutChange;
  assert.ok(token,'Gallery must still enable the original whole-layout transition guard');
  assert.deepEqual(read,[{token:undefined,view:'list'}],'A fresh entry should read the old width once, without flushing newly invalidated row transitions');
  assert.deepEqual(pressed,[{value:'true',token}],'Same-task native control acknowledgement and guard ordering retained');
  assert.deepEqual(values,[basis]);
  assert.equal(h.context.document.body.dataset.libraryView,'gallery');
  assert.equal(h.qa.getCatalog().items.length,count);
  assert.ok(list.innerHTML.includes('row-item-'+(count-1)));
  assert.equal((list.innerHTML.match(/id="row-item-\d+"/g)||[]).length,count);
  assert.equal(frames().length,1);
  // Density changes during the active batch must take the live real width
  // again: this is a single-use sample, not a width/resize cache.
  h.api.updatePrefs({density:'comfy'});
  assert.deepEqual(read.at(-1),{token,view:'gallery'});
  assert.equal(read.length,2);
  // Then restore and no longer block normal hover exactly in the same
  // frame boundary as the current product test.
  await h.tick('frame');
  assert.equal(list.dataset.layoutChange,token);
  await h.tick('frame');
  assert.equal(list.dataset.layoutChange,undefined);
  assert.equal(frames().length,0);
 }
});

'''


@unittest.skipUnless(shutil.which("node"), "Node is required for the Gallery runtime fixture")
class GalleryEntryRuntime(unittest.TestCase):
    def test_fresh_entry_reads_geometry_before_marking_transition_rows(self):
        # Reuse the existing fixture setup, without loading unrelated tests
        # or changing the shared test harness and its held branch.
        source = FIXTURE.read_text(encoding="utf8")
        boundary = "\ntest('release details coalesce and a different profile cannot receive a late result'"
        setup, sep, _ = source.partition(boundary)
        self.assertTrue(sep and "function harness(" in setup, "Shared fixture setup boundary changed")
        run = subprocess.run([shutil.which("node"), "-"], input=setup + "\n" + SCENARIOS,
                             cwd=ROOT / "tests", text=True, encoding="utf8", capture_output=True, timeout=40)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("fresh Gallery entry consumes width", run.stdout)
