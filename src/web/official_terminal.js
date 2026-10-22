/* Existing xterm renders the official CLI byte stream; no pose/ANSI reimplementation. */
(()=>{'use strict';
 function mount(host,{language=()=>window.WujiLocale?.lang||'zh'}={}){
  host.className='official-terminal';host.hidden=true;
  host.innerHTML='<div class="official-terminal-heading"><strong></strong><span class="official-terminal-note"></span></div><div class="official-terminal-actions"><button type="button" data-key="up">↑</button><button type="button" data-key="down">↓</button><button type="button" data-key="enter"></button><button type="button" data-key="focus"></button></div><div class="official-terminal-scroll"><div class="official-terminal-screen"></div></div><p role="status" class="official-terminal-error"></p>';
  const screen=host.querySelector('.official-terminal-screen'),note=host.querySelector('.official-terminal-note'),error=host.querySelector('.official-terminal-error');
  let term=null,fit=null,run={},fresh=false,cursor=0,session='',polling=false,writing=false,queue=[],lastSize='',fault='',transportFault='',gapWarning='',generation=0;
  const t=(zh,en)=>language()==='en'?en:zh;
  function message(){error.textContent=[fault,transportFault,gapWarning].filter(Boolean).join(' · ');host.querySelector('strong').textContent=t('CLI 原版视觉指导','Original CLI guidance');note.textContent=t('↑ ↓ 选择 · Enter 确认','↑ ↓ to select · Enter to confirm');host.querySelector('[data-key=enter]').textContent=t('确认','Confirm');host.querySelector('[data-key=focus]').textContent=t('使用键盘','Use keyboard');for(const button of host.querySelectorAll('button'))button.disabled=!fresh||run.running!==true||run.status==='cancelling';}
  async function action(payload,id){
   const sr=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!sr.ok)throw Error(t('工作台状态不可用','Workbench status is unavailable'));
   const state=await sr.json();if(!state.csrf)throw Error(t('请刷新工作台状态','Refresh workbench status'));
   const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name:'calibration_terminal_input',run_id:id,...payload}),signal:AbortSignal.timeout(6000)}),body=await response.json();
   if(!response.ok||body.ok===false)throw Error(body.error||t('终端输入未完成','Terminal input was not accepted'));
  }
  async function flush(){
   if(writing||!queue.length||!fresh||run.running!==true)return;
   writing=true;const id=session;
   try{while(queue.length&&session===id&&fresh&&run.running===true){const item=queue.shift();await action(item.payload,item.id);}}
   catch(e){if(session===id){queue=[];fault=e.message;message();}}finally{writing=false;if(queue.length)queueMicrotask(flush);}
  }
  function send(payload){if(!session||!fresh||run.running!==true||run.status==='cancelling')return;if(queue.length>=32){fault=t('输入较快，请稍等当前输入完成。','Please wait for the pending terminal input.');message();return;}queue.push({payload,id:session});flush();}
  function resize(){
   if(!term||host.hidden||!screen.getClientRects().length)return;
   const dimensions=fit.proposeDimensions();if(!dimensions)return;
   const cols=Math.max(81,Math.min(240,dimensions.cols)),rows=38,size=cols+'x'+rows;
   if(lastSize===size)return;lastSize=size;term.resize(cols,rows);send({type:'resize',cols,rows});
  }
  function initialize(){
   if(term)return;
   if(!window.Terminal||!window.FitAddon?.FitAddon)throw Error(t('原版终端组件未载入，请更新软件。','Terminal components are unavailable. Update the workbench.'));
   const nonce=document.querySelector('meta[name=workbench-style-nonce]')?.content;
   // xterm's public documentOverride authorizes only its generated style
   // nodes. Scripts keep the existing same-origin policy without inline eval.
   const terminalDocument=nonce?new Proxy(document,{get(target,key){if(key==='createElement')return(...args)=>{const node=target.createElement(...args);if(String(args[0]).toLowerCase()==='style')node.nonce=nonce;return node;};const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;}}):document;
   term=new Terminal({documentOverride:terminalDocument,cols:100,rows:38,fontSize:13,lineHeight:1.24,fontFamily:'Consolas, "Cascadia Mono", monospace',cursorBlink:true,scrollback:1500,convertEol:false,allowProposedApi:false,theme:{background:'#121820',foreground:'#e9edf2',cursor:'#b7d8ff',selectionBackground:'#355478'}});
   fit=new FitAddon.FitAddon();term.loadAddon(fit);term.open(screen);
   term.onData(text=>{for(let at=0;at<text.length;at+=4096)send({type:'write',text:text.slice(at,at+4096)});});
   new ResizeObserver(resize).observe(screen);
  }
  async function poll(){
   if(polling||host.hidden||document.hidden||!session||!host.getClientRects().length)return;
   polling=true;const id=session,version=generation;
   try{
    const response=await fetch('/api/calibration-terminal?run='+encodeURIComponent(id)+'&after='+cursor,{cache:'no-store',signal:AbortSignal.timeout(5000)}),data=await response.json();
    if(!response.ok)throw Error(data.error||t('无法读取官方终端','Official terminal is unavailable'));
    if(session!==id||generation!==version)return;
    if(data.id!==id||!Array.isArray(data.chunks)||data.chunks.length>128)throw Error(t('终端会话已变化，请刷新。','Terminal session changed. Refresh its status.'));
    transportFault='';
    if(data.gap){term.reset();gapWarning=t('较早的输出已截断，以下为保留的原版输出。','Earlier output was truncated. Retained original output follows.');}
    for(const chunk of data.chunks){if(!Number.isInteger(chunk.sequence)||chunk.sequence<=cursor||typeof chunk.text!=='string')continue;term.write(chunk.text);cursor=chunk.sequence;}
    message();
   }catch(e){if(session===id){transportFault=e.message;message();}}finally{polling=false;}
  }
  host.addEventListener('click',event=>{const key=event.target.closest('button')?.dataset.key;if(key==='focus')term?.focus();else if(key)send({type:'write',text:{up:'\x1b[A',down:'\x1b[B',enter:'\r'}[key]});});
  setInterval(poll,300);
  return {render(next,options={}){
   run=next||{};fresh=options.fresh===true;host.hidden=run.source!=='official_tty'||!run.id;if(host.hidden)return;
   try{initialize();if(session!==run.id){generation++;session=run.id;cursor=0;queue=[];lastSize='';fault='';transportFault='';gapWarning='';term.reset();}term.options.disableStdin=!fresh||run.running!==true||run.status==='cancelling';message();resize();poll();}
   catch(e){fault=e.message;message();}
  }};
 }
 window.OfficialTerminal={mount};
})();
