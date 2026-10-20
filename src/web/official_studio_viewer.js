/* The original Linux application's pixels and input; no reimplemented Studio UI. */
import RFB from '/studio-assets/core/rfb.js';
const values=new URLSearchParams(location.hash.slice(1));
const port=Number(values.get('port')),password=values.get('password'),session=values.get('session');
history.replaceState(null,'',location.pathname);
const status=document.getElementById('studio-viewer-status');
const report=(phase,detail='')=>{status.textContent=detail||phase;status.hidden=phase==='connected';if(parent!==window)parent.postMessage({type:'official-studio-viewer',session,phase,detail},location.origin)};
if(!Number.isInteger(port)||port<1024||port>65535||!password||password.length>100){report('error','Studio session is unavailable. Reopen the view from the workbench.');}
else{
 const rfb=new RFB(document.getElementById('studio-screen'),'ws://127.0.0.1:'+port,{credentials:{password}});
 let closed=false;
 rfb.scaleViewport=true;rfb.clipViewport=false;rfb.qualityLevel=8;rfb.compressionLevel=3;
 // Preserve the complete vendor layout in narrow workbench panels. Enlarged
 // views may resize the desktop; small views scale it rather than clipping it.
 const resize=()=>{if(!closed)rfb.resizeSession=innerWidth>=1280&&innerHeight>=800;};resize();window.addEventListener('resize',resize);
 rfb.addEventListener('connect',()=>report('connected'));
 rfb.addEventListener('disconnect',e=>{closed=true;window.removeEventListener('resize',resize);report('disconnected',e.detail.clean?'Studio view disconnected.':e.detail.reason||'Studio connection interrupted.');});
 rfb.addEventListener('securityfailure',()=>report('error','Studio session authentication failed. Reopen the view.'));
 window.addEventListener('pagehide',()=>{window.removeEventListener('resize',resize);if(!closed){closed=true;rfb.disconnect();}},{once:true});
}
