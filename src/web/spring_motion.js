/* One local preview, with no hardware/API calls or continuous animation. */
(()=>{
  'use strict';
  const host=document.getElementById('settings-appearance')?.querySelector('.wb-card');
  if(!host)return;
  const root=document.documentElement,system=matchMedia('(prefers-reduced-motion: reduce)');
  const row=document.createElement('div');row.className='spring-preview-row';
  const copy=document.createElement('div');copy.className='spring-preview-copy';
  const title=document.createElement('strong'),note=document.createElement('p');note.id='spring-preview-note';
  copy.append(title,note);
  const button=document.createElement('button');button.type='button';button.id='spring-preview';button.setAttribute('aria-describedby',note.id);
  const dot=document.createElement('span');dot.className='spring-preview-dot';dot.setAttribute('aria-hidden','true');
  const label=document.createElement('span');button.append(dot,label);row.append(copy,button);host.append(row);
  const reduced=()=>root.dataset.reduceMotion==='true'||system.matches;
  const cancel=()=>dot.getAnimations().forEach(animation=>animation.cancel());
  function render(){
    const en=window.WujiLocale?.lang==='en';
    title.textContent=en?'Gentle spring response':'柔和回弹';
    note.textContent=reduced()?(en?'Reduce motion is on. Controls respond immediately.':'已减少动态效果，控件即时响应。'):
      (en?'A light press, then a soft return. Only the interface moves.':'轻按、回弹、自然停稳，仅用于界面反馈。');
    label.textContent=en?'Try it':'试一下';button.disabled=reduced();
    if(reduced())cancel();
  }
  button.addEventListener('click',()=>{
    cancel();if(reduced())return;
    const easing=getComputedStyle(root).getPropertyValue('--wb-spring').trim();
    dot.animate([{transform:'scale(.65)'},{transform:'scale(1)'}],{duration:450,easing:easing||'ease-out',iterations:1});
  });
  // Preference changes cancel in-flight preview effects, including OS changes.
  new MutationObserver(render).observe(root,{attributes:true,attributeFilter:['data-reduce-motion']});
  system.addEventListener('change',render);window.addEventListener('wuji-language',render);
  window.addEventListener('workspace-page',event=>{if(event.detail!=='settings')cancel();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel();});
  render();
})();
