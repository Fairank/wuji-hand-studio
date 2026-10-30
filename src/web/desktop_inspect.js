/* Fixed, read-only inspection. No input values, SSH paths or credentials are returned. */
(() => {
 const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
 const bounds = el => {const r=el.getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)};};
 const nodes = [...document.querySelectorAll('button,a[data-page],input,select,textarea,dialog')].filter(visible);
 const surface = s => {const e=document.querySelector(s);if(!e)return null;const c=getComputedStyle(e);return {bounds:bounds(e),background:c.backgroundColor,blur:c.backdropFilter,radius:c.borderRadius};};
 const branding=(()=>{const e=document.querySelector('img.studio-brand');if(!e)return null;const c=getComputedStyle(e);return {bounds:bounds(e),naturalWidth:e.naturalWidth,naturalHeight:e.naturalHeight,complete:e.complete,computedWidth:c.width,computedHeight:c.height,display:c.display};})();
 const stylesheets=[...document.querySelectorAll('link[rel~="stylesheet"]')].flatMap(link=>{
  let url;try{url=new URL(link.getAttribute('href')||'',location.href);}catch{return [];}
  if(url.origin!==location.origin||!['http:','https:'].includes(url.protocol))return [];
  const sheet=link.sheet,result={href:url.pathname,sheetPresent:!!sheet,cssRules:null,error:null,disabled:!!link.disabled};
  if(sheet)try{result.cssRules=sheet.cssRules.length;}catch(error){result.error=typeof error?.name==='string'?error.name.slice(0,60):'Stylesheet unreadable';}
  return [result];
 });
 return {page:document.body.dataset.page,language:window.WujiLocale?.lang,experience:document.documentElement.dataset.experience,
   documentReadyState:document.readyState,branding,stylesheets,
   native:document.documentElement.dataset.desktop === 'true',
   viewport:{width:innerWidth,height:innerHeight},scroll:{x:scrollX,y:scrollY},overflow:document.documentElement.scrollWidth>innerWidth,
   appearance:{reduceTransparency:document.documentElement.dataset.reduceTransparency,reduceMotion:document.documentElement.dataset.reduceMotion},
   heading:document.querySelector('.workspace-page:not([hidden]) h2')?.textContent,
   closedLayoutBoxes:[...document.querySelectorAll('details:not([open])>:not(summary),dialog:not([open])')].filter(e=>e.getClientRects().length).map(e=>({id:e.id,tag:e.tagName,bounds:bounds(e)})),
   surfaces:{header:surface('.top'),navigation:surface('.directory'),content:surface('.page-area'),menu:surface('#desktop-menu')},
   officialStudio:null,
   officialVisualization:(()=>{const e=document.getElementById('hub-official');return e?{visible:visible(e),phase:document.querySelector('.ov-root')?.dataset.phase,task:document.querySelector('.ov-root')?.dataset.task,preparationButtons:{viewPresent:!!document.getElementById('ov-start'),calibrationPresent:!!document.getElementById('ov-calibrate')},viewMode:document.querySelector('.ov-root')?.dataset.viewMode,rendererReady:document.querySelector('.ov-root')?.dataset.rendererReady==='true',status:document.getElementById('ov-state')?.textContent,frames:document.querySelectorAll('#ov-frame').length,focused:document.body.dataset.officialVizFocus==='true',bounds:bounds(e)}:null})(),
   deviceInventory:(()=>{const e=document.getElementById('device-inventory');return e?{visible:visible(e),cards:e.querySelectorAll('.di-device').length,selected:e.querySelector('input:checked')?.value||null,status:e.querySelector('#di-notice')?.textContent,bounds:bounds(e)}:null})(),
   officialTools:(()=>{const e=document.getElementById('official-tools');return e?{visible:visible(e),status:document.getElementById('ot-state')?.textContent,cli:document.getElementById('ot-cli')?.textContent,sdk:document.getElementById('ot-sdk')?.textContent,bounds:bounds(e),studioEntry:!!document.querySelector('[data-page="official-studio"]')}:null})(),
   controls:nodes.map(e=>({id:e.id,tag:e.tagName,label:e.getAttribute('aria-label')||e.getAttribute('title')||(e.tagName==='INPUT'?'':e.textContent.trim().slice(0,100)),disabled:!!e.disabled,bounds:bounds(e)})),
   connection:(()=>{const e=document.getElementById('connection-toggle'),g=document.getElementById('connection-toolbar');if(!e)return null;const c=getComputedStyle(e);return {label:e.textContent,disabled:e.disabled,state:g.dataset.state,background:c.backgroundColor,color:c.color,bounds:bounds(g),panelOpen:!!document.getElementById('connection-popover')?.open};})(),
   errors:window.WujiDesktop?.errors||[]};
})()
