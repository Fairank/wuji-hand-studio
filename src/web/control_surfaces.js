/* Presentation only: explicit destination rows preserve existing device actions. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const labels=[];
 function copy(el,zh,en){labels.push([el,zh,en]);el.textContent=t(zh,en);return el}
 function note(host,title,enTitle,body,enBody){
  const box=document.createElement('div');box.className='ui-context-note';
  box.append(copy(document.createElement('strong'),title,enTitle),copy(document.createElement('p'),body,enBody));host.prepend(box);return box;
 }
 const destinations=[['calibration','calibration','手套标定','Calibrate glove','管理用户档案，按官方姿势采集','User profiles and official pose capture'],['visual','glove','遥操作预览','Teleoperation preview','查看映射，单独选择是否启用跟随','Inspect mapping before enabling hand follow']];
 const next=document.querySelector('.cr-next'),rows=[];
 if(next&&window.WorkbenchActionRow){
  next.replaceChildren();for(const [key,icon,zh,en,detail,detailEn] of destinations){
   const row=window.WorkbenchActionRow.create({icon,label:t(zh,en),detail:t(detail,detailEn),onActivate:()=>{window.WujiConnectionHub.show(key);$('hub-tab-'+key)?.focus()}});next.append(row.element);rows.push([row,zh,en,detail,detailEn]);
  }
 }
 // Documentation is secondary, but is still a recognizable control, not a loose URL.
 for(const id of ['hub-calib-doc','rt-doc','glove-config','glove-parameters-link','glove-sdk-doc','glove-calib-doc','performance-export'])$(id)?.classList.add('ui-link-button');
 document.querySelector('.cr-joint-aside a')?.classList.add('ui-link-button');
 const aside=document.querySelector('.cr-calibration-aside');
 if(aside){
  const box=note(aside,'官方采集与求解','Official capture & solver','按当前用户及左右手保存标定，重启手套后仍保留。','Calibration is saved per user and hand side and survives glove restarts.');
  const details=document.createElement('details');box.append(details);
  details.append(copy(document.createElement('summary'),'档案与触觉有什么区别？','Profiles and tactile calibration'));
  details.append(copy(document.createElement('p'),'手型结果保存在运行 SDK 的控制环境中。换电脑或控制环境，需要导入原档案。触觉标定另外按用户与手套编号保存；当前页执行六姿势手型标定。','The hand model is stored in the SDK controller environment. Import the profile when changing computers or runtimes. Tactile calibration is separate and belongs to a user and glove serial. This page runs six-pose hand-model calibration.'));
 }
 const editor=document.querySelector('.cr-mapping-editor');
 if(editor)note(editor,'调整机械手的目标动作','Adjust the robot hand target','比例、偏移和平滑作用于映射输出；不会改写手套的原始测量或手型标定。保存后需要应用，实机跟随单独开启。','Gain, offset and smoothing modify mapped targets, not raw glove measurements or hand calibration. Apply after saving; real-hand follow is enabled separately.');
 const official=$('rt-official');
 if(official)note(official,'调整手套到机械手的求解','Tune glove-to-hand solving','此表作用于工作台内的开源映射引擎，不写入手套。关键点缩放和对指参数在“应用到开源映射”后生效；SDK 内置引擎不读取此表。','This table configures the Workbench open mapping engine, not glove firmware. Landmark scaling and pinch parameters take effect after applying to that engine; the built-in SDK mapper does not read this table.');
 function translate(){for(const [el,zh,en] of labels)el.textContent=t(zh,en);for(const [row,zh,en,detail,detailEn] of rows)row.setLabels({label:t(zh,en),detail:t(detail,detailEn)});for(const id of ['choose-letter','choose-digit']){const el=$(id);if(el)el.setAttribute('aria-label',el.textContent)}}
 window.addEventListener('wuji-language',translate);
 translate();
})();
