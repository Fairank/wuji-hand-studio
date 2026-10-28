/* Display sizing only. Original viewers, images and terminals keep their nodes. */
(()=>{
 'use strict';
 const KEY='hand-workbench.viewport-height.v1.',areas=new Map();
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
 function limits(){return{min:220,max:clamp(Math.round(innerHeight*1.6),480,1600)};}
 function read(id){try{const value=JSON.parse(localStorage.getItem(KEY+id));return Number.isFinite(value)&&value>=220&&value<=1600?value:null;}catch{return null;}}
 function save(id,value){try{if(value===null)localStorage.removeItem(KEY+id);else localStorage.setItem(KEY+id,JSON.stringify(value));}catch{/* Sizing remains available when storage is blocked. */}}
 function mount(id,target,container){
  const control=document.createElement('div');control.className='vp-resize';control.dataset.viewportArea=id;
  control.innerHTML='<div class="vp-grip" role="separator" tabindex="0" aria-orientation="horizontal"><span aria-hidden="true"></span></div><details class="vp-settings"><summary><span class="vp-label"></span><span class="vp-value"></span></summary><div class="vp-size-panel"><label><span class="vp-range-label"></span><input type="range" step="10"></label><button type="button" class="vp-reset"></button></div></details>';
  if(id==='official')target.after(control);else container.append(control);
  const grip=control.querySelector('.vp-grip'),range=control.querySelector('input'),value=control.querySelector('.vp-value'),reset=control.querySelector('.vp-reset');
  let desired=read(id),drag=null;
  function sizeTerminal(){
   if(id!=='official')return;
   for(const screen of target.querySelectorAll('.official-terminal-screen')){
    if(desired===null){screen.style.removeProperty('--terminal-viewport-height');continue;}
    if(!screen.getClientRects().length)continue;
    // Keep the original terminal node and let its existing FitAddon observer
    // send the resulting PTY dimensions; only the available display changes.
    const bottom=parseFloat(getComputedStyle(screen.parentElement).paddingBottom)||0;
    const height=Math.max(160,Math.round(target.getBoundingClientRect().bottom-screen.getBoundingClientRect().top-bottom-1));
    screen.style.setProperty('--terminal-viewport-height',height+'px');
   }
  }
  if(!target.id)target.id='vp-'+id+'-screen';grip.setAttribute('aria-controls',target.id);range.setAttribute('aria-controls',target.id);
  function actual(){return Math.round(target.getBoundingClientRect().height)||limits().min;}
  function translate(){
   const name=id==='glove'?t('手套画面','Glove view'):t('官方工具画面','Official tool view');
   control.querySelector('.vp-label').textContent=t('画面大小','View size');
   control.querySelector('.vp-range-label').textContent=t('画面高度','View height');reset.textContent=t('恢复自适应','Reset to automatic');
   grip.setAttribute('aria-label',name+t('高度；拖动或用上下方向键调整，双击恢复',' height; drag or use arrow keys, double-click to reset'));
   range.setAttribute('aria-label',name+t('高度',' height'));update();
  }
  function update(){
   const bounds=limits(),height=actual();range.min=String(bounds.min);range.max=String(bounds.max);range.value=String(clamp(height,bounds.min,bounds.max));
   grip.setAttribute('aria-valuemin',String(bounds.min));grip.setAttribute('aria-valuemax',String(bounds.max));grip.setAttribute('aria-valuenow',String(clamp(height,bounds.min,bounds.max)));
   grip.setAttribute('aria-valuetext',height+t(' 像素',' pixels'));
   value.textContent=desired===null?t('自适应','Automatic'):height+' px';
   reset.disabled=desired===null;
  }
  function apply(){
   if(desired===null){target.removeAttribute('data-vp-sized');target.style.removeProperty('--vp-height');}
   else{target.dataset.vpSized='true';target.style.setProperty('--vp-height',clamp(desired,limits().min,limits().max)+'px');}
   sizeTerminal();update();
  }
  function choose(height,persist=true){desired=Math.round(clamp(height,limits().min,limits().max));apply();if(persist)save(id,desired);}
  function automatic(){desired=null;apply();save(id,null);}
  range.addEventListener('input',()=>choose(Number(range.value)));
  reset.addEventListener('click',automatic);grip.addEventListener('dblclick',automatic);
  grip.addEventListener('keydown',event=>{
   let height=actual(),step=event.shiftKey?80:20;
   if(event.key==='ArrowUp')height-=step;else if(event.key==='ArrowDown')height+=step;
   else if(event.key==='Home')height=limits().min;else if(event.key==='End')height=limits().max;
   else if(event.key==='Escape'){control.querySelector('details').open=false;return;}else return;
   event.preventDefault();choose(height);
  });
  grip.addEventListener('pointerdown',event=>{
   if(event.button!==0||!event.isPrimary)return;
   event.preventDefault();drag={id:event.pointerId,y:event.clientY,height:actual()};grip.setPointerCapture(event.pointerId);control.dataset.dragging='true';
  });
  grip.addEventListener('pointermove',event=>{if(drag?.id===event.pointerId)choose(drag.height+event.clientY-drag.y,false);});
  function finish(event){if(drag?.id!==event.pointerId)return;drag=null;delete control.dataset.dragging;if(desired!==null)save(id,desired);}
  grip.addEventListener('pointerup',finish);grip.addEventListener('pointercancel',finish);grip.addEventListener('lostpointercapture',finish);
  new ResizeObserver(()=>{sizeTerminal();update();}).observe(target);
  if(id==='official')new MutationObserver(()=>requestAnimationFrame(sizeTerminal)).observe(target,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','data-human-tty','data-guided-native']});
  const area={target,control,refresh(){control.hidden=id==='glove'&&!document.querySelector('#page-glove')?.contains(target);translate();},resize:apply};
  areas.set(id,area);apply();area.refresh();return area;
 }
 function discover(){
  const glove=document.querySelector('#page-glove .pose-screen'),display=document.querySelector('#page-glove .glove-display');
  if(glove&&display&&!areas.has('glove'))mount('glove',glove,display);
  const official=document.getElementById('ov-shared-stage');if(official&&!areas.has('official'))mount('official',official,official.parentElement);
  for(const area of areas.values())area.control.hidden=area.control.dataset.viewportArea==='glove'&&!document.querySelector('#page-glove')?.contains(area.target);
 }
 function start(){discover();new MutationObserver(records=>{if(records.some(record=>record.addedNodes.length||record.removedNodes.length))discover();}).observe(document.body,{childList:true,subtree:true});}
 window.addEventListener('resize',()=>{for(const area of areas.values())area.resize();});
 window.addEventListener('wuji-language',()=>{for(const area of areas.values())area.refresh();});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
