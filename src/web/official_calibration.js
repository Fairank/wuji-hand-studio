/* Display only the official CLI's current event. No pose catalogue, clock or actions. */
(()=>{
 'use strict';
 const names={pinch_index:['拇指与食指指尖接触','Thumb–index fingertip contact'],pinch_middle:['拇指与中指指尖接触','Thumb–middle fingertip contact'],pinch_ring:['拇指与无名指指尖接触','Thumb–ring fingertip contact'],pinch_pinky:['拇指与小指指尖接触','Thumb–little fingertip contact'],four_finger_bend_90:['四指弯曲约 90°','Four fingers bent about 90°'],flat_open:['四指伸平','Four fingers flat']};
 const text=(value,max=400)=>typeof value==='string'?value.slice(0,max):'';
 const officialResult=value=>!!value&&typeof value==='object'&&value.schema_version===2&&value.calibration==='hand_model'&&(value.event==='result'||value.type==='result');
 function completed(run){return run.running===false&&run.status==='completed'&&run.exit_code===0&&officialResult(run.event);}
 function step(run,language){
  const name=text(run.step_name,240).trim();
  if(name)return names[name]?.[language==='en'?1:0]||name;
  return run.step_total===6&&Number.isInteger(run.step)&&run.step>=0&&run.step<6?(language==='en'?'Step ':'步骤 ')+(run.step+1)+' / 6':null;
 }
 function progress(value){return typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=1?value:null;}
 function mount(host,{language=()=>window.WujiLocale?.lang||'zh'}={}){
  host.classList.add('oc-run');host.setAttribute('aria-labelledby','oc-title');
  host.innerHTML='<div class="oc-heading"><h4 id="oc-title"></h4><span id="oc-state"></span></div><div class="oc-instruction"><dl class="oc-step"><dt id="oc-step-label"></dt><dd id="hub-calib-step"></dd><span id="oc-step-count"></span></dl><p id="hub-calib-status" role="status" aria-live="polite"></p><div class="oc-progress-row"><span id="oc-progress-label"></span><progress id="oc-progress" max="1" aria-labelledby="oc-progress-label"></progress><span id="oc-progress-text"></span></div><dl class="oc-feedback"><div><dt id="oc-stability-label"></dt><dd id="oc-stability"></dd></div><div><dt id="oc-constraints-label"></dt><dd id="oc-constraints"></dd></div><div id="oc-hold-row"><dt id="oc-hold-label"></dt><dd id="oc-hold"></dd></div></dl></div><div class="oc-result" hidden><strong id="oc-result-label"></strong><p id="oc-result-user"></p></div><details class="oc-details"><summary id="oc-details-label"></summary><div id="oc-result-meta" hidden><p id="oc-result-location"></p><p id="oc-result-note"></p></div><dl class="oc-more-feedback"><div id="oc-frames-row"><dt id="oc-frames-label"></dt><dd id="oc-frames"></dd></div></dl><pre id="oc-details"></pre></details>';
  const empty=document.createElement('div');empty.id='oc-empty';empty.innerHTML='<strong id="oc-empty-title"></strong><p id="oc-empty-note"></p>';host.prepend(empty);
  const reference=document.createElement('figure');reference.id='oc-guide';reference.hidden=true;reference.innerHTML='<img id="oc-guide-image" hidden><figcaption><span id="oc-guide-caption"></span> <a id="oc-guide-link" target="_blank" rel="noopener"></a></figcaption><p id="oc-guide-unavailable"></p>';host.querySelector('.oc-instruction').before(reference);
  const diagnostics=document.createElement('ul');diagnostics.id='oc-diagnostics';diagnostics.hidden=true;host.querySelector('.oc-feedback').after(diagnostics);
  const terminalHost=document.createElement('section');host.querySelector('.oc-result').before(terminalHost);
  const terminal=window.OfficialTerminal?.mount(terminalHost,{language});
  const $=id=>host.querySelector('#'+id),details=host.querySelector('details');let data={},context={},imageKey='',hintsKey='',visualKey='';
  $('oc-guide-image').onload=()=>{$('oc-guide-image').hidden=false;$('oc-guide-unavailable').hidden=true;};$('oc-guide-image').onerror=()=>{$('oc-guide-image').hidden=true;$('oc-guide-unavailable').hidden=false;};
  const write=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value;};
  function raw(){
   if(!details.open)return;
   const run=data.run||{};let output;
   try{output=JSON.stringify({status_verified:context.fresh===true,status:run.status,running:run.running,step:run.step,step_total:run.step_total,step_name:run.step_name,progress:run.progress,phase:run.phase,checks:run.checks,timing:run.timing,user:run.user,side:run.side,serial:run.serial,exit_code:run.exit_code,event:run.event,result:run.result,error:run.error},null,2);}
   catch{output=language()==='en'?'Event details unavailable':'事件详情不可用';}
   write('oc-details',output.length>16000?output.slice(0,16000)+'\n…':output);
  }
  details.addEventListener('toggle',raw);
  function render(next,options={}){
   data=next||{};context=options;const run=data.run||{},en=language()==='en',t=(zh,eng)=>en?eng:zh,fresh=context.fresh===true;
   const native=run.source==='official_tty'||data.human_tty===true&&!data.guided_native,guided=data.guided_native===true&&!native;host.dataset.source=native?'official_tty':'official_jsonl';host.dataset.humanTty=String(native);host.dataset.guided=String(guided);host.dataset.previous=String(context.previous===true);terminal?.render(run,{fresh});
   const cleanup=data.cleanup_required===true,done=!cleanup&&!native&&completed(run),error=text(context.error)||text(run.error),status=typeof run.status==='string'?run.status:'idle';host.dataset.cleanupRequired=String(cleanup);
   let label=t('尚未开始','Not started'),message=context.notice||t('选择用户和手套，再开始标定。','Select a user and glove, then start calibration.'),tone='idle';
   if(cleanup){label=t('待结束核对','Session end unverified');message=[text(data.cleanup_error)||t('上次会话尚未完整退出，请结束并核对。','The previous session has not fully exited. End and verify it.'),error].filter((value,index,all)=>value&&all.indexOf(value)===index).join(' · ');tone='unknown';}
   else if(!fresh){label=t('状态未核对','Status unverified');message=error||t('无法读取当前状态。上次事件仍保留，刷新后再核对。','Current status is unavailable. Previous events remain; refresh to verify.');tone='unknown';}
   else if(context.error){label=t('操作未完成','Operation incomplete');message=error;tone='error';}
   else if(error||status==='error'||Number.isInteger(run.exit_code)&&run.exit_code!==0&&status!=='cancelled'){label=t('标定失败','Calibration failed');message=/EMF data stream closed|0x4102/.test(error)?t('手套采集数据中断，请检查连接后重试。','Glove capture was interrupted. Check the connection and retry.'):error||t('标定未完成，请查看详细记录。','Calibration did not finish. Review the detailed log.');tone='error';}
   else if(done){label=t('标定完成','Calibration completed');message=t('手型已保存，可以进入遥操作。','Hand model saved. You can continue to teleoperation.');tone='completed';}
   else if(status==='completed'||status==='unconfirmed'){label=t('结果未确认','Result unconfirmed');message=t('等待官方 result 与正常退出结果，尚不能确认完成。','Completion requires the official result and a normal CLI exit.');tone='unknown';}
   else if(native&&status==='session_ended'){label=t('原版会话已结束','Official session ended');message=t('CLI 已正常退出。标定结果请查看下方原版界面与刷新后的用户模型状态。','The CLI exited normally. Review the original output below and refresh the user model status for calibration results.');}
   else if(native&&status==='stopped'){label=t('会话已停止','Session stopped');message=t('本次终端会话已停止，不代表标定完成。','The terminal session has stopped. This does not confirm a completed calibration.');}
   else if(status==='cancelled'){label=t('已取消','Cancelled');message=t('标定已取消。','Calibration was cancelled.');}
   else if(run.running===true){
    const labels={starting:['正在核对身份','Verifying device identity'],terminal:['原版 CLI 运行中','Official CLI running'],collecting:['采集中','Collecting'],solving:['求解中','Solving'],cancelling:['正在取消','Cancelling']};
    label=labels[status]?t(...labels[status]):t('运行中','Running');message=status==='starting'?(data.human_tty===true?t('正在核对所选手套并打开官方流程，尚未开始采集。','Verifying the selected glove and opening the official journey. Capture has not started.'):t('正在核对所选手套、用户和左右手，尚未开始采集。','Verifying the selected glove, user and hand side. Capture has not started.')):status==='solving'?t('官方正在求解手型，请等待结果。','The official solver is fitting the hand model. Wait for its result.'):status==='cancelling'?t('正在结束本次采集，请等待官方进程退出。','Ending this capture. Wait for the official process to exit.'):t('跟随下方官方当前动作，采集条件由 CLI 判断。','Follow the official step below. Capture conditions are evaluated by the CLI.');tone='running';
   }else if(status!=='idle'&&status!==''){label=t('状态未知','Status unknown');message=t('当前状态未识别，请查看详细记录。','The current status is unrecognized. Review the detailed log.');tone='unknown';}
   else if(data.available!==true){label=t('CLI 未就绪','CLI unavailable');message=error||t('刷新状态后重试。','Refresh status and retry.');tone='unknown';}
   if(native&&fresh&&run.running&&status==='terminal')message=t('按原版界面完成用户确认与动作采集；动作图示和结果均由官方 CLI 显示。','Follow the original interface for profile confirmation and capture. Pose guidance and results are displayed by the official CLI.');
   if(guided&&fresh&&!error&&tone==='running'&&run.running===true&&run.status!=='starting'&&run.status!=='solving'&&run.status!=='cancelling'){
    const hints={waiting_movement:['先放松张开手掌，再做这个动作。','Relax and open your palm, then make this pose.'],waiting_stable:['保持这个动作，手掌尽量平稳。','Hold this pose and keep your hand steady.'],collecting:['保持不动，正在采集。','Keep holding still. Capturing this pose.'],done:['这个动作已采集，等待下一步。','Pose captured. Wait for the next step.']};if(hints[run.phase])message=t(...hints[run.phase]);
    if(run.phase==='done'&&run.step_total===6&&run.step===5)message=t('六个动作已采集，等待官方生成并保存手型。','All six poses captured. Wait for the official model and save result.');
   }
   const view=cleanup||!fresh?'unverified':tone==='error'?'error':done?'completed':native&&run.id?'terminal':tone==='unknown'?'unverified':run.running===true?status==='starting'?'preparing':status==='solving'?'solving':status==='cancelling'?'cancelling':'collecting':status==='cancelled'||status==='stopped'?'cancelled':'idle';
   host.dataset.view=view;host.dataset.phase=text(run.phase,80);host.dataset.runId=text(run.id,100);
   write('oc-title',context.previous?t('上次标定记录','Previous calibration record'):view==='collecting'?t('手型标定','Hand-model calibration'):label);write('oc-state',label);$('oc-state').hidden=guided;host.dataset.status=tone;write('hub-calib-status',view==='completed'?'':message);
   const hasRun=run.running===true||status!=='idle'&&status!=='';host.dataset.hasRun=String(hasRun);empty.hidden=!native||hasRun;
   write('oc-empty-title',t('原版手套标定','Original glove calibration'));write('oc-empty-note',context.notice||t('选择手套后，点击上方“开始标定”。','Select a glove, then choose Start calibration above.'));
   write('oc-step-label',fresh?t('当前步骤','Current step'):t('上次步骤（未核对）','Previous step (unverified)'));
   write('hub-calib-step',hasRun?(step(run,en?'en':'zh')||t('等待官方步骤','Waiting for the official step')):t('尚未开始','Not started'));
   const count=Number.isInteger(run.step)&&Number.isInteger(run.step_total)&&run.step_total>0&&run.step>=0&&run.step<run.step_total?(run.step+1)+' / '+run.step_total:'';write('oc-step-count',count);$('oc-step-count').hidden=!count;
   const value=hasRun?progress(run.progress):null;
   const ids=Object.keys(names),poseName=text(run.step_name),pose=poseName?ids.indexOf(poseName):run.step_total===6&&Number.isInteger(run.step)&&run.step>=0&&run.step<6?run.step:-1;
   reference.hidden=!guided||view!=='collecting'||!hasRun||pose<0;host.dataset.hasReference=String(!reference.hidden);write('oc-guide-caption',t('官方动作参考 · 非实测姿态','Official pose reference · Not a measured pose'));write('oc-guide-unavailable',t('参考图暂不可用，请按当前文字动作操作。','Reference image unavailable. Follow the current text instruction.'));$('oc-guide-link').href='https://docs.wuji.tech/docs/'+(en?'en':'zh')+'/wuji-cli/latest/hand-model-calibration/';write('oc-guide-link',t('官方说明 ↗','Official guide ↗'));
   const nextImageKey=reference.hidden?'':String(run.id||'')+'|'+pose;if(nextImageKey!==imageKey){imageKey=nextImageKey;$('oc-guide-image').hidden=true;$('oc-guide-unavailable').hidden=false;if(nextImageKey){$('oc-guide-image').alt=step(run,en?'en':'zh')||'';$('oc-guide-image').src='/api/calibration-guide?step='+pose;}else $('oc-guide-image').removeAttribute('src');}
   write('oc-progress-label',fresh?t('采集进度','Capture progress'):t('上次进度（未核对）','Previous progress (unverified)'));
   $('oc-progress').hidden=value===null;if(value===null)$('oc-progress').removeAttribute('value');else $('oc-progress').value=value;
   write('oc-progress-text',value===null?t('未知','Unknown'):Math.round(value*100)+'%');
   const checks=run.checks&&typeof run.checks==='object'?run.checks:{},timing=run.timing&&typeof run.timing==='object'?run.timing:{};
   const checkLabel=value=>value===true?t('已满足','Satisfied'):value===false?t('尚未满足','Not yet satisfied'):t('尚未报告','Not reported');
   write('oc-stability-label',t('动作稳定性','Pose stability'));write('oc-stability',checkLabel(checks.variance_ok));write('oc-constraints-label',t('动作约束','Pose constraints'));write('oc-constraints',checkLabel(checks.constraints_ok));
   $('oc-stability').dataset.state=checks.variance_ok===true?'ready':'waiting';$('oc-constraints').dataset.state=checks.constraints_ok===true?'ready':'waiting';
   const seconds=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null,held=seconds(timing.hold_elapsed),target=seconds(timing.hold_target),frames=seconds(timing.frames_collected);
   $('oc-hold-row').hidden=held===null;write('oc-hold-label',t('官方保持时间','Official hold time'));write('oc-hold',held===null?'—':held.toFixed(1)+(target===null?'':' / '+target.toFixed(1))+' s');
   $('oc-frames-row').hidden=frames===null;write('oc-frames-label',t('已采集帧','Captured frames'));write('oc-frames',frames===null?'—':String(frames));
   const groups=new Map();if(guided&&run.running===true&&Array.isArray(checks.metrics))for(const metric of checks.metrics){const hint=text(metric?.hint,180);if(!hint||typeof metric.error!=='number'||!Number.isFinite(metric.error)||Math.abs(metric.error)*(metric.unit==='m'?1000:1)<=.0001)continue;const group=[text(metric.finger,40),text(metric.finger_b,40)].filter(Boolean).join('-');if(!groups.has(group))groups.set(group,[]);groups.get(group).push(hint);}
   const hints=[...groups.keys()].sort().flatMap(key=>groups.get(key)).slice(0,8);if(guided&&run.running===true&&checks.constraints_ok===false&&!hints.length)hints.push(t('请微调当前动作，直到官方约束满足。','Adjust the current pose until the official constraints are satisfied.'));const nextHints=JSON.stringify(hints);if(hintsKey!==nextHints){hintsKey=nextHints;diagnostics.replaceChildren(...hints.map(hint=>{const item=document.createElement('li');item.textContent=hint;return item;}));}diagnostics.hidden=!hints.length;
   host.querySelector('.oc-feedback').hidden=!(hasRun&&view==='collecting');
   const result=officialResult(run.result)?run.result:officialResult(run.event)?run.event:null,payload=result?.result&&typeof result.result==='object'?result.result:{};
   host.querySelector('.oc-result').hidden=view!=='completed';$('oc-result-meta').hidden=view!=='completed';write('oc-result-label',t('手型已保存','Hand model saved'));
   write('oc-result-user',[text(run.user,80),run.side==='left'?t('左手','Left hand'):run.side==='right'?t('右手','Right hand'):'',text(run.serial,80)].filter(Boolean).join(' · '));
   const location=text(payload.calibrated_urdf||payload.path||payload.model_path||payload.output_path,400);write('oc-result-location',location);$('oc-result-location').hidden=!location;
   write('oc-result-note',t('已保存到所选佩戴者的档案。进入遥操作时会核对此次手型。','Saved to the selected wearer’s profile. Teleoperation verifies this model before opening.'));
   write('oc-details-label',t('详细记录','Detailed log'));raw();
   const nextVisualKey=[view,run.id||'',view==='collecting'?run.step_name||run.step:''].join('|');
   if(nextVisualKey!==visualKey){host.dataset.visualKey=nextVisualKey;const canAnimate=visualKey&&!native&&host.getClientRects().length&&document.documentElement.dataset.reduceMotion!=='true'&&!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches&&!window.matchMedia?.('(forced-colors: active)').matches;visualKey=nextVisualKey;if(canAnimate&&typeof host.animate==='function')host.animate([{opacity:.55,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:150,easing:'ease-out'});}
  }
  return {render};
 }
 window.OfficialCalibration={mount,helpers:{completed,step,progress}};
})();
