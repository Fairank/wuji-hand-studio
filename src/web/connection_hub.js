(()=>{
 'use strict';
 const $=id=>document.getElementById(id),ws=window.WujiWorkspace;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const body=$('page-connection')?.querySelector('.page-body');
 if(!body)return;
 const readingCanvas=document.querySelector('.page-area'),footer=readingCanvas?.querySelector(':scope > footer');
 if(footer)readingCanvas.append(footer);
 const old=[...body.children];
 const tabs=document.createElement('div');tabs.className='hub-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label',t('连接与校准分区','Connection sections'));
 const sections={};
 const definitions=[['device','设备','Device'],['visual','实时可视化','Live visualization'],['calibration','手套校准','Glove calibration'],['retarget','手套 → 手映射','Glove → hand mapping']];
 for(const [key,zh,en] of definitions){
  const button=document.createElement('button');button.type='button';button.id='hub-tab-'+key;button.dataset.hubTab=key;button.setAttribute('role','tab');button.setAttribute('aria-controls','hub-'+key);button.textContent=t(zh,en);tabs.append(button);
  const section=document.createElement('section');section.className='hub-section';section.id='hub-'+key;section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby',button.id);section.hidden=true;sections[key]=section;
 }
 body.replaceChildren(tabs,...Object.values(sections));
 sections.device.append(...old);
 const glovePage=$('page-glove'),gloveBody=glovePage?.querySelector('.page-body');
 if(gloveBody)sections.visual.append(...[...gloveBody.children]);
 const retarget=$('wb-retarget-title')?.closest('.wb-card');if(retarget)sections.retarget.append(retarget);
 const oldGloveNav=document.querySelector('.directory [data-page="glove"]');if(oldGloveNav)oldGloveNav.hidden=true;
 const mappingLink=$('wb-glove-settings-link');if(mappingLink)mappingLink.href='#connection';
 mappingLink?.addEventListener('click',()=>select('retarget'));

 const intro=document.createElement('div');intro.className='hub-intro';
 intro.innerHTML='<div class="hub-badges"><span id="hub-hand-badge"></span><span id="hub-glove-badge"></span></div><p id="hub-intro-note"></p>';
 sections.device.prepend(intro);
 const visualIntro=document.createElement('div');visualIntro.className='hub-intro';
 visualIntro.innerHTML='<h3 id="hub-visual-title"></h3><p id="hub-visual-note"></p><div class="hub-legend"><span id="hub-real-label"></span><span id="hub-preview-label"></span></div>';
 sections.visual.prepend(visualIntro);
 const skeleton=document.createElement('div');skeleton.className='hub-skeleton wb-card';
 skeleton.innerHTML='<div class="hub-skeleton-heading"><h3 id="hub-skeleton-title"></h3><span id="hub-skeleton-state"></span></div><svg id="hub-skeleton-svg" viewBox="0 0 320 320" role="img" aria-label="Glove skeleton"><g id="hub-skeleton-lines"></g><g id="hub-skeleton-points"></g></svg><p id="hub-skeleton-note"></p>';
 const viewerDock=$('glove-viewer-dock');if(viewerDock)viewerDock.after(skeleton);
 const calib=document.createElement('div');calib.className='hub-calibration wb-card';
 calib.innerHTML=`<h3 id="hub-calib-title"></h3><p id="hub-calib-description"></p>
 <div class="hub-calib-state"><span id="hub-calib-cli"></span><span id="hub-calib-left"></span><span id="hub-calib-right"></span></div>
 <div class="hub-fields"><label><span id="hub-profile-label"></span><select id="hub-profile"></select></label><label><span id="hub-new-profile-label"></span><input id="hub-new-profile" maxlength="32" autocomplete="off"></label></div>
 <div class="wb-actions"><button id="hub-profile-switch"></button><button id="hub-profile-create"></button><button id="hub-calib-refresh"></button></div>
 <div class="hub-fields"><label><span id="hub-glove-label"></span><select id="hub-calib-device"></select></label><label><span id="hub-side-label"></span><select id="hub-side"><option value="left"></option><option value="right"></option></select></label></div>
 <label class="hub-replace"><input type="checkbox" id="hub-replace"><span id="hub-replace-label"></span></label>
 <div class="wb-actions"><button id="hub-calib-start" class="primary"></button><button id="hub-calib-cancel"></button></div>
 <p id="hub-calib-status" role="status" aria-live="polite"></p><p id="hub-calib-step"></p>
 <a href="https://docs.wuji.tech/docs/en/wuji-studio/latest/calibration/" target="_blank" rel="noopener" id="hub-calib-doc"></a>`;
 sections.calibration.append(calib);
 const setup=document.createElement('details');setup.className='hub-calibration-setup';setup.open=true;
 const setupLabel=document.createElement('summary');setupLabel.id='hub-calib-setup-label';setup.append(setupLabel);
 const setupFields=[...calib.querySelectorAll('.hub-fields,.hub-replace')];
 const profileActions=$('hub-profile-switch').parentElement;
 setup.append(setupFields[0],profileActions,...setupFields.slice(1));
 $('hub-calib-start').parentElement.before(setup);
 const guideHost=document.createElement('div');guideHost.id='hub-calibration-guide';calib.append(guideHost);
 const guide=window.CalibrationGuide?.mount(guideHost,{language:()=>window.WujiLocale?.lang||'zh',onPreview:()=>select('visual')});
 window.WujiConnectionHub={show(key){location.hash='#connection';ws.showPage();select(key)}};
 let active='device',calibration=null,consoleState=null,busy=false,refreshPending=false,lastError='',renderedCalibration=null,renderedForm='';
 function select(key){if(!sections[key])key='device';active=key;for(const [name,section] of Object.entries(sections)){
   section.hidden=name!==key;const button=$('hub-tab-'+name);button.setAttribute('aria-selected',String(name===key));button.tabIndex=name===key?0:-1;
  }
  if(key==='visual'&&window.wujiFloating?.isFloating()!==true)$('glove-viewer-dock')?.append($('pose-console'));
  else if(window.wujiFloating?.isFloating()!==true)ws.viewerHome()?.append($('pose-console'));
  if(key==='calibration')refresh(true);
  document.body.dataset.hubTab=key;
 }
 tabs.addEventListener('click',event=>{const key=event.target.closest('[data-hub-tab]')?.dataset.hubTab;if(key)select(key)});
 tabs.addEventListener('keydown',event=>{const index=definitions.findIndex(x=>x[0]===active),delta=event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0;if(!delta)return;event.preventDefault();const key=definitions[(index+delta+definitions.length)%definitions.length][0];select(key);$('hub-tab-'+key).focus()});
 window.addEventListener('hashchange',()=>{if(location.hash==='#glove'){location.hash='#connection';select('visual')}});
 window.addEventListener('workspace-page',event=>{if(event.detail==='connection')select(active)});
 if(location.hash==='#glove'){location.hash='#connection';active='visual'}

 const bones=[[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[0,9],[9,10],[10,11],[11,12],[0,13],[13,14],[14,15],[15,16],[0,17],[17,18],[18,19],[19,20],[5,9],[9,13],[13,17]];
 function draw(points){const lines=$('hub-skeleton-lines'),dots=$('hub-skeleton-points');lines.replaceChildren();dots.replaceChildren();
  if(!Array.isArray(points)||points.length!==21||points.some(p=>!Array.isArray(p)||p.length<3||p.slice(0,3).some(x=>typeof x!=='number'||!Number.isFinite(x)))){
   skeleton.classList.remove('is-live');
   $('hub-skeleton-state').textContent=t('未接收数据','Not receiving data');return;
  }
  skeleton.classList.add('is-live');
  const xy=points.map(p=>[p[0]+.28*p[2],-p[1]+.18*p[2]]),xs=xy.map(p=>p[0]),ys=xy.map(p=>p[1]);
  const left=Math.min(...xs),top=Math.min(...ys),range=Math.max(Math.max(...xs)-left,Math.max(...ys)-top,1e-4),mapped=xy.map(p=>[160+(p[0]-(left+Math.max(...xs))/2)*260/range,160+(p[1]-(top+Math.max(...ys))/2)*260/range]);
  const svg='http://www.w3.org/2000/svg';for(const [a,b] of bones){const line=document.createElementNS(svg,'line');line.setAttribute('x1',mapped[a][0]);line.setAttribute('y1',mapped[a][1]);line.setAttribute('x2',mapped[b][0]);line.setAttribute('y2',mapped[b][1]);lines.append(line)}
  mapped.forEach((p,i)=>{const dot=document.createElementNS(svg,'circle');dot.setAttribute('cx',p[0]);dot.setAttribute('cy',p[1]);dot.setAttribute('r',i%4===0?4:2.5);dots.append(dot)});
  $('hub-skeleton-state').textContent=t('正在接收 · 21 个关键点','Receiving · 21 landmarks');
 }
 function renderStatus(){const hand=consoleState?.connection,glove=consoleState?.glove?.connection;
  $('hub-hand-badge').textContent=t('机械手：','Hand: ')+(hand==='connected'?t('已连接','Connected'):hand==='connecting'?t('连接中','Connecting'):hand==='disconnected'?t('离线','Offline'):t('未知','Unknown'));
  $('hub-glove-badge').textContent=t('手套：','Glove: ')+(glove==='receiving'?t('接收中','Receiving'):glove==='ready'?t('已发现','Discovered'):glove==='connecting'?t('连接中','Connecting'):glove==='disconnected'?t('离线','Offline'):t('未知','Unknown'));
  draw(consoleState?.glove?.stream?.fresh?consoleState.glove.stream.keypoints:null);
 }
 async function refresh(force=false){if(busy||refreshPending||(!force&&active!=='calibration'))return;refreshPending=true;try{const response=await fetch('/api/calibration'+(force?'?refresh=1':''));if(!response.ok)throw Error('Calibration status unavailable');calibration=await response.json();renderCalibration()}catch(error){lastError=error.message;renderCalibration()}finally{refreshPending=false}}
 async function action(name,extra={}){if(busy)return;busy=true;lastError='';renderCalibration();try{
   const token=consoleState?.csrf;if(!token)throw Error(t('工作台服务未就绪','Workbench service not ready'));
   const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({name,...extra})});const data=await response.json();if(!response.ok)throw Error(data.error||'Action failed');
   calibration=data.calibration||calibration;if(name==='calibration_start')$('hub-replace').checked=false;
  }catch(error){lastError=error.message}finally{busy=false;renderCalibration();refresh(true)}}
 function renderCalibration(){
  const form=JSON.stringify([busy,lastError,consoleState?.connection,!!consoleState?.glove?.busy,$('hub-profile').value,$('hub-calib-device').value,$('hub-new-profile').value,$('hub-side').value,$('hub-replace').checked,window.WujiLocale?.lang]);
  if(renderedCalibration===calibration&&renderedForm===form)return;renderedCalibration=calibration;renderedForm=form;
  const c=calibration||{},run=c.run||{},users=(c.users||[]).filter(x=>x&&typeof x==='object'),devices=(c.devices||[]).filter(x=>x&&typeof x==='object');
  guide?.render(c);
  $('hub-calib-cli').textContent=c.available?(c.calibration_supported?t('官方 CLI 可用','Official CLI available'):t('远程 CLI 仅可查看状态','Remote CLI: status only')):t('官方 CLI 未就绪','Official CLI unavailable');
  const current=c.current||{},profile=current.name||'',old=$('hub-profile').value;
  $('hub-profile').replaceChildren(new Option(t('选择 SDK 用户','Select SDK user'),''),...users.map(u=>new Option(u.name+(u.is_default?' · Default':''),u.name)));
  $('hub-profile').value=[...$('hub-profile').options].some(o=>o.value===old)?old:profile;
  const serial=$('hub-calib-device').value;$('hub-calib-device').replaceChildren(new Option(t('选择已发现手套','Select discovered glove'),''),...devices.filter(d=>d.sn||d.serial).map(d=>new Option(d.sn||d.serial,d.sn||d.serial)));
  if([...$('hub-calib-device').options].some(o=>o.value===serial))$('hub-calib-device').value=serial;
  for(const side of ['left','right']){const calibrated=current[side+'_hand']?.calibrated;$('hub-calib-'+side).textContent=t(side==='left'?'左手：':'右手：',side==='left'?'Left: ':'Right: ')+(c.available&&calibrated===true?t('已校准','Calibrated'):c.available&&calibrated===false?t('未校准','Not calibrated'):t('未知','Unknown'))}
  if(run.running)setup.open=false;
  for(const id of ['hub-profile','hub-new-profile','hub-calib-device','hub-side','hub-replace'])$(id).disabled=busy||run.running;
  const defaultUser=!profile||profile.toLowerCase()==='default'||users.some(u=>u.name===profile&&u.is_default);
  const hasModel=!!current[$('hub-side').value+'_hand']?.calibrated;
  $('hub-replace').closest('label').hidden=!hasModel;
  $('hub-profile-switch').disabled=busy||run.running||!c.available||!$('hub-profile').value||$('hub-profile').value===profile;
  $('hub-profile-create').disabled=busy||run.running||!c.available||!$('hub-new-profile').value.trim();
  $('hub-calib-refresh').disabled=busy||run.running;
  $('hub-calib-start').disabled=busy||run.running||!c.available||!c.calibration_supported||defaultUser||!$('hub-calib-device').value||(hasModel&&!$('hub-replace').checked)||consoleState?.connection!=='disconnected'||!!consoleState?.glove?.busy;
  $('hub-calib-cancel').disabled=busy||!run.running;
  $('hub-calib-status').textContent=lastError||c.error||(c.available&&!c.calibration_supported?t('远程 SSH 可查看标定状态；请改用工作台内置或本机 Linux 控制端执行引导标定。','SSH can read calibration status. Use the built-in or local Linux controller for the guided flow.'):null)||({'collecting':t('正在按官方引导采集','Collecting with official guide'),'completed':t('标定完成','Calibration completed'),'cancelled':t('已取消','Cancelled'),'error':t('官方流程失败','Official flow failed'),'cancelling':t('正在取消','Cancelling')})[run.status]||t('先选择命名用户，再按官方六个姿势完成标定。','Select a named user, then complete the six official poses.');
  $('hub-calib-step').textContent=run.running?t('当前阶段：','Current step: ')+(run.step??'—')+(run.progress!=null?' · '+run.progress:''):run.error||'';
 }
 $('hub-calib-refresh').onclick=()=>refresh(true);$('hub-profile-switch').onclick=()=>action('calibration_profile_switch',{profile:$('hub-profile').value});$('hub-profile-create').onclick=()=>action('calibration_profile_create',{profile:$('hub-new-profile').value.trim()});
 $('hub-calib-start').onclick=()=>action('calibration_start',{side:$('hub-side').value,serial:$('hub-calib-device').value,replace:$('hub-replace').checked});$('hub-calib-cancel').onclick=()=>action('calibration_cancel');
 for(const id of ['hub-new-profile','hub-side','hub-replace','hub-profile','hub-calib-device'])$(id).addEventListener('input',renderCalibration);
 window.addEventListener('console-state',event=>{consoleState=event.detail;renderStatus();renderCalibration()});
 window.addEventListener('console-offline',()=>{consoleState=null;renderStatus()});
 function labels(){for(const [key,zh,en] of definitions)$('hub-tab-'+key).textContent=t(zh,en);
  $('hub-intro-note').textContent=t('手和手套分别显示连接状态；连接并不表示已完成标定。','Hand and glove connection states are separate; a connection does not imply calibration.');
  $('hub-visual-title').textContent=t('两路独立反馈','Two independent feedback views');
  $('hub-visual-note').textContent=t('三维画面显示机械手反馈或当前预览源；下方骨架仅显示手套原始 21 关键点。两者不代表已通过联动遥操作验收。','The 3D viewer shows hand feedback or the selected preview source. The skeleton below shows only 21 raw glove landmarks. Together they do not establish validated teleoperation.');
  $('hub-real-label').textContent=t('三维：机械手 / 映射预览','3D: hand / mapped preview');$('hub-preview-label').textContent=t('骨架：手套原始数据','Skeleton: raw glove data');
  $('hub-skeleton-title').textContent=t('手套骨架','Glove skeleton');$('hub-skeleton-note').textContent=t('仅作实时可视化；姿态按相对坐标缩放。','Live visualization only; pose scaled from relative coordinates.');
  $('hub-calib-title').textContent=t('官方手部模型标定','Official hand-model calibration');
  $('hub-calib-setup-label').textContent=t('选择用户与手套','Select user and glove');
  $('hub-calib-description').textContent=t('按 SDK 用户和左右手分别保存。Default 用户不保存标定；已有标定需明确勾选覆盖。标定过程不驱动机械手。','Saved per SDK user and hand side. Default does not save calibration; replacing an existing model requires explicit consent. Calibration does not drive the robot hand.');
  $('hub-profile-label').textContent=t('当前 SDK 用户','Current SDK user');$('hub-new-profile-label').textContent=t('新建命名用户','New named user');$('hub-profile-switch').textContent=t('切换用户','Switch user');$('hub-profile-create').textContent=t('创建并切换','Create and switch');$('hub-calib-refresh').textContent=t('刷新官方状态','Refresh official status');
  $('hub-glove-label').textContent=t('已发现手套','Discovered glove');$('hub-side-label').textContent=t('标定侧','Hand side');$('hub-side').options[0].textContent=t('左手','Left');$('hub-side').options[1].textContent=t('右手','Right');$('hub-replace-label').textContent=t('覆盖这一侧已有标定','Replace existing model for this side');$('hub-calib-start').textContent=t('开始官方标定','Start official calibration');$('hub-calib-cancel').textContent=t('取消标定','Cancel calibration');$('hub-calib-doc').textContent=t('查看官方六姿势说明 ↗','Official six-pose guide ↗');
  renderStatus();renderCalibration();
 }
 window.addEventListener('wuji-language',labels);labels();select(active);setInterval(()=>{if(active==='calibration'&&calibration?.run?.running)refresh(false)},500);
})();
