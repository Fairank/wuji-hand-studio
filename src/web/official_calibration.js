/* Display only the official CLI's current event. No pose catalogue, clock or actions. */
(()=>{
 'use strict';
 const names={pinch_index:['拇指与食指接触','Thumb–index pinch'],pinch_middle:['拇指与中指接触','Thumb–middle pinch'],pinch_ring:['拇指与无名指接触','Thumb–ring pinch'],pinch_pinky:['拇指与小指接触','Thumb–little pinch'],four_finger_bend_90:['四指弯曲 90°','Four fingers bent 90°'],flat_open:['四指伸平','Four fingers flat']};
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
  host.innerHTML='<div class="oc-heading"><h4 id="oc-title"></h4><span id="oc-state"></span></div><p id="hub-calib-status" role="status" aria-live="polite"></p><dl class="oc-step"><dt id="oc-step-label"></dt><dd id="hub-calib-step"></dd></dl><div class="oc-progress-row"><span id="oc-progress-label"></span><progress id="oc-progress" max="1" aria-labelledby="oc-progress-label"></progress><span id="oc-progress-text"></span></div><details class="oc-details"><summary id="oc-details-label"></summary><pre id="oc-details"></pre></details>';
  const $=id=>host.querySelector('#'+id),details=host.querySelector('details');let data={},context={};
  const write=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value;};
  function raw(){
   if(!details.open)return;
   const run=data.run||{};let output;
   try{output=JSON.stringify({status_verified:context.fresh===true,status:run.status,running:run.running,step:run.step,step_total:run.step_total,step_name:run.step_name,progress:run.progress,exit_code:run.exit_code,event:run.event,result:run.result,error:run.error},null,2);}
   catch{output=language()==='en'?'Event details unavailable':'事件详情不可用';}
   write('oc-details',output.length>16000?output.slice(0,16000)+'\n…':output);
  }
  details.addEventListener('toggle',raw);
  function render(next,options={}){
   data=next||{};context=options;const run=data.run||{},en=language()==='en',t=(zh,eng)=>en?eng:zh,fresh=context.fresh===true;
   const done=completed(run),error=text(context.error)||text(run.error),status=typeof run.status==='string'?run.status:'idle';
   let label=t('尚未开始','Not started'),message=context.notice||t('选择用户和手套，再开始标定。','Select a user and glove, then start calibration.'),tone='idle';
   if(!fresh){label=t('状态未核对','Status unverified');message=error||t('无法读取当前状态。上次事件仍保留，刷新后再核对。','Current status is unavailable. Previous events remain; refresh to verify.');tone='unknown';}
   else if(context.error){label=t('操作未完成','Operation incomplete');message=error;tone='error';}
   else if(error||status==='error'||Number.isInteger(run.exit_code)&&run.exit_code!==0&&status!=='cancelled'){label=t('标定失败','Calibration failed');message=error||t('CLI 未正常结束，请查看详细记录。','The CLI did not finish normally. Review the detailed log.');tone='error';}
   else if(done){label=t('标定完成','Calibration completed');message=t('官方 result 已返回，CLI 退出代码为 0。','The official result was received and the CLI exited with code 0.');tone='completed';}
   else if(status==='completed'||status==='unconfirmed'){label=t('结果未确认','Result unconfirmed');message=t('等待官方 result 与正常退出结果，尚不能确认完成。','Completion requires the official result and a normal CLI exit.');tone='unknown';}
   else if(status==='cancelled'){label=t('已取消','Cancelled');message=t('标定已取消。','Calibration was cancelled.');}
   else if(run.running===true){
    const labels={collecting:['采集中','Collecting'],solving:['求解中','Solving'],cancelling:['正在取消','Cancelling']};
    label=labels[status]?t(...labels[status]):t('运行中','Running');message=t('按 CLI 当前步骤操作。','Follow the current CLI step.');tone='running';
   }else if(status!=='idle'&&status!==''){label=t('状态未知','Status unknown');message=t('当前状态未识别，请查看详细记录。','The current status is unrecognized. Review the detailed log.');tone='unknown';}
   else if(data.available!==true){label=t('CLI 未就绪','CLI unavailable');message=error||t('刷新状态后重试。','Refresh status and retry.');tone='unknown';}
   write('oc-title',t('运行状态','Run status'));write('oc-state',label);host.dataset.status=tone;write('hub-calib-status',message);
   const hasRun=run.running===true||status!=='idle'&&status!=='';
   write('oc-step-label',fresh?t('当前步骤','Current step'):t('上次步骤（未核对）','Previous step (unverified)'));
   write('hub-calib-step',hasRun?(step(run,en?'en':'zh')||t('未知，等待 CLI 事件','Unknown; waiting for the CLI event')):t('尚未开始','Not started'));
   const value=hasRun?progress(run.progress):null;
   write('oc-progress-label',fresh?t('采集进度','Capture progress'):t('上次进度（未核对）','Previous progress (unverified)'));
   $('oc-progress').hidden=value===null;if(value===null)$('oc-progress').removeAttribute('value');else $('oc-progress').value=value;
   write('oc-progress-text',value===null?t('未知','Unknown'):Math.round(value*100)+'%');
   write('oc-details-label',t('详细记录','Detailed log'));raw();
  }
  return {render};
 }
 window.OfficialCalibration={mount,helpers:{completed,step,progress}};
})();
