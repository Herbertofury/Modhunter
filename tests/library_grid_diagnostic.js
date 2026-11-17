/* Diagnostic only. Preserve the authored header as the grid sizing authority. */
(() => {
 'use strict';
 const header=document.querySelector('#libraryViewport > .list-header');
 if(!header||typeof ResizeObserver!=='function'||typeof MutationObserver!=='function')return;
 let rule=null,key='';
 const evidence={reads:0,writes:0,fallbacks:0,tracks:'',gap:''};
 window.__MODHUNTER_GRID_DIAGNOSTIC__=()=>({...evidence});
 function fallback(){
  if(!rule||!key)return;
  rule.style.removeProperty('grid-template-columns');
  rule.style.removeProperty('column-gap');
  key='';evidence.fallbacks++;
 }
 function update(){
  if(document.body.dataset.layout!=='compact'||document.body.dataset.libraryView!=='list'||innerWidth<=820)return;
  const style=getComputedStyle(header);evidence.reads++;
  // A hidden workspace has no used tracks. Keep its last exact sizing until
  // the header is shown again; the media/view selector excludes other views.
  if(style.display!=='grid'||!header.getClientRects().length)return;
  const tracks=style.gridTemplateColumns,gap=style.columnGap;
  if(!/^\d+(?:\.\d+)?px(?: \d+(?:\.\d+)?px)*$/.test(tracks)||!/^\d+(?:\.\d+)?px$/.test(gap)){fallback();return}
  const next=tracks+' / '+gap;
  if(key===next)return;
  try{
   if(!rule){
    const sheet=document.createElement('style');sheet.dataset.libraryGridDiagnostic='';
    const nonce=document.querySelector('style[nonce]')?.nonce;if(nonce)sheet.nonce=nonce;
    sheet.textContent='@media(min-width:821px){body[data-layout="compact"][data-library-view="list"] #modList > .mod-row{grid-template-columns:'+tracks+';column-gap:'+gap+'}}';
    document.head.append(sheet);rule=sheet.sheet.cssRules[0].cssRules[0];
   }else{
    rule.style.setProperty('grid-template-columns',tracks);
    rule.style.setProperty('column-gap',gap);
   }
   key=next;evidence.tracks=tracks;evidence.gap=gap;evidence.writes++;
  }catch{fallback()}
 }
 // Observer delivery is synchronous before the next paint. There are no
 // timers, animation frames, row reads or per-record style mutations here.
 // benchmark() still measures import/actions through its original two frames.
 new ResizeObserver(update).observe(header);
 new MutationObserver(update).observe(document.body,{attributes:true,attributeFilter:[
  'data-layout','data-library-view','data-library-columns','data-library-hidden-columns',
  'data-workspace','data-nav','data-density','data-theme','style'
 ]});
 update();
})();
