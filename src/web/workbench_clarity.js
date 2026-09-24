/* Presentation hierarchy only. Existing controls, listeners and device paths stay intact. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const sheets=[];
 function sheet(id,zh,en,nodes){
  const item=window.HandWorkbenchSheet.create({id,title:t(zh,en),triggerLabel:t(zh,en)});
  for(const node of nodes)if(node)item.body.append(node);
  sheets.push({item,zh,en});return item;
 }
 function init(){
  if(!window.HandWorkbenchSheet)return;
  const heading=$('page-library').querySelector('.page-heading'),controls=document.querySelector('.motion-controls');
  const headingTools=document.createElement('div');headingTools.className='wc-heading-tools';heading.append(headingTools);
  const modes=document.querySelector('.playback-modes');controls.prepend(modes);
  const program=$('wb-program');program.open=true;
  const playlist=sheet('wc-playlist','节目单','Playlist',[program]);headingTools.append(playlist.trigger);
  const details=sheet('wc-run-details','运行详情','Run details',[]);
  headingTools.append(details.trigger);
  // Keep errors/blockers and confirmation on the main screen. Only reference copy is moved.
  const real=$('real-motion-panel');
  for(const node of [...real.querySelectorAll('.motion-notes')])details.body.append(node);
  details.body.append($('command-timing'));
  for(const node of [...$('page-library').querySelectorAll('.library-reference')])details.body.append(node);
  const confirm=$('trial-clear').closest('label');
  const options=document.createElement('details');options.className='wc-options';
  const summary=document.createElement('summary');summary.id='wc-options-title';options.append(summary);
  const fields=document.createElement('div');fields.className='motion-fields wc-option-fields';options.append(fields);
  for(const id of ['trial-cycles','trial-amplitude'])fields.append($(id).closest('.motion-field'));
  confirm.before(options);
  const preview=$('playback-panel');
  preview.querySelectorAll(':scope > .hint:not([id])').forEach(n=>details.body.append(n));
  // A short source statement remains on screen. Technical information is available in details.
  const source=document.createElement('p');source.className='wc-view-help';
  const dock=document.querySelector('.viewer-dock');dock.append(source);
  const running=document.createElement('div');running.className='wc-playlist-running';running.hidden=true;
  const runningText=document.createElement('span'),playlistOpen=document.createElement('button'),playlistStop=document.createElement('button');
  playlistOpen.type='button';playlistOpen.onclick=()=>playlist.open();
  playlistStop.type='button';playlistStop.className='wc-stop';playlistStop.onclick=()=>{if(!$('wb-program-stop').disabled)$('wb-program-stop').click();};
  running.append(runningText,playlistOpen,playlistStop);heading.after(running);
  const nav=document.querySelector('.directory'),advanced=nav.querySelector('.nav-tool-links');
  for(const key of ['feedback','parameters','doctor','interaction','capture','records']){
   const link=nav.querySelector(`[data-page="${key}"]`);if(link)advanced.append(link);
  }
  for(const key of ['library','devices','glove','connection']){
   const link=nav.querySelector(`[data-page="${key}"]`);if(link)nav.insertBefore(link,nav.querySelector('.nav-advanced'));
  }
  function labels(){
   for(const {item,zh,en}of sheets)item.setLabels({title:t(zh,en),triggerLabel:t(zh,en),closeLabel:t('关闭','Close')});
   summary.textContent=t('循环与幅度','Cycles & amplitude');
   source.textContent=t('拖动旋转 · 滚轮缩放 · 画面来源始终标在左下角','Drag to orbit · Scroll to zoom · Source is labelled in the view');
   playlistOpen.textContent=t('查看节目单','View playlist');
   playlistStop.textContent=t('停止节目单','Stop playlist');
   const names={devices:['多手演示','Hand ensemble'],glove:['手套遥操作','Glove control']};
   for(const [key,pair]of Object.entries(names)){
    const link=nav.querySelector(`[data-page="${key}"] span`);if(link){link.removeAttribute('data-i18n');link.textContent=t(...pair);}
   }
   const navTitle=$('nav-tools-title');if(navTitle)navTitle.textContent=t('调试工具','Tools');
   window.WujiLocale.apply(heading);
  }
  function mode(){
   const value=$('mode-preview').getAttribute('aria-pressed')==='true'?'preview':'hardware';
   controls.dataset.playback=value;
  }
  modes.addEventListener('click',e=>{
   mode();
   if(!e.target.closest('button'))return;
   const source=controls.dataset.playback==='preview'?'preview':'feedback';
   document.querySelector(`.view-sources [data-source="${source}"]`)?.click();
  });
  // Mode controls select the source only through the existing display control, never a motor command.
  const observer=new MutationObserver(mode);observer.observe($('mode-preview'),{attributes:true,attributeFilter:['aria-pressed']});
  window.addEventListener('console-state',e=>{
   const p=e.detail?.program;running.hidden=!p?.active;
   playlistStop.disabled=$('wb-program-stop').disabled;
   runningText.textContent=p?.active?t('节目单播放中','Playlist playing'):'';
  });
  window.addEventListener('wuji-language',labels);
  window.addEventListener('workspace-page',()=>{
   const link=advanced.querySelector('[aria-current=page]');if(link)advanced.parentElement.open=true;
  });
  labels();mode();
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
