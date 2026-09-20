/* Fixed, read-only inspection. No input values, SSH paths or credentials are returned. */
(() => {
 const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
 const bounds = el => {const r=el.getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)};};
 const nodes = [...document.querySelectorAll('button,a[data-page],input,select,textarea,dialog')].filter(visible);
 const surface = s => {const e=document.querySelector(s);if(!e)return null;const c=getComputedStyle(e);return {bounds:bounds(e),background:c.backgroundColor,blur:c.backdropFilter,radius:c.borderRadius};};
 return {page:document.body.dataset.page,language:window.WujiLocale?.lang,
   native:document.documentElement.dataset.desktop === 'true',
   viewport:{width:innerWidth,height:innerHeight},scroll:{x:scrollX,y:scrollY},overflow:document.documentElement.scrollWidth>innerWidth,
   appearance:{reduceTransparency:document.documentElement.dataset.reduceTransparency,reduceMotion:document.documentElement.dataset.reduceMotion},
   heading:document.querySelector('.workspace-page:not([hidden]) h2')?.textContent,
   surfaces:{header:surface('.top'),navigation:surface('.directory'),content:surface('.page-area'),menu:surface('#desktop-menu'),edgeTop:surface('.glass-edge-top'),edgeLeft:surface('.glass-edge-left'),edgeRight:surface('.glass-edge-right'),edgeBottom:surface('.glass-edge-bottom')},
   controls:nodes.map(e=>({id:e.id,tag:e.tagName,label:e.getAttribute('aria-label')||e.getAttribute('title')||(e.tagName==='INPUT'?'':e.textContent.trim().slice(0,100)),disabled:!!e.disabled,bounds:bounds(e)})),
   connection:(()=>{const e=document.getElementById('connection-toggle'),g=document.getElementById('connection-toolbar');if(!e)return null;const c=getComputedStyle(e);return {label:e.textContent,disabled:e.disabled,state:g.dataset.state,background:c.backgroundColor,color:c.color,bounds:bounds(g),panelOpen:!!document.getElementById('connection-popover')?.open};})(),
   errors:window.WujiDesktop?.errors||[]};
})()
