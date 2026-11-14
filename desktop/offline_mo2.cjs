'use strict';
/**
 * Read-only offline MO2 profile import for standalone Modhunter Desktop.
 *
 * The user selects a specific MO2 profiles/<name>/modlist.txt. This module
 * does not write or launch MO2, follow mod symlinks, upload local paths,
 * or assume a game identity from a mod's display name.
 */
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');

const h=(value)=>crypto.createHash('sha256').update(String(value)).digest('hex').slice(0,16);
const safeName=(v)=>typeof v==='string'&&v.length>0&&v.length<260&&v!=='.'&&v!=='..'&&!/[\/\\\u0000-\u001f]/u.test(v);
const urlOf=(v)=>{
  try{
    const u=new URL(String(v||'').trim());
    if(u.protocol!=='https:'||!u.hostname||u.username||u.password||u.port||u.hostname.toLowerCase()==='localhost')return '';
    return 'https://'+u.hostname.toLowerCase()+(u.pathname||'/');
  }catch{return ''}
};
const provider=(u)=>{
 const host=new URL(u).hostname.replace(/^www\./,'').toLowerCase();
 const types=[['nexusmods.com','nexus'],['github.com','github'],['gitlab.com','gitlab'],
 ['patreon.com','patreon'],['loverslab.com','loverslab'],['moddb.com','moddb'],
 ['curseforge.com','curseforge'],['creations.bethesda.net','bethesda'],
 ['modding-guild.com','modding-guild'],['dwemermods.com','dwemer'],['modrinth.com','modrinth']];
 return (types.find(([domain])=>host===domain||host.endsWith('.'+domain))||['','website'])[1];
};
function ini(text){
 const result={},sections=[];
 let section='general';
 for(const raw of String(text||'').split(/\r?\n/u)){
  const line=raw.trim();
  if(!line||line.startsWith(';')||line.startsWith('#'))continue;
  const group=line.match(/^\[([^\]]+)\]$/u);
  if(group){section=group[1].toLowerCase();sections.push(section);continue}
  const ix=line.indexOf('=');
  if(ix<1)continue;
  const key=line.slice(0,ix).trim().toLowerCase(),v=line.slice(ix+1).trim();
  if(!result[key]||section==='general')result[key]=v;
 }
 return result;
}
function gameSlug(raw){
 const normalized=String(raw||'').toLowerCase().replace(/[^a-z0-9]/g,'');
 const lookup={
  skyrimspecialedition:'skyrimspecialedition',skyrimse:'skyrimspecialedition',
  skyrimanniversaryedition:'skyrimspecialedition',skyrim:'skyrim',
  skyrimvr:'skyrimvr',fallout4:'fallout4',fallout4vr:'fallout4vr',
  falloutnewvegas:'newvegas',newvegas:'newvegas',fallout3:'fallout3',
  oblivion:'oblivion',oblivionremastered:'oblivionremastered',
  morrowind:'morrowind',starfield:'starfield',enderalspecialedition:'enderalspecialedition'};
 return lookup[normalized]||'unknown';
}
async function boundedText(file,max=128000){
 try{
  const stat=await fs.stat(file);
  if(!stat.isFile()||stat.size>max||stat.size<0)return '';
  return (await fs.readFile(file,'utf8')).replace(/^\uFEFF/u,'');
 }catch{return ''}
}
function parseModlist(content){
 const entries=[],seen=new Set();
 for(const raw of String(content||'').split(/\r?\n/u)){
  const line=raw.replace(/^\uFEFF/u,'').trimEnd();
  if(!line||line[0]==='#')continue;
  const enabled=line[0]==='+',disabled=line[0]==='-';
  if(!enabled&&!disabled)continue;
  const name=line.slice(1).trim();
  if(!safeName(name)||seen.has(name.toLowerCase()))continue;
  seen.add(name.toLowerCase());
  entries.push({name,enabled});
 }
 return entries;
}
function sourceFor(raw,modid,game){
 const exact=urlOf(raw);
 if(exact){
  const type=provider(exact);
  return {type,verified:true,kind:'project',label:type.toUpperCase()+' · MO2 URL',url:exact};
 }
 if(modid>0&&game!=='unknown')return {type:'nexus',verified:true,kind:'project',
  label:'Nexus / Files',url:'https://www.nexusmods.com/'+game+'/mods/'+modid+'?tab=files'};
 return {type:'unknown',verified:false,kind:'unresolved',label:'Source unlinked',url:''};
}
async function readMo2Profile(modlistPath){
 const file=path.resolve(String(modlistPath||''));
 if(path.basename(file).toLowerCase()!=='modlist.txt')
  throw Error('Select the modlist.txt file inside an MO2 profile directory');
 const profileDir=path.dirname(file),profilesDir=path.dirname(profileDir);
 if(path.basename(profilesDir).toLowerCase()!=='profiles')
  throw Error('Expected an MO2 profiles/<name>/modlist.txt location');
 const instanceDir=path.dirname(profilesDir);
 const modsDir=path.join(instanceDir,'mods');
 const modsStat=await fs.stat(modsDir).catch(()=>null);
 if(!modsStat||!modsStat.isDirectory())
  throw Error('MO2 mods/ directory not found beside profiles/. Choose the profile from the same MO2 instance, or use the live plugin for split-path installs.');
 const modlist=await boundedText(file,12_000_000);
 if(!modlist)throw Error('The selected MO2 modlist.txt is missing, empty or too large to parse');
 const entries=parseModlist(modlist),present=new Set(entries.map(x=>x.name.toLowerCase()));
 const dirs=await fs.readdir(modsDir,{withFileTypes:true});
 // Imported profile order is preserved; installed mod directories missing from
 // this profile still appear separately, never silently dropped.
 for(const d of dirs){
  if(!d.isDirectory()||!safeName(d.name)||present.has(d.name.toLowerCase()))continue;
  entries.push({name:d.name,enabled:false,notInProfile:true});
 }
 const settings=ini(await boundedText(path.join(instanceDir,'ModOrganizer.ini')));
 const game=gameSlug(settings.gamename||settings.game||settings.gamenexusname||'');
 const gameRuntime=settings.gameversion||'unknown';
 const profileId='offline-'+h(instanceDir.toLowerCase()+'|'+profileDir.toLowerCase());
 const groups=[],items=[];
 const addGroup=(name)=>{
  const group={id:'g-'+h(profileId+'|'+name+'|'+groups.length),name,
    startPriority:items.length,count:0,color:['mint','gold','sky','rose','green'][groups.length%5]};
  groups.push(group);return group;
 };
 let group=addGroup('Imported load order');
 const allowed=new Set(dirs.filter(d=>d.isDirectory()).map(d=>d.name.toLowerCase()));
 const fetchItem=async(entry,index)=>{
  const modDir=path.join(modsDir,entry.name);
  const meta=allowed.has(entry.name.toLowerCase())?ini(await boundedText(path.join(modDir,'meta.ini'),65536)):{};
  const mid=Number(meta.modid||meta.nexusid||meta.mod_id||0);
  const nexusId=Number.isSafeInteger(mid)&&mid>0?mid:0;
  const version=String(meta.version||'?'),latest=String(meta.newestversion||'');
  const customDisabled=['false','0'].includes(String(meta.hascustomurl||'').toLowerCase());
  const url=customDisabled?'':(meta.url||meta.customurl||meta.modurl||meta.homepage||meta.website||'');
  let source=sourceFor(url,nexusId,game);
  const notes=String(meta.notes||'');
  // Notes may describe dependencies or troubleshooting, not this mod's identity.
  if(!source.verified){
   const found=notes.match(/https:\/\/[^\s<>'"\])]+/iu);
   if(found){
    const advisory=sourceFor(found[0],0,'unknown');
    if(advisory.verified)source={...advisory,verified:false,kind:'advisory',
      label:'Unverified link from MO2 notes'};
   }
  }
  const modPath=path.join(modDir,'SKSE','Plugins');
  let flags=[];
  // Apply the same real-directory boundary as metadata reads. A listed
  // symlink is retained as an unresolved item, not traversed for DLL flags.
  try{const children=allowed.has(entry.name.toLowerCase())
      ?await fs.readdir(modPath,{withFileTypes:true}):[];
      if(children.some(x=>x.isFile()&&x.name.toLowerCase().endsWith('.dll')))flags=['DLL'];
  }catch{}
  return {id:'live-'+h(profileId+'|'+entry.name),mo2InternalName:entry.name,
    name:entry.name,installedVersion:version,latestKnownVersion:latest,
    enabled:entry.enabled,nexusId,priority:index,flags,groupId:'',
    group:'Imported load order',screenshotNote:notes.slice(0,4000),author:String(meta.author||''),
    source,screenshot:0,audit:flags.length?{
      verdict:'check',note:'Native DLL detected; verify game runtime and dependencies',evidence:'Imported MO2 profile metadata'}:{},
    offlineImported:true};
 };
 const imported=new Array(entries.length),workers=Math.min(24,Math.max(1,entries.length));
 let next=0;
 await Promise.all(Array.from({length:workers},async()=>{
   while(next<entries.length){const i=next++;imported[i]=await fetchItem(entries[i],i);}
 }));
 for(let i=0;i<entries.length;i++){
  const entry=entries[i],item=imported[i];
  if(entry.name.toLowerCase().endsWith('_separator')){
   group=addGroup(entry.name.replace(/_separator$/iu,''));
   continue;
  }
  if(entry.notInProfile&&group.name!=='Installed but not in this profile')group=addGroup('Installed but not in this profile');
  group.count++;item.group=group.name;item.groupId=group.id;items.push(item);
 }
 const stamp=new Date().toISOString();
 return {
   schemaVersion:2,catalogId:'offline-mo2-'+profileId,profileId,
   profileName:path.basename(profileDir),game,gameRuntime,
   title:'Imported MO2 / '+path.basename(profileDir),generatedAt:stamp,
   groups,items,build:{count:items.length,groups:groups.length,
     verifiedLinks:items.filter(x=>x.source.verified).length,
     unresolvedLinks:items.filter(x=>!x.source.verified).length},
   sources:[{kind:'offline-modlist',name:'User-selected MO2 profile',timestamp:stamp}],
   offline:true,importedAt:stamp
 };
}
module.exports={readMo2Profile,parseModlist,ini,gameSlug,sourceFor};
