'use strict';
(() => {
 const api=()=>window.ModhunterWatchtower,$=id=>document.getElementById(id);
 let model={rows:[]},busy=false,lastFocus=0,lastKey='',lastPrivacy=null;
 const button=document.createElement('button');button.id='noticeOpen';button.className='quiet';button.textContent='Notices';document.querySelector('.header-actions').prepend(button);
 const dialog=document.createElement('dialog');dialog.id='noticeDialog';dialog.setAttribute('aria-labelledby','noticeTitle');
 dialog.innerHTML='<div class="dialog-head"><div><div class="eyebrow">PRIVATE NOTIFICATIONS</div><h2 id="noticeTitle">Changes to review</h2></div><button id="noticeClose" type="button" class="icon-button" aria-label="Close notifications">×</button></div><div class="dialog-body"><p>Notices keep names and addresses private. Opening one rechecks the exact current page or native updater item.</p><div class="row-actions"><button id="noticeRefresh" type="button">Refresh verified facts</button></div><p id="noticeStatus" role="status"></p><div id="noticeList"></div><section id="noticeItemDetails" hidden></section><p class="scope-note" id="noticeScope"></p></div>';
 document.body.append(dialog);
 const text=(tag,value,className)=>{const node=document.createElement(tag);node.textContent=String(value??'');if(className)node.className=className;return node};
 async function call(kind,value={}){const result=await api().command(kind,value,true);if(!result?.ok)throw Error(result?.error||'The notification action was not confirmed. Refresh the current item.');return result}
 function status(message,error=false){$('noticeStatus').textContent=message;$('noticeStatus').dataset.outcome=error?'rejected':'ready';$('noticeStatus').setAttribute('role',error?'alert':'status')}
 function render(){
  const privacy=!!api().state().settings?.hideTitles;if(privacy!==lastPrivacy){lastPrivacy=privacy;const panel=$('noticeItemDetails');panel.hidden=true;panel.replaceChildren();panel.removeAttribute('data-item-id');panel.removeAttribute('data-profile-id');}
  button.textContent='Notices'+(model.rows?.some(row=>!row.seen)?' · '+model.rows.filter(row=>!row.seen).length:'');
  $('noticeScope').textContent=model.desktopActionScope||'Every action rechecks its exact current item. Nothing is installed from a notice.';
  const key=JSON.stringify([model.rows,model.error,model.status]);
  if(key!==lastKey){lastKey=key;const list=$('noticeList');list.replaceChildren();for(const row of model.rows||[]){
   const article=document.createElement('article');article.className='settings-card';article.dataset.noticeId=row.id;
   article.append(text('h3',row.title||'Recorded change'),text('p',row.body||'Review the exact current item.'),text('p',new Date(row.createdAt).toLocaleString(),'scope-note'));
   const actions=document.createElement('div');actions.className='row-actions';
   const open=document.createElement('button');open.type='button';open.dataset.noticeAction=row.id;open.textContent=row.actionLabel||'Review current item';open.onclick=()=>void activate(row.id);
   const dismiss=document.createElement('button');dismiss.type='button';dismiss.dataset.noticeDismiss=row.id;dismiss.textContent='Dismiss';dismiss.onclick=()=>void dismissNotice(row.id);
   actions.append(open,dismiss);article.append(actions);list.append(article);
  }if(!model.rows?.length)list.append(text('p','No verified changes have been recorded yet.','scope-note'));status(model.error||model.status||'Recorded notices are rechecked when you open them.',!!model.error);}
  $('noticeRefresh').disabled=busy||!!model.refreshing;
  for(const control of $('noticeList').querySelectorAll('button'))control.disabled=busy;
  const focus=Number(model.focusSequence||0);if(model.focusId&&focus>lastFocus&&!busy){lastFocus=focus;void activate(model.focusId)}
 }
 function show(){if(!dialog.open)dialog.showModal();render();}
 function close(){if(dialog.open)dialog.close();button.focus({preventScroll:true})}
 function showUpdater(action){
  show();const panel=$('noticeItemDetails');panel.hidden=false;panel.dataset.itemId=action.itemId;panel.dataset.profileId=action.profileId;panel.replaceChildren();
  const privateTitles=api().state().settings?.hideTitles;
  const heading=text('h3',privateTitles?'Verified native updater item':action.itemName||'Verified native updater item');if(!privateTitles&&action.itemName)heading.dataset.noTranslate='';panel.append(heading);
  panel.append(text('p','Installed '+String(action.installedVersion||'unknown')+' · Candidate '+String(action.latestVersion||'unknown')));
  panel.append(text('p','Source: '+String(action.provider||'verified provider')+' · Checked '+new Date(action.checkedAt).toLocaleString()));
  if(!privateTitles&&action.sourceUrl){const source=text('p',action.sourceUrl,'scope-note');source.dataset.noTranslate='';panel.append(source)};
  panel.append(text('p',action.scope||'This is a current version candidate. Exact files, compatibility and access require separate review.'));
  panel.setAttribute('tabindex','-1');panel.focus({preventScroll:true});status('Current native item and version evidence verified.');
 }
 async function activate(id){
  if(busy)return;busy=true;render();$('noticeItemDetails').hidden=true;
  try{const reply=await call('activateNotice',{id});const action=reply.noticeAction;
   if(action?.kind==='page'&&typeof action.watchId==='string'){close();api().openDetail(action.watchId);$('detailDrawer').dataset.noticeId=id;}
   else if(action?.kind==='updater'&&typeof action.itemId==='string')showUpdater(action);
   else throw Error('The native notification did not identify a current item.');
  }catch(error){show();status(error.message,true);api().showToast(error.message,null,'Undo',true)}finally{busy=false;for(const control of $('noticeList').querySelectorAll('button'))control.disabled=false;$('noticeRefresh').disabled=!!model.refreshing;}
 }
 async function dismissNotice(id){if(busy)return;busy=true;try{await call('dismissNotice',{id})}catch(error){status(error.message,true)}finally{busy=false;render()}}
 async function refresh(){if(busy)return;busy=true;render();try{const reply=await call('refreshNotices');if(reply.notices)model=reply.notices;lastKey='';}catch(error){status(error.message,true)}finally{busy=false;render()}}
 button.onclick=show;$('noticeClose').onclick=close;$('noticeRefresh').onclick=()=>void refresh();
 window.addEventListener('modhunter-native-state',()=>{const next=api().state().notices;if(next){model=next;render()}});
 void call('notices').then(reply=>{if(reply.notices)model=reply.notices;render()}).catch(error=>status(error.message,true));
})();
