/* Configuration editing only. This module never applies an IK config to hardware. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const host=$('hub-retarget'),output=$('wb-retarget-title')?.closest('.wb-card');
 if(!host||!output||!window.NumericGrid)return;
 let state=null,context=null,grid=null,pinch=null,busy=false,dirty=false,stale=false,started=false,width=3;
 const fingers=['thumb','index','middle','ring','pinky'];
 const rows=()=>fingers.map((key,i)=>({key,label:t(['拇指','食指','中指','无名指','小指'][i],['Thumb','Index','Middle','Ring','Pinky'][i])}));
 const cols=()=> (width===4?['MCP','PIP','DIP','TIP']:['PIP','DIP','TIP']).map(key=>({key,label:key==='TIP'?t('指尖 TIP','Fingertip TIP'):key}));
 const pinCols=()=>[{key:'d1',label:'d1 · cm'},{key:'d2',label:'d2 · cm'}];
 const switches=document.createElement('div');switches.className='rt-segments';switches.setAttribute('role','tablist');
 const a=document.createElement('button'),b=document.createElement('button');a.type=b.type='button';a.id='rt-output-tab';b.id='rt-official-tab';a.setAttribute('role','tab');b.setAttribute('role','tab');
 switches.append(a,b);host.prepend(switches);
 const panel=document.createElement('section');panel.className='wb-card rt-tuning';panel.id='rt-official';panel.hidden=true;
 panel.innerHTML=`<header class="rt-heading"><div><h3 id="rt-title"></h3><p id="rt-mode"></p></div><a id="rt-doc" href="https://docs.wuji.tech/docs/en/wuji-retargeting/latest/tuning/" target="_blank" rel="noopener"></a></header>
 <p id="rt-binding"></p><p id="rt-scope"></p><h4 id="rt-scale-title"></h4><p id="rt-scale-note"></p><div id="rt-scale-grid"></div>
 <details class="rt-advanced"><summary id="rt-advanced-label"></summary><h4 id="rt-pinch-title"></h4><div id="rt-pinch-grid"></div><div class="rt-fields"><label><span id="rt-alpha-label"></span><input id="rt-alpha" type="number" min="0" max="1" step="0.01"></label><label><span id="rt-delta-label"></span><input id="rt-delta" type="number" min="0" step="0.01"></label></div></details>
 <div class="wb-actions"><button id="rt-reload"></button><button id="rt-default"></button><button id="rt-save"></button><button id="rt-export" class="primary"></button></div>
 <p id="rt-status" role="status" aria-live="polite"></p><details id="rt-export-result" hidden><summary id="rt-export-label"></summary><p id="rt-export-help"></p><textarea id="rt-yaml" readonly rows="12" spellcheck="false" aria-label="YAML"></textarea></details>`;
 host.append(panel);output.id='rt-output';output.setAttribute('role','tabpanel');panel.setAttribute('role','tabpanel');
 const help=document.createElement('details');help.className='rt-help';const helpLabel=document.createElement('summary');helpLabel.id='rt-help-label';help.append(helpLabel,$('rt-scope'),$('rt-scale-note'));$('rt-export-result').before(help);
 output.setAttribute('aria-labelledby',a.id);panel.setAttribute('aria-labelledby',b.id);a.setAttribute('aria-controls',output.id);b.setAttribute('aria-controls',panel.id);
 function select(official){output.hidden=official;panel.hidden=!official;a.setAttribute('aria-selected',String(!official));b.setAttribute('aria-selected',String(official));a.tabIndex=official?-1:0;b.tabIndex=official?0:-1;if(official&&!context)load()}
 a.onclick=()=>select(false);b.onclick=()=>select(true);
 switches.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();select(panel.hidden);(panel.hidden?a:b).focus()}};
 async function request(name,values={}){
  if(!state?.csrf)throw Error(t('正在连接工作台服务','Connecting to workbench service'));
  const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name,...values})});
  const data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||'Request failed');return data;
 }
 function controls(){for(const id of ['rt-reload','rt-default','rt-save','rt-export'])$(id).disabled=busy||(id!=='rt-reload'&&!context)||(stale&&id!=='rt-reload');grid?.setDisabled(busy||stale);pinch?.setDisabled(busy||stale);$('rt-alpha').disabled=$('rt-delta').disabled=busy||stale;}
 function changed(){dirty=true;$('rt-status').textContent=t('有未保存修改','Unsaved changes');$('rt-export-result').hidden=true;}
 function fill(value){
  const matrix=fingers.map(f=>value.segment_scaling[f]),pins=fingers.slice(1).map(f=>[value.pinch_thresholds[f].d1,value.pinch_thresholds[f].d2]);
  if(width!==matrix[0].length){width=matrix[0].length;grid?.destroy();pinch?.destroy();grid=pinch=null;}
  if(!grid){grid=NumericGrid.mount($('rt-scale-grid'),{rows:rows(),columns:cols(),value:matrix,onChange:changed,language:window.WujiLocale?.lang});pinch=NumericGrid.mount($('rt-pinch-grid'),{rows:rows().slice(1),columns:pinCols(),value:pins,onChange:changed,language:window.WujiLocale?.lang});}
  else {grid.setValue(matrix);pinch.setValue(pins)}
  $('rt-alpha').value=value.lp_alpha;$('rt-delta').value=value.norm_delta;
 }
 function values(){
  const matrix=grid.read(),pins=pinch.read(),alpha=$('rt-alpha').value.trim(),delta=$('rt-delta').value.trim();
  if(!alpha||!delta||![Number(alpha),Number(delta)].every(Number.isFinite))throw Error(t('请完整填写数值','Complete every numeric field'));
  return {segment_scaling:Object.fromEntries(fingers.map((f,i)=>[f,matrix[i]])),pinch_thresholds:Object.fromEntries(fingers.slice(1).map((f,i)=>[f,{d1:pins[i][0],d2:pins[i][1]}])),lp_alpha:Number(alpha),norm_delta:Number(delta)};
 }
 function binding(){if(context){const x=context.binding;$('rt-binding').textContent=`${x.generation} · ${x.side} · ${t('手套','Glove')} ${x.glove_serial||'—'} · ${t('机械手','Hand')} ${x.hand_serial||'—'} · ${t('用户','User')} ${x.sdk_user||'—'} · v${context.revision}`}}
 async function load(){if(busy)return;busy=true;controls();try{context=(await request('retarget_tuning_context')).tuning;fill(context.values);stale=dirty=false;binding();$('rt-status').textContent=t('已载入。仅用于官方开源调参工具。','Loaded. For the official open-source tuning tool only.');$('rt-export-result').hidden=true;}catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 async function save(exporting){if(busy||!context||stale)return;busy=true;controls();try{
  const v=values(),result=await request(exporting?'retarget_tuning_export':'retarget_tuning_save',{binding:context.binding,values:v,revision:context.revision});
  if(exporting){$('rt-yaml').value=result.tuning_export.yaml;$('rt-export-result').hidden=false;$('rt-export-result').open=true;$('rt-status').textContent=t('已生成 YAML；尚未应用到当前手套或机械手。','YAML generated; not applied to the current glove or hand.');}
  else{context=result.tuning;dirty=false;binding();$('rt-status').textContent=t('已保存参数草稿；当前 SDK 实时映射未改变。','Draft saved; current SDK live mapping is unchanged.');}
 }catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 $('rt-reload').onclick=()=>{if(!dirty||confirm(t('放弃未保存修改并重新载入？','Discard unsaved edits and reload?')))load()};
 $('rt-default').onclick=()=>{if(context){fill(context.defaults);changed()}};$('rt-save').onclick=()=>save(false);$('rt-export').onclick=()=>save(true);
 for(const id of ['rt-alpha','rt-delta'])$(id).oninput=changed;
 for(const id of ['rt-scale-grid','rt-pinch-grid'])$(id).addEventListener('input',changed);
 function labels(){
  a.textContent=t('实时输出调整','Live output adjustment');b.textContent=t('官方参数表','Official parameter table');
  const texts={title:['官方映射参数表','Official retargeting parameters'],mode:['配置草稿 · 不影响当前连接','Configuration draft · does not affect this connection'],doc:['官方字段说明 ↗','Official field reference ↗'],scope:['用于 Wuji 开源 retargeting 的 tuning_tool.py。当前 SDK 内置映射没有开放这些参数，保存不会自动应用。','For tuning_tool.py in Wuji open-source retargeting. The current SDK built-in mapper does not expose these fields; saving does not apply them.'],'scale-title':['五指比例','Finger target scales'],'scale-note':['每格缩放“手腕到该关键点”的向量，不是电机力度或逐节骨长。1 表示保持原比例；支持从表格粘贴多行数值。','Each cell scales a wrist-to-landmark vector, not motor force or individual bone length. 1 preserves the scale. Paste rows from a spreadsheet.'],'advanced-label':['对指距离与平滑','Pinch distances and smoothing'],'pinch-title':['拇指配对阈值 · d1 < d2','Thumb pairing thresholds · d1 < d2'],'alpha-label':['输出低通 lp_alpha · 越小越平滑、延迟越大','Output low-pass lp_alpha · smaller adds smoothing and lag'],'delta-label':['逐帧变化权重 norm_delta','Frame-change regularization norm_delta'],reload:['重新载入','Reload'],default:['填入官方默认值','Fill official defaults'],save:['保存草稿','Save draft'],export:['生成 YAML','Generate YAML'],'export-label':['导出配置内容','Export configuration'],'export-help':['复制以下完整 YAML，在官方仓库 example/config 下保存为对应配置文件，再用 tuning_tool.py 加载。模型路径沿用官方模板；本工作台尚未运行这套求解器。','Copy the full YAML into the matching config file under the official repository’s example/config and load it with tuning_tool.py. Model paths follow the official template. This workbench does not run that solver.']};
  for(const [id,pair] of Object.entries(texts))$('rt-'+id).textContent=t(...pair);
  $('rt-help-label').textContent=t('参数含义与使用方法','Parameter meanings and usage');
  $('rt-mode').textContent=t('仅保存与导出 · 不应用到当前 SDK','Save/export only · not applied to the current SDK');
  if(grid){grid.setLabels(rows(),cols());pinch.setLabels(rows().slice(1),pinCols());grid.setLanguage(window.WujiLocale?.lang||'zh');pinch.setLanguage(window.WujiLocale?.lang||'zh')}binding();
 }
 window.addEventListener('console-state',e=>{state=e.detail;if(!started&&state?.csrf){started=true;load()}if(context&&state?.device_profile&&(state.device_profile.generation!==context.binding.generation||state.device_profile.side!==context.binding.side)){stale=true;controls();$('rt-status').textContent=t('手的型号已改变，请重新载入参数表。','Hand model changed; reload the parameter table.');}});
 window.addEventListener('retarget-binding-changed',()=>{if(!context)return;stale=true;controls();$('rt-status').textContent=t('设备配对已改变。重新载入后再编辑。','Device pairing changed. Reload before editing.');});
 window.addEventListener('wuji-language',labels);labels();select(false);controls();
})();
