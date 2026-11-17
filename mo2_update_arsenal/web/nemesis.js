/* Original, game-only rival rules. Never an update recommendation or currency authority. */
(function(root){'use strict';
 const day=86400000,number=(n,max=Number.MAX_SAFE_INTEGER)=>Number.isFinite(n)?Math.max(0,Math.min(max,n)):0;
 function key(target,type='warden'){return target?.id?JSON.stringify(['mod',String(target.id),String(target.installedVersion||''),String(target.latestVersion||'')]):JSON.stringify(['training',type]);}
 function record(book,k){return Object.hasOwn(book,k)&&book[k]&&typeof book[k]==='object'?book[k]:(book[k]={losses:0,victories:0});}
 function observe(book,targets,now){if(!Number.isFinite(now)||now<=0)return;for(const t of targets){const r=record(book,key(t));if(!Number.isFinite(r.observedAt)||r.observedAt<=0)r.observedAt=now;r.lastObservedAt=Math.max(number(r.lastObservedAt),now);}}
 function stats(book,k,enabled=true){const r=book[k]||{},losses=Math.floor(number(r.losses,5));
 // Only confirmed observation span counts; an offline gap alone cannot increase age.
 const age=Number.isFinite(r.observedAt)&&r.observedAt>0&&Number.isFinite(r.lastObservedAt)&&r.lastObservedAt>=r.observedAt?Math.floor((r.lastObservedAt-r.observedAt)/day):null;
 const rank=enabled?Math.min(8,losses+Math.min(3,Math.floor((age||0)/7))):0;
 return {key:k,rank,losses,ageDays:age,hp:1+.12*rank,damage:1+.05*rank,bonusXP:rank*4,bonusScore:rank*15,label:rank?'RIVAL '+rank:'UNPROVEN',ageLabel:age===null?'Update age unknown':`${age}d since first observed candidate (not release age)`};}
 function lost(book,k,runId){const r=record(book,k);if(!runId||r.lastLossRun===runId)return false;r.losses=Math.min(5,number(r.losses,5)+1);r.lastLossRun=runId;return true;}
 function won(book,k){const r=record(book,k);r.victories=number(r.victories)+1;r.losses=Math.max(0,number(r.losses,5)-1);}
 const api={key,observe,stats,lost,won};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ModhunterNemesis=api;
})(globalThis);
