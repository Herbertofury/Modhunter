(function anonymous(phase,count
) {
'use strict';
// Geometry equivalence witnesses for the actual fully rendered Gallery.
// This never virtualizes, hides, clips, detaches, or defers any node.
const reading=['font','letterSpacing','wordSpacing','textTransform','fontFeatureSettings','fontVariationSettings',
 'paddingInlineStart','paddingInlineEnd','borderInlineStartWidth','borderInlineEndWidth','marginInlineStart',
 'marginInlineEnd','minInlineSize','maxInlineSize','boxSizing','whiteSpace','appearance','direction'];
const controls=['files','favorite','inspect'];
const px=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0&&x<=4096;
const safe=x=>typeof x==='string'&&x.length<=1024;
function shape(x){
 if(!x||!px(x.inline)||x.inline<=0||!safe(x.state)||!Array.isArray(x.buttons)||
  ![2,3].includes(x.buttons.length)||x.buttons.some((c,i)=>
   !c||c.kind!==controls[3-x.buttons.length+i]||!safe(c.label)||!safe(c.classes)||
   !Array.isArray(c.metrics)||c.metrics.length!==reading.length||!c.metrics.every(safe)))return null;
 // Exact tuple: numerical width, variant and control font/layout properties.
 // Each encoded field is data, never concatenated into a stylesheet selector.
 return JSON.stringify([x.inline,x.state,x.buttons.map(b=>[b.kind,b.label,b.classes,b.metrics])]);
}
function admitGroupSet(rows,owner){
 if(!owner||!Array.isArray(rows)||!rows.length||rows.length>20000)return null;
 // Keep a separate record of disagreements. One shape claiming two native
 // sizes fails the whole plan; do not average or discard inconvenient rows.
 const widths=new Map(),ids=new Set(),assignments=[];
 for(const row of rows){
  if(typeof row.id!=='string'||!row.id||ids.has(row.id)||!px(row.right)||!px(row.left))return null;
  ids.add(row.id);
  const key=shape(row);if(!key)return null;
  const known=widths.get(key);
  if(known){
   if(!Object.is(known.px,row.right))return null;
   assignments.push(known.token);
  }else{
   if(widths.size===128)return null;
   const entry=Object.freeze({token:widths.size,px:row.right});
   widths.set(key,entry);assignments.push(entry.token);
  }
 }
 return Object.freeze({owner,rows,assignments:Object.freeze(assignments),rules:Object.freeze([...widths.values()])});
}
function lookupOwnedRules(plan,owner,rows){
 return plan?.owner===owner&&plan.rows===rows?plan.rules:null;
}


return (function(phase,count){
  if(!window.__ARSENAL_QA__||window.__ARSENAL_QA__.live!==false||
     window.__ARSENAL_QA__.getCatalog().items.length!==count)
   throw Error('Expected exact owned offline test catalog');
  const resultKey='__modhunterTrackProof';
  if(phase==='audit'){if(!window[resultKey])throw Error('test not armed');return window[resultKey].audit();}
  if(phase==='restore'){const p=window[resultKey];if(!p)return false;p.restore();delete window[resultKey];return true;}
  if(phase!=='apply'||window[resultKey])throw Error('unknown or duplicate proof');
  const list=document.getElementById('modList'),body=document.body;
  if(!list||body.dataset.libraryView!=='gallery'||body.dataset.layout!=='compact'||
     document.fonts.status!=='loaded')throw Error('stable complete Gallery required');
  const actual=[...list.querySelectorAll(':scope > .mod-row')];
  if(actual.length!==count)throw Error('incomplete owned card records');
  const asPx=s=>/^\d+(?:\.\d+)?px$/.test(s)?Number(s.slice(0,-2)):NaN;
  const rect=e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height];};
  const fontSet=document.fonts,faces=[...fontSet],epoch={changed:false};
  const markChange=()=>{epoch.changed=true;};
  const fontEvents=['loading','loadingdone','loadingerror'];
  const boundary=[innerWidth,innerHeight,devicePixelRatio,document.documentElement.lang,body.dataset.density,
     body.dataset.theme,body.dataset.galleryGrouping,fontSet.status];
  const owner=Object.freeze({});
  const buttonMetrics=button=>{
   const styles=getComputedStyle(button);
   return Object.freeze({kind:button.dataset.act,label:button.textContent,classes:button.className,
    metrics:Object.freeze(reading.map(prop=>styles[prop]))});
  };
  const observe=readButtons=>actual.map(row=>{
   const style=getComputedStyle(row),[left,right,...others]=style.gridTemplateColumns.trim().split(/\s+/);
   const footer=row.querySelector(':scope > .inspect-col');
   if(!footer||others.length||style.contentVisibility!=='visible'||style.display!=='grid')
    throw Error('no complete original grid');
   const children=[...footer.children],footerStyle=getComputedStyle(footer),footerBox=rect(footer);
   if(children.some(b=>b.tagName!=='BUTTON')||footerStyle.gridColumnStart!=='2'||footerStyle.gridColumnEnd!=='auto'||
      !['auto','normal','stretch'].includes(footerStyle.justifySelf)||footerStyle.marginInlineStart!=='0px'||footerStyle.marginInlineEnd!=='0px'||
      !Number.isFinite(asPx(right))||Math.abs(footerBox[2]-asPx(right))>.001)
    throw Error('Unproven measured action-column geometry');
   const footerShape=['display','columnGap','rowGap','flexDirection','flexWrap','alignItems','justifyContent',
     'paddingInlineStart','paddingInlineEnd','borderInlineStartWidth','borderInlineEndWidth','direction','writingMode']
     .map(prop=>footerStyle[prop]);
   return Object.freeze({id:row.dataset.id,inline:rect(row)[2],
    state:JSON.stringify([row.className,row.dataset.detected,footerShape]),
    left:asPx(left),right:footerBox[2],originalTrack:style.gridTemplateColumns,
    buttons:readButtons?Object.freeze(children.map(buttonMetrics)):null,
    boxes:[rect(row),...['mod-art','mod-primary','mod-version','mod-assessment','mod-source','inspect-col'].map(k=>{
      const b=row.querySelector(':scope > .'+k);
      if(!b)throw Error('missing original child');
      return rect(b);
    }),...children.map(rect)]});
  });
  const rows=Object.freeze(observe(true)),plan=admitGroupSet(rows,owner);
  if(!plan)throw Error('Same layout shape did not have an exact shared native width');
  const style=document.createElement('style'),nonce=document.querySelector('style[nonce]')?.nonce;
  if(nonce)style.nonce=nonce;
  style.textContent=plan.rules.map(({token,px})=>
   'body[data-layout="compact"][data-library-view="gallery"] #modList > .mod-row[data-mh-proof-track="'+token+
   '"]{grid-template-columns:minmax(0,1fr) '+px+'px}').join('\n');
  function restore(){
   style.remove();for(const row of actual)delete row.dataset.mhProofTrack;
   for(const event of fontEvents)fontSet.removeEventListener(event,markChange);
  }
  function audit(){
   const next=[innerWidth,innerHeight,devicePixelRatio,document.documentElement.lang,body.dataset.density,
       body.dataset.theme,body.dataset.galleryGrouping,document.fonts.status];
   const current=list.querySelectorAll(':scope > .mod-row');
   const currentFaces=[...fontSet];
   if(epoch.changed||currentFaces.length!==faces.length||currentFaces.some((x,i)=>x!==faces[i])||
      current.length!==actual.length||next.some((v,i)=>!Object.is(v,boundary[i]))||lookupOwnedRules(plan,owner,rows)!==plan.rules||
      actual.some((r,i)=>r!==current[i]))
     throw Error('live evidence owner changed');
   const arm=observe(true);
   if(arm.some((r,i)=>shape(r)!==shape(rows[i])||!Object.is(r.left,rows[i].left)||!Object.is(r.right,rows[i].right)||r.originalTrack!==rows[i].originalTrack||
                      JSON.stringify(r.boxes)!==JSON.stringify(rows[i].boxes)))
     throw Error('complete row, grid or interactive control geometry differs');
   return {fullRecords:actual.length,sharedRules:plan.rules.length,allFullyRendered:true};
  }
  if(actual.some(row=>row.hasAttribute('data-mh-proof-track')))throw Error('prior diagnostic state');
  try{
   for(const event of fontEvents)fontSet.addEventListener(event,markChange);
   document.head.append(style);
   if(!style.sheet)throw Error('existing CSP will not admit diagnostic stylesheet');
   actual.forEach((r,i)=>{r.dataset.mhProofTrack=String(plan.assignments[i]);});
   const proof=audit();window[resultKey]={audit,restore};return proof;
  }catch(error){restore();throw error;}
 })(phase,count);
})