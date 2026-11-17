/* Shared simulation-clock contract. No wall clock, network or ranked currency. */
(()=>{'use strict';
const minutes=[20,30,60,120];
function seconds(value){return value==='endless'?null:(minutes.includes(Number(value))?Number(value):30)*60}
function duration(run){if(run.duration===null)return null;return Number.isFinite(run.duration)&&run.duration>=1200?run.duration:1200}
function complete(run,elapsed){const goal=duration(run);return goal!==null&&elapsed>=goal}
function label(run){const goal=duration(run);return goal===null?'ENDLESS':`${goal/60} MIN`}
window.ModhunterRunContract=Object.freeze({seconds,duration,complete,label});
})();
