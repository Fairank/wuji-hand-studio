/* Select/apply official solver in glove-only preview. Starting hand follow remains separate. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const host=$('hub-retarget'),output=$('wb-retarget-title')?.closest('.wb-card');
 if(!host||!output||!window.NumericGrid)return;
 let state=null,context=null,grid=null,pinch=null,busy=false,dirty=false,stale=false,started=false,width=3,serviceOffline=false;
 const fingers=['thumb','index','middle','ring','pinky'];
 const rows=()=>fingers.map((key,i)=>({key,label:t(['拇指','食指','中指','无名指','小指'][i],['Thumb','Index','Middle','Ring','Pinky'][i])}));
 const cols=()=> (width===4?['MCP','PIP','DIP','TIP']:['PIP','DIP','TIP']).map(key=>({key,label:key==='TIP'?t('指尖 TIP','Fingertip TIP'):key}));
 const pinCols=()=>[{key:'d1',label:'d1 · cm'},{key:'d2',label:'d2 · cm'}];
 const switches=document.createElement('div');switches.className='rt-segments';switches.setAttribute('role','tablist');switches.setAttribute('aria-label',t('映射调整方式','Mapping adjustment method'));
 host.classList.add('rt-flow-ready');
 const a=document.createElement('button'),b=document.createElement('button');a.type=b.type='button';a.id='rt-output-tab';b.id='rt-official-tab';a.setAttribute('role','tab');b.setAttribute('role','tab');
 switches.append(a,b);host.prepend(switches);
 const panel=document.createElement('section');panel.className='wb-card rt-tuning';panel.id='rt-official';panel.hidden=true;
 panel.innerHTML=`<header class="rt-heading"><div><h3 id="rt-title"></h3><p id="rt-mode"></p></div><a id="rt-doc" href="https://docs.wuji.tech/docs/en/wuji-retargeting/latest/tuning/" target="_blank" rel="noopener"></a></header>
 <div class="rt-runtime"><div class="rt-section-kicker" id="rt-runtime-title"></div><p id="rt-runtime-state" role="status"></p><p id="rt-runtime-selection" role="status"></p><p id="rt-runtime-reason"></p><div class="wb-actions"><button id="rt-install"></button><button id="rt-apply" class="primary"></button><button id="rt-sdk"></button></div></div>
 <details class="rt-binding-card rt-official-binding"><summary class="rt-section-kicker" id="rt-binding-title"></summary><p id="rt-binding"></p></details><p id="rt-scope"></p><h4 id="rt-scale-title"></h4><p id="rt-scale-note"></p><div id="rt-scale-grid"></div>
 <details class="rt-advanced"><summary id="rt-advanced-label"></summary><h4 id="rt-pinch-title"></h4><div id="rt-pinch-grid"></div><div class="rt-fields"><label><span id="rt-alpha-label"></span><input id="rt-alpha" type="number" min="0" max="1" step="0.01"></label><label><span id="rt-delta-label"></span><input id="rt-delta" type="number" min="0" step="0.01"></label></div></details>
 <div class="wb-actions rt-draft-actions"><button id="rt-save" class="primary"></button><button id="rt-export"></button><button id="rt-reload"></button><button id="rt-default"></button></div>
 <p id="rt-status" role="status" aria-live="polite"></p><details id="rt-export-result" hidden><summary id="rt-export-label"></summary><p id="rt-export-help"></p><textarea id="rt-yaml" readonly rows="12" spellcheck="false" aria-label="YAML"></textarea></details>`;
 host.append(panel);output.id='rt-output';output.setAttribute('role','tabpanel');panel.setAttribute('role','tabpanel');
 const officialLayout=document.createElement('div');officialLayout.className='rt-official-layout';
 const parameters=document.createElement('div');parameters.className='rt-parameter-editor';
 const activation=document.createElement('aside');activation.className='rt-activation';
 panel.querySelector('.rt-binding-card').after(officialLayout);officialLayout.append(parameters,activation);
 const quick=document.createElement('p');quick.id='rt-scale-quick';parameters.append($('rt-scale-title'),quick,$('rt-scale-grid'),panel.querySelector('.rt-advanced'));
 const saveArea=document.createElement('section');saveArea.className='rt-save-area';saveArea.innerHTML='<div class="rt-save-heading"><h4 id="rt-save-title"></h4><span id="rt-draft-state" class="rt-state-pill"></span></div><p id="rt-save-note"></p>';
 saveArea.append(panel.querySelector('.rt-draft-actions'),$('rt-status'),$('rt-export-result'));parameters.append(saveArea);
 activation.append(panel.querySelector('.rt-runtime'));
 const applyNote=document.createElement('p');applyNote.id='rt-apply-note';applyNote.className='rt-apply-note';activation.append(applyNote);

 const help=document.createElement('details');help.className='rt-help';const helpLabel=document.createElement('summary');helpLabel.id='rt-help-label';help.append(helpLabel,$('rt-scope'),$('rt-scale-note'));parameters.append(help);
 output.setAttribute('aria-labelledby',a.id);panel.setAttribute('aria-labelledby',b.id);a.setAttribute('aria-controls',output.id);b.setAttribute('aria-controls',panel.id);
 function select(official){output.hidden=official;panel.hidden=!official;a.setAttribute('aria-selected',String(!official));b.setAttribute('aria-selected',String(official));a.tabIndex=official?-1:0;b.tabIndex=official?0:-1;if(official&&!context)load()}
 a.onclick=()=>select(false);b.onclick=()=>select(true);
 switches.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();select(e.key==='Home'?false:e.key==='End'?true:panel.hidden);(panel.hidden?a:b).focus()}};
 async function request(name,values={}){
  if(!state?.csrf)throw Error(t('正在连接工作台服务','Connecting to workbench service'));
  const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name,...values})});
  const data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||'Request failed');return data;
 }
 function controls(){runtime();for(const id of ['rt-reload','rt-default','rt-save','rt-export'])$(id).disabled=busy||(!state?.csrf&&id!=='rt-default')||(id!=='rt-reload'&&!context)||(stale&&id!=='rt-reload');grid?.setDisabled(busy||stale);pinch?.setDisabled(busy||stale);$('rt-alpha').disabled=$('rt-delta').disabled=busy||stale;}
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
 function binding(){if(context){const x=context.binding;$('rt-binding').textContent=`${x.generation} · ${x.side} · ${t('手套','Glove')} ${x.glove_serial||'—'} · ${t('灵巧手','Hand')} ${x.hand_serial||'—'} · ${t('用户','User')} ${x.sdk_user||'—'} · v${context.revision}`}}
 async function load(){if(busy)return;busy=true;controls();try{context=(await request('retarget_tuning_context')).tuning;fill(context.values);stale=dirty=false;binding();$('rt-status').textContent=t('参数已载入。保存与应用分别操作。','Parameters loaded. Save and apply are separate actions.');$('rt-export-result').hidden=true;}catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 async function save(exporting){if(busy||!context||stale)return;busy=true;controls();try{
  const v=values(),result=await request(exporting?'retarget_tuning_export':'retarget_tuning_save',{binding:context.binding,values:v,revision:context.revision});
  if(exporting){$('rt-yaml').value=result.tuning_export.yaml;$('rt-export-result').hidden=false;$('rt-export-result').open=true;$('rt-status').textContent=t('已生成 YAML；尚未应用到当前手套或灵巧手。','YAML generated; not applied to the current glove or hand.');}
  else{context=result.tuning;dirty=false;binding();$('rt-status').textContent=t('参数已保存，可在手套预览中试用。','Saved. Try the parameters in glove preview.');}
 }catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 $('rt-reload').onclick=()=>{if(!dirty||confirm(t('放弃未保存修改并重新载入？','Discard unsaved edits and reload?')))load()};
 $('rt-default').onclick=()=>{if(context){fill(context.defaults);changed()}};$('rt-save').onclick=()=>save(false);$('rt-export').onclick=()=>save(true);
 for(const id of ['rt-alpha','rt-delta'])$(id).oninput=changed;
 for(const id of ['rt-scale-grid','rt-pinch-grid'])$(id).addEventListener('input',changed);

 function runtime(){
  const online=!!state?.csrf,r=state?.solver_runtime||{},g=state?.glove||{},live=g.stream?.solver;
  const attached=!!g.feedback?.device_id||!!g.hardware?.active||state?.connection==='connected';
  $('rt-install').textContent=t('准备求解环境','Prepare solver');
  $('rt-apply').textContent=t('预览开源映射','Preview open mapper');$('rt-sdk').textContent=t('切换为 SDK 映射','Switch to SDK mapper');
  $('rt-install').disabled=!online||!!r.busy||!!g.busy||attached||r.ready===true;
  $('rt-apply').disabled=!online||busy||stale||!context||!r.ready||attached;
  $('rt-sdk').disabled=!online||busy||stale||!context||attached;
  $('rt-runtime-state').textContent=!online?(serviceOffline?t('工作台服务离线','Workbench service offline'):t('等待工作台服务','Waiting for workbench service')):r.busy?t('正在准备独立求解环境…','Preparing isolated solver…'):r.ready?t('求解环境已就绪','Solver ready'):r.error||t('首次使用需要联网下载依赖。','First use requires an online dependency download.');
  let current='';
  if(!online)current=t('当前生效状态未知；本地修改仍然保留。','Applied state unknown. Local edits are preserved.');
  else if(live?.applied&&g.stream?.fresh){current=(live.engine==='official_open'?t('已确认：官方开源映射','Confirmed: official open mapper'):t('已确认：SDK 映射','Confirmed: SDK mapper'));
    if(live.engine==='official_open'){current+=' · '+(live.values_sha256===context?.values_sha256&&!dirty?t('当前参数已生效','Current parameters applied'):t('当前帧仍使用其他参数','Current frame uses other parameters'));if(Number.isFinite(live.solve_ms))current+=' · '+live.solve_ms.toFixed(1)+' ms';}}
  else if(context)current=t('下次接入使用官方 SDK','New connections use the official SDK');
  else current=t('载入当前配对后显示配置','Load the current pairing to inspect its configuration');
  $('rt-runtime-selection').textContent=current;
  $('rt-runtime-reason').textContent=attached?t('切换求解器前，请先断开灵巧手反馈。','Disconnect hand feedback before switching the solver.'):'';
  $('rt-runtime-reason').hidden=!attached;
  const draft=$('rt-draft-state');draft.textContent=stale?t('配对已改变','Pairing changed'):dirty?t('未保存','Unsaved'):context?t('已保存 · v','Saved · v')+context.revision:t('尚未载入','Not loaded');draft.dataset.state=stale?'stale':dirty?'dirty':'saved';

 }
 $('rt-install').onclick=async()=>{try{await request('solver_install');}catch(e){$('rt-status').textContent=e.message}};
 async function applyEngine(engine){if(busy||!context||stale)return;busy=true;controls();try{
  const result=await request('retarget_engine_select',{binding:context.binding,revision:context.revision,engine,values:engine==='official_open'?values():context.values});
  context=result.tuning;dirty=false;binding();$('rt-status').textContent=t('配置已保存；等待手套预览的新帧确认后才算生效。','Configuration saved; application is confirmed only by a fresh glove-preview frame.');
 }catch(e){$('rt-status').textContent=e.message}finally{busy=false;controls()}}
 $('rt-apply').onclick=()=>applyEngine('official_open');$('rt-sdk').onclick=()=>applyEngine('sdk');

 function labels(){
  a.textContent=t('输出微调','Output adjustment');b.textContent=t('官方求解器','Official solver');switches.setAttribute('aria-label',t('映射调整方式','Mapping adjustment method'));
  const texts={title:['官方映射参数表','Official retargeting parameters'],mode:['配置草稿 · 不影响当前连接','Configuration draft · does not affect this connection'],doc:['参数说明 ↗','Field reference ↗'],scope:['使用随软件固定的官方开源算法。二代使用与画面一致的 Beta 2 模型；映射在内置 Linux 独立环境运行。参数更改只在手套预览中应用，实机跟随仍由你单独启动。','Uses the pinned official open-source solver in an isolated built-in Linux environment. Hand 2 uses the same Beta 2 model as the viewer. Apply during glove-only preview; hand follow is started separately.'],'scale-title':['五指比例','Finger target scales'],'scale-note':['每格缩放“手腕到该关键点”的向量，不是电机力度或逐节骨长。1 表示保持原比例；支持从表格粘贴多行数值。','Each cell scales a wrist-to-landmark vector, not motor force or individual bone length. 1 preserves the scale. Paste rows from a spreadsheet.'],'advanced-label':['对指距离与平滑','Pinch distances and smoothing'],'pinch-title':['拇指配对阈值 · d1 < d2','Thumb pairing thresholds · d1 < d2'],'alpha-label':['输出低通 lp_alpha · 越小越平滑、延迟越大','Output low-pass lp_alpha · smaller adds smoothing and lag'],'delta-label':['逐帧变化权重 norm_delta','Frame-change regularization norm_delta'],reload:['重新载入','Reload'],default:['填入官方默认值','Fill official defaults'],save:['保存草稿','Save draft'],export:['导出 YAML','Export YAML'],'export-label':['导出配置内容','Export configuration'],'export-help':['复制以下完整 YAML，在官方仓库 example/config 下保存为对应配置文件，再用 tuning_tool.py 加载。模型路径沿用官方模板；软件内运行与导出是两个独立入口。','Copy the full YAML into the matching config file under the official repository’s example/config and load it with tuning_tool.py. Model paths follow the official template. In-app execution and export are separate actions.']};
  texts.scope=['开源参数仅用于调参预览；实机遥操作使用官方稳定版 SDK。','Open parameters are preview-only. Real teleoperation uses the official stable SDK.'];
  for(const [id,pair] of Object.entries(texts))$('rt-'+id).textContent=t(...pair);
  $('rt-help-label').textContent=t('参数含义与使用方法','Parameter meanings and usage');
  $('rt-mode').textContent=t('先调整比例，再保存；需要预览时单独应用。','Adjust scales, then save. Apply separately when you want a preview.');
  const extra={'scale-quick':['1 保持比例；每格调整手腕到对应关键点的距离。','1 preserves scale. Each cell adjusts the wrist-to-landmark distance.'],'binding-title':['当前设备配对','Current pairing'],'runtime-title':['预览中的求解器','Preview solver'],'save-title':['保存配置','Save configuration'],'save-note':['保存只更新这组配对的草稿，不切换当前预览。','Saving updates this pairing’s configuration without switching the current preview.'],'apply-note':['应用会保存本表参数并选择求解器；以控制端新帧确认为准。真实手跟随仍需单独开启。','Apply saves this table and selects the solver. Wait for a fresh controller frame to confirm. Real-hand follow is started separately.']};
  extra['apply-note']=['应用仅切换调参预览。实机跟随请使用 SDK 映射。','Apply changes the tuning preview only. Use the SDK mapper for real-hand follow.'];
  for(const [id,pair] of Object.entries(extra))$('rt-'+id).textContent=t(...pair);runtime();outputLabels();
  if(grid){grid.setLabels(rows(),cols());pinch.setLabels(rows().slice(1),pinCols());grid.setLanguage(window.WujiLocale?.lang||'zh');pinch.setLanguage(window.WujiLocale?.lang||'zh')}binding();
 }

 // Only regroup existing controls. Their handlers and control requests remain unchanged.
 let outputLayout=null;
 function outputLabels(){
  if(!outputLayout)return;
  const texts={intro:['从关节输出开始微调，右侧模型帮助定位每一格。','Fine-tune joint output; use the model to locate each cell.'],pair:['当前设备配对','Current pairing'],change:['更换配对','Change pairing'],save:['保存与预览','Save and preview'],note:['先保存这组配对的设置，再按需应用到手套预览。','Save settings for this pairing, then apply to glove preview when needed.'],live:['控制端确认','Controller confirmation'],values:['关节输出','Joint output'],presets:['预设与说明','Presets and reference']};
  for(const [id,pair] of Object.entries(texts)){const el=$('rt-output-'+id);if(el)el.textContent=t(...pair)}
  if(serviceOffline)outputOffline();
 }
 function arrangeOutput(){
  const editor=output.querySelector('.cr-mapping-editor');if(!editor||outputLayout)return;outputLayout=editor;output.classList.add('rt-output-flow');
  const header=document.createElement('header');header.className='rt-output-heading';header.append($('wb-retarget-title'));
  const intro=document.createElement('p');intro.id='rt-output-intro';header.append(intro);output.prepend(header);
  const bindingCard=document.createElement('details');bindingCard.className='rt-binding-card rt-output-binding';
  const pair=document.createElement('summary');pair.className='rt-section-kicker';pair.id='rt-output-pair';
  const change=document.createElement('button');change.type='button';change.id='rt-output-change';change.className='rt-pair-change';change.onclick=()=>window.WujiConnectionHub?.show('device');
  bindingCard.append(pair,$('wb-mapping-binding'),change);header.append(bindingCard);
  const fields=document.createElement('section');fields.className='rt-output-fields';
  const valuesTitle=document.createElement('h4');valuesTitle.id='rt-output-values';fields.append(valuesTitle);
  const toolbar=editor.querySelector('.cr-editor-toolbar'),hint=editor.querySelector('.cr-grid-help');if(toolbar)fields.append(toolbar);if(hint)fields.append(hint);fields.append($('wb-retarget-grid'));editor.prepend(fields);
  const actions=$('wb-retarget-save').parentElement,live=[...editor.querySelectorAll(':scope > p.wb-note')].find(el=>el.id!=='wb-mapping-binding');
  const save=document.createElement('section');save.className='rt-save-area rt-output-save';save.innerHTML='<h4 id="rt-output-save"></h4><p id="rt-output-note"></p>';
  const apply=actions.querySelector('button:not([id])');if(apply)apply.id='rt-output-apply';
  save.append(actions,$('wb-retarget-status'));if(live){const confirmation=document.createElement('div');confirmation.className='rt-controller-confirmation';confirmation.innerHTML='<span class="rt-section-kicker" id="rt-output-live"></span>';confirmation.append(live);save.append(confirmation)}fields.after(save);
  const presets=editor.querySelector(':scope > .cr-details');if(presets){const summary=presets.querySelector('summary');if(summary)summary.id='rt-output-presets';const note=editor.querySelector(':scope > .ui-context-note');if(note)presets.append(note);editor.append(presets)}
  const solverNote=panel.querySelector(':scope > .ui-context-note');if(solverNote)help.append(solverNote);
  const quickNote=$('rt-output-note');quickNote.setAttribute('aria-live','off');
  outputLabels();
 }
 if(document.readyState==='complete')arrangeOutput();else document.addEventListener('DOMContentLoaded',arrangeOutput,{once:true});
 window.addEventListener('console-state',e=>{state=e.detail;serviceOffline=false;controls();if(!started&&state?.csrf){started=true;load();if(!state.glove?.busy)request('solver_probe').catch(()=>{})}if(context&&state?.device_profile&&(state.device_profile.generation!==context.binding.generation||state.device_profile.side!==context.binding.side)){stale=true;controls();$('rt-status').textContent=t('手的型号已改变，请重新载入参数表。','Hand model changed; reload the parameter table.');}});
 window.addEventListener('retarget-binding-changed',()=>{if(!context)return;stale=true;controls();$('rt-status').textContent=t('设备配对已改变。重新载入后再编辑。','Device pairing changed. Reload before editing.');});
 function outputOffline(){const button=$('rt-output-apply');if(button)button.disabled=true;const live=output.querySelector('.rt-controller-confirmation p');if(live)live.textContent=t('工作台服务离线，无法确认当前映射。','Workbench service offline. Applied mapping is unknown.');}
 window.addEventListener('console-offline',()=>{state=null;serviceOffline=true;controls();outputOffline();});
 window.addEventListener('wuji-language',labels);labels();select(false);controls();
})();
