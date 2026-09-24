/* Select/apply official solver in glove-only preview. Starting hand follow remains separate. */
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
 <div class="rt-runtime"><p id="rt-runtime-state" role="status"></p><div class="wb-actions"><button id="rt-install"></button><button id="rt-apply" class="primary"></button><button id="rt-sdk"></button></div></div>
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
 function controls(){runtime();for(const id of ['rt-reload','rt-default','rt-save','rt-export'])$(id).disabled=busy||(id!=='rt-reload'&&!context)||(stale&&id!=='rt-reload');grid?.setDisabled(busy||stale);pinch?.setDisabled(busy||stale);$('rt-alpha').disabled=$('rt-delta').disabled=busy||stale;}
 function changed(){dirty=true;runtime();$('rt-status').textContent=t('有未保存修改','Unsaved changes');$('rt-export-result').hidden=true;}
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
 async function load(){if(busy)return;busy=true;controls();try{context=(await request('retarget_tuning_context')).tuning;fill(context.values);stale=dirty=false;binding();$('rt-status').textContent=t('参数已载入。保存与应用分别操作。','Parameters loaded. Save and apply are separate actions.');$('rt-export-result').hidden=true;}catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 async function save(exporting){if(busy||!context||stale)return;busy=true;controls();try{
  const v=values(),result=await request(exporting?'retarget_tuning_export':'retarget_tuning_save',{binding:context.binding,values:v,revision:context.revision});
  if(exporting){$('rt-yaml').value=result.tuning_export.yaml;$('rt-export-result').hidden=false;$('rt-export-result').open=true;$('rt-status').textContent=t('已生成 YAML；尚未应用到当前手套或机械手。','YAML generated; not applied to the current glove or hand.');}
  else{context=result.tuning;dirty=false;binding();$('rt-status').textContent=t('参数已保存。点击“应用到开源映射”才会切换当前预览。','Saved. Apply to open mapper to switch the preview.');}
 }catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 $('rt-reload').onclick=()=>{if(!dirty||confirm(t('放弃未保存修改并重新载入？','Discard unsaved edits and reload?')))load()};
 $('rt-default').onclick=()=>{if(context){fill(context.defaults);changed()}};$('rt-save').onclick=()=>save(false);$('rt-export').onclick=()=>save(true);
 for(const id of ['rt-alpha','rt-delta'])$(id).oninput=changed;
 for(const id of ['rt-scale-grid','rt-pinch-grid'])$(id).addEventListener('input',changed);

 function runtime(){
  const r=state?.solver_runtime||{},g=state?.glove||{},live=g.stream?.solver;
  const attached=!!g.feedback?.device_id||!!g.hardware?.active||state?.connection==='connected';
  $('rt-install').textContent=t('准备求解环境','Prepare solver environment');
  $('rt-apply').textContent=t('应用到开源映射','Apply to open mapper');$('rt-sdk').textContent=t('使用 SDK 映射','Use SDK mapper');
  $('rt-install').disabled=!!r.busy||!!g.busy||attached||r.ready===true;
  $('rt-apply').disabled=busy||stale||!context||!r.ready||attached;
  $('rt-sdk').disabled=busy||stale||!context||attached;
  let text=r.busy?t('正在准备独立求解环境…','Preparing isolated solver environment…'):r.ready?t('求解环境已就绪','Solver environment ready'):r.error||t('首次使用先准备求解环境，需要联网下载依赖。','Prepare the solver first; initial dependency download needs internet.');
  if(live?.applied&&g.stream?.fresh){text+=' · '+(live.engine==='official_open'?t('当前：官方开源映射','Active: official open mapper'):t('当前：SDK 映射','Active: SDK mapper'));
    if(live.engine==='official_open'){text+=' · '+(live.values_sha256===context?.values_sha256&&!dirty?t('本表已生效','This table is applied'):t('当前使用旧参数；本表尚未应用','Older parameters active; this table is not applied'));if(Number.isFinite(live.solve_ms))text+=' · '+live.solve_ms.toFixed(1)+' ms';}}
  else if(context)text+=' · '+t('下次连接：','Next connection: ')+(context.engine==='official_open'?t('官方开源映射','Official open mapper'):'SDK');
  if(attached)text+=' · '+t('切换前请断开机械手反馈','Disconnect hand feedback before changing solver');
  $('rt-runtime-state').textContent=text;
 }
 $('rt-install').onclick=async()=>{try{await request('solver_install');}catch(e){$('rt-status').textContent=e.message}};
 async function applyEngine(engine){if(busy||!context||stale)return;busy=true;controls();try{
  const result=await request('retarget_engine_select',{binding:context.binding,revision:context.revision,engine,values:engine==='official_open'?values():context.values});
  context=result.tuning;dirty=false;binding();$('rt-status').textContent=t('选择已保存；连接中则等待控制端确认新帧。','Selection saved; if connected, wait for controller confirmation and a fresh frame.');
 }catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 $('rt-apply').onclick=()=>applyEngine('official_open');$('rt-sdk').onclick=()=>applyEngine('sdk');

 function labels(){
  a.textContent=t('实时输出调整','Live output adjustment');b.textContent=t('官方参数表','Official parameter table');
  const texts={title:['官方映射参数表','Official retargeting parameters'],mode:['配置草稿 · 不影响当前连接','Configuration draft · does not affect this connection'],doc:['官方字段说明 ↗','Official field reference ↗'],scope:['使用随软件固定的官方开源算法。二代使用与画面一致的 Beta 2 模型；映射在内置 Linux 独立环境运行。参数更改只在手套预览中应用，实机跟随仍由你单独启动。','Uses the pinned official open-source solver in an isolated built-in Linux environment. Hand 2 uses the same Beta 2 model as the viewer. Apply during glove-only preview; hand follow is started separately.'],'scale-title':['五指比例','Finger target scales'],'scale-note':['每格缩放“手腕到该关键点”的向量，不是电机力度或逐节骨长。1 表示保持原比例；支持从表格粘贴多行数值。','Each cell scales a wrist-to-landmark vector, not motor force or individual bone length. 1 preserves the scale. Paste rows from a spreadsheet.'],'advanced-label':['对指距离与平滑','Pinch distances and smoothing'],'pinch-title':['拇指配对阈值 · d1 < d2','Thumb pairing thresholds · d1 < d2'],'alpha-label':['输出低通 lp_alpha · 越小越平滑、延迟越大','Output low-pass lp_alpha · smaller adds smoothing and lag'],'delta-label':['逐帧变化权重 norm_delta','Frame-change regularization norm_delta'],reload:['重新载入','Reload'],default:['填入官方默认值','Fill official defaults'],save:['保存草稿','Save draft'],export:['生成 YAML','Generate YAML'],'export-label':['导出配置内容','Export configuration'],'export-help':['复制以下完整 YAML，在官方仓库 example/config 下保存为对应配置文件，再用 tuning_tool.py 加载。模型路径沿用官方模板；软件内运行与导出是两个独立入口。','Copy the full YAML into the matching config file under the official repository’s example/config and load it with tuning_tool.py. Model paths follow the official template. In-app execution and export are separate actions.']};
  for(const [id,pair] of Object.entries(texts))$('rt-'+id).textContent=t(...pair);
  $('rt-help-label').textContent=t('参数含义与使用方法','Parameter meanings and usage');
  $('rt-mode').textContent=t('参数格子 → 开源映射预览 → 单独开启实机跟随','Parameter grid → Open mapper preview → Separate hand follow');runtime();
  if(grid){grid.setLabels(rows(),cols());pinch.setLabels(rows().slice(1),pinCols());grid.setLanguage(window.WujiLocale?.lang||'zh');pinch.setLanguage(window.WujiLocale?.lang||'zh')}binding();
 }
 window.addEventListener('console-state',e=>{state=e.detail;runtime();if(!started&&state?.csrf){started=true;load();if(!state.glove?.busy)request('solver_probe').catch(()=>{})}if(context&&state?.device_profile&&(state.device_profile.generation!==context.binding.generation||state.device_profile.side!==context.binding.side)){stale=true;controls();$('rt-status').textContent=t('手的型号已改变，请重新载入参数表。','Hand model changed; reload the parameter table.');}});
 window.addEventListener('retarget-binding-changed',()=>{if(!context)return;stale=true;controls();$('rt-status').textContent=t('设备配对已改变。重新载入后再编辑。','Device pairing changed. Reload before editing.');});
 window.addEventListener('wuji-language',labels);labels();select(false);controls();
})();
