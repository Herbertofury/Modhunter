'use strict';
const {contextBridge, ipcRenderer}=require('electron');
const api={
  action:(kind,value)=>ipcRenderer.invoke('modhunter:action',{kind,value}),
  onState:(callback)=>{if(typeof callback!=='function')return()=>{};const cb=(_,state)=>callback(state);ipcRenderer.on('modhunter:state',cb);return()=>ipcRenderer.removeListener('modhunter:state',cb)}
};
contextBridge.exposeInMainWorld('modhunterShell',Object.freeze(api));
