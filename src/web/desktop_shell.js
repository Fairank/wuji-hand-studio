/* Desktop commands and appearance only. Motor commands stay in the existing controller. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 let native=null,info=null,state=null;
 const button=document.createElement('button');button.className='app-menu-button';button.id='desktop-menu-button';button.type='button';button.textContent='⋯';button.setAttribute('aria-haspopup','dialog');
 const tools=document.createElement('div');tools.className='desktop-tools';tools.append(button);document.querySelector('.top-actions').append(tools);
 const menu=document.createElement('dialog');menu.id='desktop-menu';
 menu.innerHTML=`<div class="desktop-menu-heading"><strong id="desktop-menu-title"></strong><button id="desktop-menu-close" type="button">×</button></div><section class="desktop-menu-section"><label class="desktop-menu-row"><span id="desktop-transparency-label"></span><input type="checkbox" id="desktop-transparency"></label><label class="desktop-menu-row"><span id="desktop-motion-label"></span><input type="checkbox" id="desktop-motion"></label></section><section class="desktop-menu-section"><button class="desktop-menu-row" id="desktop-viewer"></button><button class="desktop-menu-row" id="desktop-folder"></button><button class="desktop-menu-row" id="desktop-model"></button><button class="desktop-menu-row" id="desktop-quit"></button></section><p class="desktop-about" id="desktop-about"></p><p class="desktop-about" id="desktop-menu-status" role="status"></p>`;
 document.body.append(menu);
 const closeDialog=document.createElement('dialog');closeDialog.id='desktop-close-dialog';
 closeDialog.innerHTML=`<h2 id="desktop-close-title"></h2><p id="desktop-close-note"></p><p id="desktop-close-status" role="status"></p><div class="desktop-close-actions"><button id="desktop-close-cancel"></button><button id="desktop-close-confirm" class="primary"></button></div>`;document.body.append(closeDialog);
 async function applyMaterial(){
  if(!native)return;
  const reduce=document.documentElement.dataset.reduceTransparency==='true'||matchMedia('(prefers-reduced-transparency: reduce)').matches||matchMedia('(forced-colors: active)').matches;
  const result=await native.set_window_material(!reduce);
  document.documentElement.dataset.externalBackdrop=String(result.external_backdrop===true);
  document.documentElement.dataset.nativeMaterial=result.mode||'solid';if(info)info.material=result;
 }
 function appearance(key,value){document.documentElement.dataset[key]=String(value);try{localStorage.setItem('wuji-'+key,String(value));}catch{}if(key==='reduceTransparency')applyMaterial().catch(()=>{});}
 for(const [key,id] of [['reduceTransparency','desktop-transparency'],['reduceMotion','desktop-motion']]){
  let saved=false;try{saved=localStorage.getItem('wuji-'+key)==='true';}catch{}
  appearance(key,saved);$(id).checked=saved;$(id).onchange=()=>appearance(key,$(id).checked);
 }
 function labels(){
  button.title=t('工作台菜单','Workbench menu');button.setAttribute('aria-label',t('工作台菜单','Workbench menu'));
  $('desktop-menu-title').textContent=t('工作台','Workbench');$('desktop-menu-close').setAttribute('aria-label',t('关闭菜单','Close menu'));
  $('desktop-transparency-label').textContent=t('减少透明效果','Reduce transparency');$('desktop-motion-label').textContent=t('减少动态效果','Reduce motion');
  $('desktop-viewer').textContent=t('打开独立模型窗口','Open model window');$('desktop-folder').textContent=t('打开本机数据文件夹','Open local data folder');$('desktop-quit').textContent=t('退出工作台','Quit workbench');
  for(const id of ['desktop-viewer','desktop-folder','desktop-model','desktop-quit'])$(id).disabled=!native;
  $('desktop-model').textContent=t('导入私有模型包…','Import private model pack…');
  $('desktop-about').textContent=info?`Hand Workbench ${info.version}\n${t('灵巧手工作台 · 非官方个人工具','Unofficial personal hand workbench')}`:t('界面预览 · 桌面菜单在软件窗口中可用','Interface preview · Desktop commands are available in the app');
  $('desktop-close-title').textContent=t('结束会话并退出？','End session and quit?');$('desktop-close-note').textContent=t('将停止实机动作、保存正在采集的记录并断开设备。','Stops hand motion, saves the current recording and disconnects devices.');
  $('desktop-close-cancel').textContent=t('返回工作台','Back to workbench');$('desktop-close-confirm').textContent=t('停止并退出','Stop and quit');
 }
 button.onclick=()=>{labels();if(!menu.open)menu.showModal();};$('desktop-menu-close').onclick=()=>menu.close();
 menu.addEventListener('click',e=>{if(e.target===menu){const r=menu.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)menu.close();}});
 async function call(name){try{await native[name]();menu.close();}catch(e){$('desktop-menu-status').textContent=e.message;}}
 $('desktop-viewer').onclick=()=>call('open_viewer');$('desktop-folder').onclick=()=>call('open_data_folder');$('desktop-quit').onclick=()=>call('request_close');
 $('desktop-model').onclick=async()=>{try{const r=await native.import_model_dialog();if(r.ok){$('desktop-menu-status').textContent=t('模型已导入，仅用于离线仿真识别','Model imported for offline simulation only');window.dispatchEvent(new Event('workbench-model-changed'));}else if(!r.cancelled)throw Error(r.error);}catch(e){$('desktop-menu-status').textContent=e.message;}};
 $('desktop-close-cancel').onclick=()=>closeDialog.close();
 $('desktop-close-confirm').onclick=async()=>{
  $('desktop-close-confirm').disabled=true;$('desktop-close-status').textContent=t('正在停止并保存…','Stopping and saving…');
  try{const result=await native.stop_and_close();if(!result.ok)$('desktop-close-status').textContent=result.error;}
  catch(e){$('desktop-close-status').textContent=e.message;}
  finally{$('desktop-close-confirm').disabled=false;}
 };
 const errors=[];
 window.addEventListener('error',e=>{errors.push(String(e.message).slice(0,200));if(errors.length>20)errors.shift();});
 window.addEventListener('unhandledrejection',e=>{errors.push(String(e.reason?.message||e.reason).slice(0,200));if(errors.length>20)errors.shift();});
 window.WujiDesktop={errors,
  setAppearance(key,value){if(key==='language'){window.WujiLocale.setLanguage(value);return;}appearance(key,value);$(key==='reduceMotion'?'desktop-motion':'desktop-transparency').checked=value;},
  setMenu(opened){if(opened&&!menu.open){labels();menu.showModal();}else if(!opened)menu.close();},
  confirmClose(){menu.close();labels();$('desktop-close-status').textContent='';if(!closeDialog.open)closeDialog.showModal();}};
 async function ready(){native=window.pywebview?.api;if(!native)return;info=await native.info();document.documentElement.dataset.desktop='true';document.documentElement.dataset.platform=info.platform;if($('studio-desktop'))$('studio-desktop').hidden=true;await native.set_language(window.WujiLocale.lang);await applyMaterial();labels();}
 window.addEventListener('pywebviewready',()=>ready().catch(e=>{$('desktop-menu-status').textContent=e.message;}));
 if(window.pywebview?.api)ready().catch(()=>{});
 window.addEventListener('wuji-language',()=>{labels();if(native)native.set_language(window.WujiLocale.lang).catch(()=>{});});
 window.addEventListener('console-state',e=>{state=e.detail;});
 for(const query of ['(prefers-reduced-transparency: reduce)','(forced-colors: active)'])matchMedia(query).addEventListener('change',()=>applyMaterial().catch(()=>{}));
 document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key===','){e.preventDefault();menu.close();location.hash='#settings';window.WujiWorkspace.showPage();}
  if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.code==='KeyV'&&native){e.preventDefault();native.open_viewer();}
  if(native&&(e.key==='F5'||(e.ctrlKey||e.metaKey)&&e.code==='KeyR')){e.preventDefault();}
 });
 labels();
})();
