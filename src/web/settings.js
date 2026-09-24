/* Settings reuse the existing controls and handlers; no second copy of preferences. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),L=window.WujiLocale,t=(zh,en)=>L.lang==='en'?en:zh;
 const body=$('page-settings')?.querySelector('.page-body');if(!body)return;
 const directory=document.querySelector('.directory'),advanced=directory.querySelector('.nav-advanced');
 const devicesLink=directory.querySelector('[data-page=devices]');if(devicesLink)directory.insertBefore(devicesLink,advanced);
 directory.insertBefore(directory.querySelector('[data-page=settings]'),directory.querySelector('.nav-brand-note'));
 const textNodes=[];
 function text(node,zh,en){textNodes.push([node,zh,en]);node.textContent=t(zh,en);return node;}
 function el(tag,zh,en,className){const node=document.createElement(tag);if(className)node.className=className;if(zh!==undefined)text(node,zh,en);return node;}
 const appearance=$('wb-appearance-title').closest('.wb-card');
 const tabs=el('div',undefined,undefined,'hub-tabs settings-tabs');tabs.setAttribute('role','tablist');
 const sections={},definitions=[['appearance','外观','Appearance'],['devices','连接与设备','Connections'],['tools','数据与工具','Data & tools']];
 for(const [key,zh,en] of definitions){
  const button=el('button',zh,en);button.type='button';button.id='settings-tab-'+key;button.setAttribute('role','tab');button.setAttribute('aria-controls','settings-'+key);tabs.append(button);
  const section=el('section',undefined,undefined,'settings-section');section.id='settings-'+key;section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby',button.id);sections[key]=section;
  button.onclick=()=>show(key);
 }
 function show(key,focus=false){for(const [name,node] of Object.entries(sections)){node.hidden=name!==key;const tab=$('settings-tab-'+name);tab.setAttribute('aria-selected',String(name===key));tab.tabIndex=name===key?0:-1;if(focus&&name===key)tab.focus();}}
 tabs.addEventListener('keydown',event=>{const keys=definitions.map(x=>x[0]),i=keys.findIndex(key=>$('settings-tab-'+key)===event.target);if(i<0)return;
  let next;if(event.key==='ArrowRight')next=(i+1)%keys.length;else if(event.key==='ArrowLeft')next=(i+keys.length-1)%keys.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=keys.length-1;else return;
  event.preventDefault();show(keys[next],true);
 });
 body.replaceChildren(tabs,...Object.values(sections));sections.appearance.append(appearance);
 function card(parent,zh,en,noteZh,noteEn){const node=el('div',undefined,undefined,'wb-card');node.append(el('h3',zh,en));if(noteZh)node.append(el('p',noteZh,noteEn));parent.append(node);return node;}
 const discovery=card(sections.devices,'自动发现','Automatic discovery','连接仅开启反馈或预览；设备跟随与动作播放仍由你启动。','Connecting starts feedback or preview. You start follow and motion separately.');
 for(const id of ['wb-auto-hand-label','wb-auto-glove-label']){const label=$(id),row=label.closest('.wb-row');row.classList.add('settings-row','settings-discovery');label.append(label.querySelector('input'));discovery.append(row);}
 discovery.append(el('p','发现多只设备时需要选择；网络或控制端未就绪时，在连接页查看具体原因。','Select a device when multiple are found. Connection details show network or controller issues.','settings-footnote'));
 const appearanceRow=$('wb-theme').closest('.wb-row');appearanceRow.classList.add('settings-row');
 const languageRow=el('div',undefined,undefined,'wb-row settings-row'),languageLabel=el('label','语言','Language'),language=el('select');language.id='settings-language';languageLabel.htmlFor=language.id;
 language.append(new Option('简体中文','zh'),new Option('English','en'));language.value=L.lang;language.onchange=()=>L.setLanguage(language.value);languageRow.append(languageLabel,language);appearanceRow.after(languageRow);
 // Move, do not clone: checked state, native callbacks and persistence stay intact.
 const oldPreferenceSection=$('desktop-transparency').closest('.desktop-menu-section');
 for(const id of ['desktop-transparency','desktop-motion']){const label=$(id).closest('label');label.className='settings-row settings-toggle';appearance.append(label);}
 oldPreferenceSection.remove();
 const menuLink=el('button','设置…','Settings…','desktop-menu-row');menuLink.id='desktop-settings';menuLink.type='button';
 menuLink.onclick=()=>{$('desktop-menu').close();location.hash='#settings';window.WujiWorkspace.showPage();};$('desktop-viewer').before(menuLink);
 appearance.append(el('p','系统不支持透明材质时使用实色背景。减少动态效果会关闭界面过渡。','A solid background is used when native transparency is unavailable. Reduce motion disables interface transitions.','settings-footnote'));
 function route(parent,zh,en,noteZh,noteEn,callback){const button=el('button',undefined,undefined,'settings-route');button.type='button';const copy=el('span');copy.append(el('strong',zh,en),el('small',noteZh,noteEn));const arrow=el('span','›','›');arrow.setAttribute('aria-hidden','true');button.append(copy,arrow);button.onclick=callback;parent.append(button);return button;}
 const openPage=key=>{location.hash='#'+key;window.WujiWorkspace.showPage();};
 const connections=card(sections.devices,'设备工作流','Device workflow');
 route(connections,'手与手套连接','Hand & glove connections','设备识别、控制端与实时反馈','Discovery, controller and live feedback',()=>window.WujiConnectionHub.show('device'));
 route(connections,'手套可视化与遥操作','Glove visualization & teleoperation','连接手套、检查映射、开始或停止跟随','Connect, inspect mapping, start or stop follow',()=>window.WujiConnectionHub.show('visual'));
 route(connections,'手型标定','Hand-model calibration','SDK 用户、左右手标定与状态','SDK profiles, hand side and calibration status',()=>window.WujiConnectionHub.show('calibration'));
 route(connections,'手套 → 手映射','Glove → hand mapping','各关节幅度、偏移与平滑','Per-joint gain, offset and smoothing',()=>window.WujiConnectionHub.show('retarget'));
 if($('page-devices'))route(connections,'多手工作区','Multiple hands','为每只设备保留独立连接与节目单','Independent connections and playlists',()=>openPage('devices'));
 const controls=card(sections.tools,'控制与记录','Control & records');
 route(controls,'动作参数','Motion parameters','Kp / Kd、发送频率、轨迹速度与电流','Kp / Kd, command rate, trajectory speed and current',()=>openPage('parameters'));
 route(controls,'官方诊断','Official diagnostics','运行检查并查看设备报告','Run checks and review device reports',()=>openPage('doctor'));
 route(controls,'运行记录','Session records','查看记录与导出数据','Review records and export data',()=>openPage('records'));
 const tools=card(sections.tools,'本机数据','Local data','设置和私人模型保存在本机；公开程序不包含私人训练权重。','Preferences and private models stay on this computer. The public app contains no private weights.');
 // Existing desktop actions retain their native availability and status handling.
 for(const [target,zh,en,noteZh,noteEn] of [
  ['desktop-folder','打开数据文件夹','Open data folder','配置、运行记录和模型包','Configuration, records and model packs'],
  ['desktop-model','导入私人模型','Import private model','仅提供已接入的离线识别能力','For the integrated offline recognition workflow'],
  ['desktop-viewer','独立三维窗口','Separate 3D window','移动窗口、调整视角','Move the viewer and adjust the camera']]){
  const button=route(tools,zh,en,noteZh,noteEn,()=>{window.WujiDesktop.setMenu(true);$(target).click();});button.dataset.desktopAction=target;button.disabled=$(target).disabled;
 }
 const about=card(sections.tools,'关于工作台','About Hand Workbench');about.append(el('p','非官方个人展示工具，不代表舞肌科技官方产品。','Unofficial personal demonstration tool, not a Wuji Technology product.'));
 const version=el('p');version.id='settings-version';about.append(version);
 function syncNative(){for(const button of tools.querySelectorAll('[data-desktop-action]'))button.disabled=$(button.dataset.desktopAction).disabled;version.textContent=$('desktop-about').textContent;}
 const observer=new MutationObserver(syncNative);observer.observe($('desktop-about'),{childList:true,subtree:true});syncNative();
 window.addEventListener('wuji-language',()=>{for(const [node,zh,en] of textNodes)node.textContent=t(zh,en);language.value=L.lang;tabs.setAttribute('aria-label',t('设置分类','Settings categories'));syncNative();});
 tabs.setAttribute('aria-label',t('设置分类','Settings categories'));show('appearance');
})();
