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
 const destinations=[['calibration','calibration','手套标定','Calibrate glove','个人档案与官方指导','Personal profile and official guidance'],['visual','glove','遥操作','Teleoperation','选择手套与目标灵巧手','Choose a glove and target hand']];
 const next=document.querySelector('.cr-next'),rows=[];
 if(next&&window.WorkbenchActionRow){
  next.replaceChildren();for(const [key,icon,zh,en,detail,detailEn] of destinations){
   const row=window.WorkbenchActionRow.create({icon,label:t(zh,en),detail:t(detail,detailEn),onActivate:()=>{window.WujiConnectionHub.show(key);if(key==='visual')$('glove-title')?.focus({preventScroll:true});else $('hub-tab-'+key)?.focus()}});next.append(row.element);rows.push([row,zh,en,detail,detailEn]);
  }
 }
 // Documentation is secondary, but is still a recognizable control, not a loose URL.
 for(const id of ['hub-calib-doc','rt-doc','glove-config','glove-parameters-link','glove-sdk-doc','glove-calib-doc','performance-export'])$(id)?.classList.add('ui-link-button');
 document.querySelector('.cr-joint-aside a')?.classList.add('ui-link-button');
 const editor=document.querySelector('.cr-mapping-editor');
 if(editor)note(editor,'调整灵巧手的目标动作','Adjust the robot hand target','比例、偏移和平滑作用于映射输出；不会改写手套的原始测量或手型标定。保存后需要应用，实机跟随单独开启。','Gain, offset and smoothing modify mapped targets, not raw glove measurements or hand calibration. Apply after saving; real-hand follow is enabled separately.');
 const official=$('rt-official');
 if(official)note(official,'调整手套到灵巧手的求解','Tune glove-to-hand solving','此表作用于工作台内的开源映射引擎，不写入手套。关键点缩放和对指参数在“应用到开源映射”后生效；SDK 内置引擎不读取此表。','This table configures the Workbench open mapping engine, not glove firmware. Landmark scaling and pinch parameters take effect after applying to that engine; the built-in SDK mapper does not read this table.');
 function translate(){for(const [el,zh,en] of labels)el.textContent=t(zh,en);for(const [row,zh,en,detail,detailEn] of rows)row.setLabels({label:t(zh,en),detail:t(detail,detailEn)});for(const id of ['choose-letter','choose-digit']){const el=$(id);if(el)el.setAttribute('aria-label',el.textContent)}}
 window.addEventListener('wuji-language',translate);
 translate();
})();
