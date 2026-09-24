/* A missed 60 Hz presentation (>31 ms) or sustained sub-60 Hz delivery turns experimental refraction off.
   The limit applies only while the external pixels are visible. */
((host)=>{
 'use strict';
 const MAX_GAP_MS=31,MIN_HZ=59.5,WINDOW=30;
 class RefractionHealth{
  constructor(){this.reset();}
  reset(){this.lastDisplay=null;this.lastPresent=null;this.displayIntervals=[];this.presentIntervals=[];this.processingSamples=[];this.lastFault=null;}
  check(kind,previous,now,intervals){
   if(previous===null)return null;
   const gap=now-previous;
   if(!Number.isFinite(gap)||gap<0)return null;
   if(gap>MAX_GAP_MS){this.lastFault={kind,gap_ms:Math.round(gap*10)/10};return this.lastFault;}
   intervals.push(gap);if(intervals.length>WINDOW)intervals.shift();
   if(intervals.length===WINDOW){const hz=1000*WINDOW/intervals.reduce((a,b)=>a+b,0);
    if(hz<MIN_HZ){this.lastFault={kind:kind+'_rate',hz:Math.round(hz*10)/10};return this.lastFault;}}
   return null;
  }
  display(now,visible){if(!visible){this.lastDisplay=null;this.displayIntervals=[];return null;}
   const fault=this.check('display',this.lastDisplay,now,this.displayIntervals);this.lastDisplay=now;return fault;}
  present(now,sourceFrames){if(sourceFrames<12){this.lastPresent=null;this.presentIntervals=[];return null;}
   const fault=this.check('presentation',this.lastPresent,now,this.presentIntervals);this.lastPresent=now;return fault;}
  processing(ms,visible){if(!visible||!Number.isFinite(ms)||ms<0)return null;
   this.processingSamples.push(ms);if(this.processingSamples.length>WINDOW)this.processingSamples.shift();
   if(ms>MAX_GAP_MS){this.lastFault={kind:'processing',gap_ms:Math.round(ms*10)/10};return this.lastFault;}
   return null;}
  capture(hz,count){if(count<12||!Number.isFinite(hz)||hz>=MIN_HZ)return null;
   this.lastFault={kind:'capture_rate',hz};return this.lastFault;}
  stats(){const sum=this.displayIntervals.reduce((a,b)=>a+b,0),presentSum=this.presentIntervals.reduce((a,b)=>a+b,0),workSum=this.processingSamples.reduce((a,b)=>a+b,0);
   return {display_hz:sum>0?Math.round(1000*this.displayIntervals.length/sum):null,
    presented_hz:presentSum>0?Math.round(1000*this.presentIntervals.length/presentSum):null,
    mean_processing_ms:this.processingSamples.length?Math.round(workSum/this.processingSamples.length*10)/10:null,
    max_gap_ms:MAX_GAP_MS,last_fault:this.lastFault};}
 }
 host.WujiRefractionHealth={RefractionHealth,MAX_GAP_MS,MIN_HZ};
 if(typeof module!=='undefined'&&module.exports)module.exports=host.WujiRefractionHealth;
})(typeof window!=='undefined'?window:globalThis);
