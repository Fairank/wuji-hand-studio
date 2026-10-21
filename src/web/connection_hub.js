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
 const definitions=[['device','设备连接','Devices'],['official','官方可视化','Visualization'],['calibration','手套校准','Calibration'],['retarget','映射调校','Mapping'],['visual','遥操作预览','Teleoperation']];
 for(const [key,zh,en] of definitions){
  const button=document.createElement('button');button.type='button';button.id='hub-tab-'+key;button.dataset.hubTab=key;button.setAttribute('role','tab');button.setAttribute('aria-controls','hub-'+key);button.textContent=t(zh,en);tabs.append(button);
  const section=document.createElement('section');section.className='hub-section';section.id='hub-'+key;section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby',button.id);section.hidden=true;sections[key]=section;
 }
 body.replaceChildren(tabs,...Object.values(sections));
 const officialViz=window.WujiOfficialViz?.mount(sections.official);
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
 const calib=document.createElement('div');calib.className='hub-calibration wb-card';
 calib.innerHTML=`<h3 id="hub-calib-title"></h3><p id="hub-calib-description"></p>
 <p id="hub-calib-cli" class="hub-calib-availability"></p>
 <div class="hub-fields"><label><span id="hub-profile-label"></span><select id="hub-profile"></select></label><label><span id="hub-new-profile-label"></span><input id="hub-new-profile" maxlength="32" autocomplete="off"></label></div>
 <div class="wb-actions"><button id="hub-profile-switch"></button><button id="hub-profile-create"></button><button id="hub-calib-refresh"></button></div>
 <div class="hub-fields"><label><span id="hub-glove-label"></span><select id="hub-calib-device"></select></label><label><span id="hub-side-label"></span><select id="hub-side"><option value="left"></option><option value="right"></option></select></label></div>
 <label class="hub-replace"><input type="checkbox" id="hub-replace"><span id="hub-replace-label"></span></label>
 <div class="wb-actions"><button id="hub-calib-start" class="primary"></button><button id="hub-calib-cancel"></button></div>
 <a href="https://docs.wuji.tech/docs/en/wuji-cli/latest/hand-model-calibration/" target="_blank" rel="noopener" id="hub-calib-doc"></a>`;
 sections.calibration.append(calib);
 const setup=document.createElement('details');setup.className='hub-calibration-setup';setup.open=false;
 const setupLabel=document.createElement('summary');setupLabel.id='hub-calib-setup-label';setup.append(setupLabel);
 const setupFields=[...calib.querySelectorAll('.hub-fields,.hub-replace')];
 const profileActions=$('hub-profile-switch').parentElement;
 const profileModels=document.createElement('p');profileModels.id='hub-calib-models';
 setup.append(setupFields[0],profileActions,...setupFields.slice(1),profileModels);
 $('hub-calib-start').parentElement.before(setup);
 const guideHost=document.createElement('div');guideHost.id='hub-calibration-guide';calib.append(guideHost);
 const guide=window.OfficialCalibration?.mount(guideHost,{language:()=>window.WujiLocale?.lang||'zh'});
 window.WujiConnectionHub={show(key){location.hash='#connection';ws.showPage();select(key)}};
 let active='device',calibration=null,calibrationFresh=false,consoleState=null,busy=false,refreshPending=false,actionError='',refreshError='',renderedCalibration=null,renderedForm='';
 function select(key){if(!sections[key])key='device';active=key;for(const [name,section] of Object.entries(sections)){
   section.hidden=name!==key;const button=$('hub-tab-'+name);button.setAttribute('aria-selected',String(name===key));button.tabIndex=name===key?0:-1;
  }
  if(key==='visual'&&window.wujiFloating?.isFloating()!==true)$('glove-viewer-dock')?.append($('pose-console'));
  else if(window.wujiFloating?.isFloating()!==true)ws.viewerHome()?.append($('pose-console'));
  if(key==='calibration')refresh(true);
  document.body.dataset.hubTab=key;
  officialViz?.syncVisibility();
 }
 tabs.addEventListener('click',event=>{const key=event.target.closest('[data-hub-tab]')?.dataset.hubTab;if(key)select(key)});
 tabs.addEventListener('keydown',event=>{const index=definitions.findIndex(x=>x[0]===active),delta=event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0;if(!delta)return;event.preventDefault();const key=definitions[(index+delta+definitions.length)%definitions.length][0];select(key);$('hub-tab-'+key).focus()});
 window.addEventListener('hashchange',()=>{if(location.hash==='#glove'){location.hash='#connection';select('visual')}});
 window.addEventListener('workspace-page',event=>{if(event.detail==='connection')select(active)});
 if(location.hash==='#glove'){location.hash='#connection';active='visual'}

 function renderStatus(){const hand=consoleState?.connection,glove=consoleState?.glove?.connection;
  $('hub-hand-badge').textContent=t('机械手：','Hand: ')+(hand==='connected'?t('已连接','Connected'):hand==='connecting'?t('连接中','Connecting'):hand==='disconnected'?t('离线','Offline'):t('未知','Unknown'));
  $('hub-glove-badge').textContent=t('手套：','Glove: ')+(glove==='receiving'?t('接收中','Receiving'):glove==='ready'?t('已发现','Discovered'):glove==='connecting'?t('连接中','Connecting'):glove==='disconnected'?t('离线','Offline'):t('未知','Unknown'));
 }
 async function refresh(force=false){if(busy||refreshPending||(!force&&active!=='calibration'))return;refreshPending=true;renderCalibration();try{const response=await fetch('/api/calibration'+(force?'?refresh=1':''),{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error(t('无法读取官方校准状态','Calibration status unavailable'));const next=await response.json();if(!next||typeof next.available!=='boolean')throw Error(t('官方校准状态无效','Invalid calibration status'));calibration=next;calibrationFresh=true;refreshError='';}catch(error){calibrationFresh=false;refreshError=error.message;}finally{refreshPending=false;renderCalibration()}}
 async function action(name,extra={}){if(busy)return;busy=true;actionError='';renderCalibration();try{
   const token=consoleState?.csrf;if(!token)throw Error(t('工作台服务未就绪','Workbench service not ready'));
   const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({name,...extra})});const data=await response.json();if(!response.ok)throw Error(data.error||'Action failed');
   calibration=data.calibration||calibration;if(name==='calibration_start')$('hub-replace').checked=false;
  }catch(error){actionError=error.message}finally{busy=false;renderCalibration();refresh(true)}}
 function renderCalibration(){
  const form=JSON.stringify([busy,refreshPending,calibrationFresh,actionError,refreshError,consoleState?.connection,!!consoleState?.glove?.busy,$('hub-profile').value,$('hub-calib-device').value,$('hub-new-profile').value,$('hub-side').value,$('hub-replace').checked,window.WujiLocale?.lang]);
  if(renderedCalibration===calibration&&renderedForm===form)return;renderedCalibration=calibration;renderedForm=form;
  const c=calibration||{},available=calibrationFresh&&c.available,run=c.run||{},users=(c.users||[]).filter(x=>x&&typeof x==='object'),devices=(c.devices||[]).filter(x=>x&&typeof x==='object');
  $('hub-calib-cli').textContent=!calibrationFresh?t('状态未核对','Status unverified'):c.available?(c.calibration_supported?t('CLI 可用','CLI available'):t('远程 CLI 仅可查看状态','Remote CLI: status only')):t('CLI 未就绪','CLI unavailable');
  const current=c.current||{},profile=current.name||'',old=$('hub-profile').value;
  $('hub-profile').replaceChildren(new Option(t('选择 SDK 用户','Select SDK user'),''),...users.map(u=>new Option(u.name+(u.is_default?' · Default':''),u.name)));
  $('hub-profile').value=[...$('hub-profile').options].some(o=>o.value===old)?old:profile;
  const serial=$('hub-calib-device').value;$('hub-calib-device').replaceChildren(new Option(t('选择已发现手套','Select discovered glove'),''),...devices.filter(d=>d.sn||d.serial).map(d=>new Option(d.sn||d.serial,d.sn||d.serial)));
  if([...$('hub-calib-device').options].some(o=>o.value===serial))$('hub-calib-device').value=serial;
  profileModels.textContent=t('用户已有模型：','Saved user models: ')+['left','right'].map(side=>{const calibrated=current[side+'_hand']?.calibrated;return t(side==='left'?'左手 ':'右手 ',side==='left'?'Left ':'Right ')+(available&&calibrated===true?t('已标定','Calibrated'):available&&calibrated===false?t('未标定','Not calibrated'):t('未知','Unknown'))}).join(' · ');
  if(run.running)setup.open=false;
  for(const id of ['hub-profile','hub-new-profile','hub-calib-device','hub-side','hub-replace'])$(id).disabled=busy||run.running;
  const defaultUser=!profile||profile.toLowerCase()==='default'||users.some(u=>u.name===profile&&u.is_default);
  const hasModel=!!current[$('hub-side').value+'_hand']?.calibrated;
  if(available&&!run.running&&!setup.dataset.offered){setup.open=defaultUser||!$('hub-calib-device').value;setup.dataset.offered='true'}
  $('hub-replace').closest('label').hidden=!hasModel;
  $('hub-profile-switch').disabled=busy||refreshPending||run.running||!available||!$('hub-profile').value||$('hub-profile').value===profile;
  $('hub-profile-create').disabled=busy||refreshPending||run.running||!available||!$('hub-new-profile').value.trim();
  $('hub-calib-refresh').disabled=busy||refreshPending||run.running;
  $('hub-calib-start').disabled=busy||refreshPending||run.running||!available||!c.calibration_supported||defaultUser||!$('hub-calib-device').value||(hasModel&&!$('hub-replace').checked)||consoleState?.connection!=='disconnected'||!!consoleState?.glove?.busy;
  $('hub-calib-cancel').disabled=busy||!run.running;
  let notice=available&&!c.calibration_supported?t('远程 SSH 可查看标定状态；请改用工作台内置或本机 Linux 控制端执行官方标定。','SSH can read calibration status. Use the built-in or local Linux controller for official calibration.'):'';
  if(!run.running&&!actionError&&!refreshError&&!c.error&&available&&c.calibration_supported&&(!run.status||run.status==='idle')){
   const reason=!consoleState?t('工作台服务离线，恢复后再操作。','Workbench service offline; wait for reconnection.'):consoleState.connection!=='disconnected'?t('先断开机械手反馈，再校准手套。','Disconnect hand feedback before glove calibration.'):
    defaultUser?t('展开“选择用户与手套”，创建或选择一个命名用户。','Open user and glove settings to create or choose a named user.'):
    !$('hub-calib-device').value?t('请选择要校准的手套；未发现时刷新状态。','Select a glove; refresh official status if none are listed.'):
    hasModel&&!$('hub-replace').checked?t('此侧已有标定；如需重做，请在设置中确认覆盖。','This side is calibrated. Confirm replacement in settings to recalibrate.'):null;
   if(reason)notice=reason;
  }
  guide?.render(c,{fresh:calibrationFresh,error:[actionError,refreshError,c.error].filter(Boolean).join(' · '),notice});
 }
 $('hub-calib-refresh').onclick=()=>refresh(true);$('hub-profile-switch').onclick=()=>action('calibration_profile_switch',{profile:$('hub-profile').value});$('hub-profile-create').onclick=()=>action('calibration_profile_create',{profile:$('hub-new-profile').value.trim()});
 $('hub-calib-start').onclick=()=>action('calibration_start',{side:$('hub-side').value,serial:$('hub-calib-device').value,replace:$('hub-replace').checked});$('hub-calib-cancel').onclick=()=>action('calibration_cancel');
 for(const id of ['hub-new-profile','hub-side','hub-replace','hub-profile','hub-calib-device'])$(id).addEventListener('input',renderCalibration);
 window.addEventListener('console-state',event=>{consoleState=event.detail;renderStatus();renderCalibration()});
 window.addEventListener('console-offline',()=>{consoleState=null;renderStatus();renderCalibration()});
 function labels(){for(const [key,zh,en] of definitions)$('hub-tab-'+key).textContent=t(zh,en);
  $('hub-intro-note').textContent=t('先连接设备，再校准与调校。连接状态与标定结果分别显示。','Connect first, then calibrate and tune. Connection and calibration are shown separately.');
  $('hub-visual-title').textContent=t('映射与机械手预览','Mapping and hand preview');
  $('hub-visual-note').textContent=t('此处显示工作台独有的映射目标与机械手反馈；手套骨架和触觉数据请使用官方可视化。','View workbench mapping targets and hand feedback here. Use the original visualization for glove skeleton and tactile data.');
  $('hub-real-label').textContent=t('三维：机械手 / 映射预览','3D: hand / mapped preview');$('hub-preview-label').textContent='';
  $('hub-calib-title').textContent=t('手型标定','Hand-model calibration');
  $('hub-calib-setup-label').textContent=t('选择用户与手套','Select user and glove');
  $('hub-calib-description').textContent=t('通过 Wuji CLI 标定，结果按命名用户和左右手保存。此流程不驱动机械手。','Calibrate through Wuji CLI. Results are saved per named user and hand side. This flow does not drive the robot hand.');
  $('hub-profile-label').textContent=t('当前 SDK 用户','Current SDK user');$('hub-new-profile-label').textContent=t('新建命名用户','New named user');$('hub-profile-switch').textContent=t('切换用户','Switch user');$('hub-profile-create').textContent=t('创建并切换','Create and switch');$('hub-calib-refresh').textContent=t('刷新状态','Refresh status');
  $('hub-glove-label').textContent=t('已发现手套','Discovered glove');$('hub-side-label').textContent=t('标定侧','Hand side');$('hub-side').options[0].textContent=t('左手','Left');$('hub-side').options[1].textContent=t('右手','Right');$('hub-replace-label').textContent=t('覆盖这一侧已有标定','Replace existing model for this side');$('hub-calib-start').textContent=t('开始标定','Start calibration');$('hub-calib-cancel').textContent=t('取消标定','Cancel calibration');$('hub-calib-doc').textContent=t('标定说明 ↗','Calibration guide ↗');$('hub-calib-doc').href='https://docs.wuji.tech/docs/'+(window.WujiLocale?.lang==='en'?'en':'zh')+'/wuji-cli/latest/hand-model-calibration/';
  renderStatus();renderCalibration();
 }
 window.addEventListener('wuji-language',labels);labels();select(active);setInterval(()=>{if(active==='calibration'&&calibration?.run?.running)refresh(false)},500);
})();
