(() => {
  'use strict';
  const get=id=>document.getElementById(id),app=document.querySelector('.app');
  const pages=[['motion','动作播放','选择动作、幅度与循环，画面跟随实际反馈。'],['parameters','参数调节','修改数值后保存，再同步到控制端。'],['feedback','实时反馈','查看手部姿态与每个关节的反馈。'],['capture','触碰采集','按手指标记采样；采集不驱动机械手。'],['records','运行记录','查看实机动作结果、采集报告与活动记录。'],['connection','连接与校准','连接设备，检查显示映射与单关节对应。']];
  const paths={motion:'M9 6l9 6-9 6z M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',parameters:'M5 3v5m0 4v9m7-18v10m0 4v4m7-18v3m0 4v11 M2 8h6v4H2z M9 13h6v4H9z M16 6h6v4h-6z',feedback:'M4 21V11h4v10m4 0V3h4v18m4 0v-7h3v7 M2 21h22',capture:'M6 3h10l3 3v15H6z M9 8h7 M9 12h7 M9 16h4',records:'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M12 6v6l4 2',connection:'M9 15l6-6 M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0'};
  pages.splice(1,0,['library','动作库','官方示例、数字、报时与字母造型。'],['interaction','触碰互动','真实反馈、识别状态与反应流程。']);
  paths.library=paths.motion;paths.interaction=paths.feedback;
  const nav=document.createElement('nav');nav.className='directory';nav.setAttribute('aria-label','功能目录');
  const area=document.createElement('main');area.className='page-area';const views={};
  for(const [key,title,description] of pages){
    const a=document.createElement('a');a.href='#'+key;a.dataset.page=key;
    a.innerHTML=`<svg viewBox="0 0 26 24" aria-hidden="true"><path d="${paths[key]}"/></svg><span>${title}</span>`;nav.append(a);
    const s=document.createElement('section');s.className='workspace-page';s.id='page-'+key;s.hidden=true;
    s.innerHTML=`<header class="page-heading"><h2 tabindex="-1">${title}</h2><p>${description}</p></header><div class="page-body"></div>`;
    area.append(s);views[key]=s.querySelector('.page-body');
  }
  const advanced=document.createElement('details');advanced.className='nav-advanced';
  advanced.innerHTML='<summary><span id="nav-tools-title">更多工具</span></summary><div class="nav-tool-links"></div>';
  for(const key of ['interaction','capture','records'])advanced.lastElementChild.append(nav.querySelector(`[data-page="${key}"]`));
  nav.append(advanced);nav.querySelector('[data-page="motion"]').hidden=true;
  for(const key of ['library','feedback','parameters','connection'])nav.insertBefore(nav.querySelector(`[data-page="${key}"]`),advanced);
  const oldMain=document.querySelector('main.workspace'),bottom=document.querySelector('.bottom-grid');
  oldMain.before(nav,area);
  const connectionPanel=get('connect').closest('section');views.connection.append(connectionPanel);
  const taskPanel=document.querySelector('.task-choices').closest('section');views.capture.append(taskPanel,get('record').closest('section'));
  const motionLayout=document.createElement('div');motionLayout.className='motion-layout';
  const motionControls=document.createElement('div');motionControls.className='motion-controls';
  const preview=get('playback-panel').closest('details');
  // Reuse the original controls and their event listeners; only reorganize display.
  const real=get('real-motion-panel'),trial=get('trial-action').parentElement;
  const motionFields=document.createElement('div');motionFields.className='motion-fields';
  const firstLabel=document.querySelector('label[for="trial-action"]');firstLabel.before(motionFields);
  const sourceNote=get('trial-action').nextElementSibling;
  const speedContainer=get('trial-speed').parentElement;
  for(const id of ['trial-action','trial-amplitude','trial-cycles','trial-speed']){
    const field=document.createElement('div');field.className='motion-field';if(id==='trial-action')field.classList.add('motion-field-wide');
    field.append(document.querySelector(`label[for="${id}"]`),get(id));motionFields.append(field);
  }
  const notes=document.createElement('details');notes.className='motion-notes';notes.innerHTML='<summary>动作来源与参数说明</summary>';
  notes.append(real.querySelector(':scope > p'),get('active-motion-parameters').parentElement,sourceNote,speedContainer);
  get('trial-clear').closest('label').classList.add('workspace-confirmation');
  trial.append(notes);
  // Dynamic device warnings remain visible; only the long policy explanation folds.
  const policy=get('warning-policy'),policyNotes=document.createElement('details');policyNotes.className='motion-notes';policyNotes.innerHTML='<summary>告警等级与完成条件</summary>';
  policyNotes.append(policy.querySelector('p'),policy.querySelector('details'));policy.append(policyNotes);
  motionControls.append(get('real-motion-panel'),preview);const motionDock=document.createElement('div');motionDock.className='viewer-dock';
  motionLayout.append(motionControls,motionDock);views.motion.append(motionLayout);
  const history=get('motion-history').closest('.hardware-controls');history.parentElement.remove();views.records.append(history);
  for(const child of [...bottom.children])views.records.append(child);
  views.connection.append(get('probe-joint').closest('details'));
  const mapping=get('mapping-editor');mapping.classList.add('panel','folded-panel');mapping.querySelector('summary').id='mapping-tools-title';mapping.querySelector('summary').textContent='三维显示映射（首次校准）';views.connection.append(mapping);
  const feedbackPanel=document.querySelector('.feedback-panel');views.feedback.append(feedbackPanel);
  const pose=get('pose-console');const feedbackDock=get('visual-slot');
  const parking=document.createElement('div');parking.hidden=true;parking.id='viewer-parking';app.append(parking);parking.append(pose);
  oldMain.remove();bottom.remove();
  const status=document.createElement('a');status.id='shell-connection';status.href='#connection';status.title='前往连接与校准';status.textContent='未连接';document.querySelector('.top-actions').prepend(status);
  const connectLink=document.createElement('a');connectLink.href='#connection';connectLink.className='connection-shortcut';connectLink.textContent='连接设备';document.querySelector('.top-actions').append(connectLink);
  get('runtime-mode').hidden=true;
  const enlarge=document.createElement('button');enlarge.type='button';enlarge.id='viewer-enlarge';enlarge.textContent='放大画面';enlarge.addEventListener('click',()=>get('presentation').click());pose.querySelector('.pose-heading').append(enlarge);
  const jump=document.createElement('button');jump.type='button';jump.className='viewer-jump';jump.textContent='查看同步画面 ↓';jump.addEventListener('click',()=>motionDock.scrollIntoView({block:'start'}));get('page-motion').querySelector('.page-heading').append(jump);
  const title=document.querySelector('.top p');title.hidden=true;
  let current='library',beforePresentation='library';
  function showPage(){
    const requested=location.hash.slice(1);current=requested==='motion'?'library':views[requested]?requested:'library';
    for(const [key] of pages){get('page-'+key).hidden=key!==current;const a=nav.querySelector(`[data-page="${key}"]`);if(key===current)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}
    document.body.dataset.page=current;const more=nav.querySelector('details');if(more){const tool=['capture','interaction','records'].includes(current);more.classList.toggle('is-current',tool);if(window.innerWidth>700&&tool)more.open=true;else if(window.innerWidth<=700)more.open=false;}
    if(!window.wujiFloating?.isFloating())(['motion','library'].includes(current)?motionDock:current==='feedback'?feedbackDock:parking).append(pose);
    document.title=(window.WujiLocale?window.WujiLocale.text(current):pages.find(x=>x[0]===current)[1])+' · Wuji Hand Studio';
    window.dispatchEvent(new CustomEvent('workspace-page',{detail:current}));
    window.scrollTo({top:0,behavior:'instant'});
  }
  nav.addEventListener('click',e=>{const a=e.target.closest('a');if(!a)return;if(document.body.classList.contains('presentation'))get('presentation').click();});
  document.addEventListener('pointerdown',e=>{if(window.innerWidth<=700&&!advanced.contains(e.target))advanced.open=false;});
  advanced.addEventListener('keydown',e=>{if(e.key==='Escape'){advanced.open=false;advanced.querySelector('summary').focus();}});
  window.addEventListener('hashchange',showPage);showPage();
  get('presentation').addEventListener('click',()=>{const enlarged=document.body.classList.contains('presentation');enlarge.textContent=enlarged?'退出放大':'放大画面';if(enlarged){beforePresentation=current;location.hash='feedback';}else location.hash=beforePresentation;});
  status.addEventListener('click',()=>{if(document.body.classList.contains('presentation'))get('presentation').click();});
  window.addEventListener('console-state',e=>{const s=e.detail;status.textContent=({connected:s.stale?'反馈过期':'设备已连接',connecting:'连接中',disconnected:'未连接',error:'连接未完成'})[s.connection]||'状态未知';status.classList.toggle('is-connected',s.connection==='connected'&&!s.stale);});
  window.addEventListener('console-offline',()=>{status.textContent='服务未连接';status.classList.remove('is-connected');});
  window.WujiWorkspace={views,showPage,viewerHome:()=>['motion','library'].includes(current)?motionDock:current==='feedback'?feedbackDock:parking};
})();
