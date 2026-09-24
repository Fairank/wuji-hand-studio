/* Presentation and read-only anatomy. No connection, calibration or motion is started here. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const copy=[];
 function text(el,zh,en){copy.push([el,zh,en]);el.textContent=t(zh,en);return el}
 function node(tag,cls,parent){const el=document.createElement(tag);el.className=cls||'';if(parent)parent.append(el);return el}
 function fold(parent,zh,en,nodes){const d=node('details','cr-details',parent);text(node('summary','',d),zh,en);for(const n of nodes)if(n)d.append(n);return d}
 function link(parent,zh,en,key){const b=text(node('button','cr-link',parent),zh,en);b.type='button';b.onclick=()=>window.WujiConnectionHub.show(key);return b}
 const device=$('hub-device'),mapping=$('rt-output');if(!device||!mapping)return;
 const cards=node('div','cr-device-grid');device.querySelector('.hub-intro').after(cards);
 const hand=node('section','cr-device-card',cards);text(node('h3','',hand),'机械手','Robot hand');
 const connection=$('connect').closest('.panel'),profile=device.querySelector('.device-profile-control');
 hand.append(profile,connection);
 const glove=node('section','cr-device-card',cards);text(node('h3','',glove),'数据手套','Data glove');
 const gloveControls=document.querySelector('.glove-controls'),steps=gloveControls?.querySelectorAll('.glove-step');
 if(steps?.length===2){
  const children=[...gloveControls.children];for(const child of children){if(child===steps[1])break;glove.append(child)}
  steps[0].hidden=true;
  fold(glove,'配对说明与失联设置','Pairing help and timeout',[$('glove-pair-note'),$('glove-user-note'),$('glove-timeout-label'),$('glove-timeout').closest('.glove-input-unit')]);
  const header=node('div','cr-card-heading');glove.prepend(header);header.append(glove.querySelector('h3'),$('glove-scan'));
  const description=glove.querySelector('.cr-details');description.append($('glove-profile'));
  const settingLink=glove.querySelector('.button-row a');if(settingLink)description.append(settingLink);
  const pairing=node('div','cr-pair-fields');$('glove-user-label').before(pairing);
  for(const ids of [['glove-user-label','glove-user'],['glove-hand-device-label','glove-hand-device']]){const field=node('div','',pairing);ids.forEach(id=>field.append($(id)))}
 }
 const gloveStatus=node('p','cr-device-status',glove);gloveStatus.setAttribute('role','status');
 const next=node('div','cr-next',glove);link(next,'校准手套','Calibrate glove','calibration');link(next,'查看遥操作','View teleoperation','visual');
 const environment=fold(device,'控制环境与连接设置','Controller environment and settings',[device.querySelector('.runtime-card'),device.querySelector('.installation')]);
 environment.classList.add('cr-environment');
 const network=device.querySelector('.network-card');if(network)cards.after(network);
 const checks=node('div','cr-device-checks',device);
 device.querySelectorAll(':scope > .folded-panel').forEach(x=>checks.append(x));

 const calib=$('hub-calibration').querySelector('.hub-calibration');
 const calibSpace=node('div','cr-calibration-workspace',calib),calibAside=node('aside','cr-calibration-aside',calibSpace);
 const guide=$('hub-calibration-guide');calibSpace.prepend(guide);
 for(const child of [...calib.children])if(child!==calibSpace&&!['hub-calib-title','hub-calib-description'].includes(child.id))calibAside.append(child);
 const poseFeedback=guide.querySelector('.cg-guide');if(poseFeedback)fold(guide.querySelector('.cg-main'),'当前姿势与采集反馈','Selected pose and capture feedback',[poseFeedback]);

 // A fixed workspace for editing, with contextual joint help rather than another long form.
 const workspace=node('div','cr-mapping-workspace',mapping),editor=node('div','cr-mapping-editor',workspace),aside=node('aside','cr-joint-aside',workspace);
 for(const child of [...mapping.children])if(child!==workspace)editor.append(child);
 const help=fold(editor,'配置与预设','Configuration and presets',[$('wb-mapping-binding')]);
 const preset=editor.querySelector('input[maxlength="48"]')?.parentElement;if(preset)help.append(preset);
 const note=$('wb-retarget-note');if(note)help.append(note);
 const hint=text(node('p','cr-grid-help'), '选择格子，右侧查看位置。1 保持幅度，角度偏移以度为单位。','Select a cell to locate it. Gain 1 preserves amplitude; offsets are in degrees.');
 $('wb-retarget-grid').before(hint);
 const toolbar=node('div','cr-editor-toolbar'),smooth=$('wb-smoothing').parentElement,kind=$('wb-output-kind');
 smooth.before(toolbar);const adjustment=node('label','cr-adjustment',toolbar);text(node('span','',adjustment),'调整内容','Adjustment');adjustment.append(kind);toolbar.append(smooth);
 const inspectorHost=node('div','cr-inspector',aside);
 let chosen=4,info=null,state=null,requestKey='',serial=0,lastPaint='';
 const inspector=window.JointInspector?.mount(inspectorHost,{onSelect:index=>{chosen=index;paint();}});
 const fallback=text(node('p','cr-reference-error',aside),'正在载入关节参考…','Loading joint reference…');fallback.setAttribute('role','status');
 const source=fold(aside,'如何对应实物电机？','How does this match the motors?',[]);
 text(node('p','',source),'高亮取自原生模型。编号 0–19 是软件索引，不是设备节点号。只有当前设备已核对的显示映射，才显示确认的节点与反馈。','The highlight comes from the native model. Indices 0–19 are software positions, not node IDs. A confirmed node and feedback require a verified display mapping for this device.');
 const doc=node('a','',source);doc.href='https://docs.wuji.tech/docs/zh/wuji-hand/latest/control-guide/';doc.target='_blank';doc.rel='noopener';text(doc,'官方命名说明 ↗','Official joint naming ↗');
 const legend=text(node('p','cr-anatomy-legend',aside),'蓝色：所选关节部件 · 金色：原生转轴','Blue: selected joint body · Gold: native axis');
 let cells=[];
 function decorate(){
  cells=[...$('wb-retarget-grid').querySelectorAll('input[data-grid-row]')];
  for(const input of cells){
   const index=Number(input.dataset.gridRow)*4+Number(input.dataset.gridColumn),item=info?.items[index];if(!item)continue;
   let label=input.parentElement.querySelector('.cr-cell-label');if(!label)label=node('span','cr-cell-label',input.parentElement);
   label.textContent=item.jointLabel;input.title=`${item.fingerLabel} · ${item.jointLabel} · #${index}`;
  }
 }
 function paint(){
  if(!info||!inspector)return;
  const connected=state?.connection==='connected'&&!state?.stale;
  const verified=connected&&state?.mapping_verified&&state?.mapping_device_id===state?.device_id;
  const items=info.items.map(item=>{
   const entry=verified?state.mapping?.find(e=>e.index===item.index&&e.verified===true):null;
   const rate=entry&&state.joint_rates?.find(r=>r.nid===entry.nid);
   const row=entry&&state.latest?.joints?.find(r=>r.nid===entry.nid);
   const q=row&&rate?.status==='fresh'?entry.sign*row.position_rad+entry.offset:null;
   return {...item,nodeLabel:entry?'NID '+entry.nid:t('未核对','Unverified'),
    valueLabel:Number.isFinite(q)?(q*180/Math.PI).toFixed(1)+'°':t('无已核对反馈','No verified feedback')};
  });
  const open=state?.glove?.stream?.fresh&&state.glove.stream.solver?.engine==='official_open';
  const model={language:window.WujiLocale?.lang||'zh',profileLabel:info.profileLabel,selectedIndex:chosen,items,imageUrl:items[chosen].imageUrl,imageAlt:items[chosen].fingerLabel+' · '+items[chosen].jointLabel,
   sourceNote:open?t('开源映射按此模型顺序输出。图为位置参考，非实时姿态。','Open mapper uses this model order. Reference image, not live pose.'):
   t('模型位置参考；SDK 原生输出与模型轴序仍需设备核对。','Model reference; native SDK axis order still needs device verification.')};
  const key=JSON.stringify(model);if(key!==lastPaint){lastPaint=key;inspector.render(model)}
  for(const input of cells)input.parentElement.classList.toggle('cr-cell-selected',Number(input.dataset.gridRow)*4+Number(input.dataset.gridColumn)===chosen);
 }
 async function load(){
  const pid=state?.device_profile?.id||$('device-profile')?.value||'hand2_left',lang=window.WujiLocale?.lang||'zh',key=pid+lang;if(key===requestKey)return;
  requestKey=key;const token=++serial;info=null;lastPaint='';inspectorHost.hidden=true;fallback.hidden=false;fallback.textContent=t('正在载入关节参考…','Loading joint reference…');
  try{const res=await fetch('/api/joints?profile='+encodeURIComponent(pid)+'&language='+lang);if(!res.ok)throw Error('reference');const result=await res.json();if(token!==serial)return;info=result;inspectorHost.hidden=false;fallback.hidden=true;decorate();paint();}
  catch(e){if(token!==serial)return;fallback.textContent=t('关节参考暂不可用，请切换页面后重试','Joint reference unavailable; revisit this page to retry');}
 }
 $('wb-retarget-grid').addEventListener('focusin',e=>{const input=e.target.closest('input[data-grid-row]');if(!input)return;chosen=Number(input.dataset.gridRow)*4+Number(input.dataset.gridColumn);paint()});
 $('wb-output-kind').addEventListener('change',()=>{decorate();paint()});
 new MutationObserver(()=>{decorate();paint()}).observe($('wb-retarget-grid'),{childList:true});
 function labels(){copy.forEach(([el,zh,en])=>el.textContent=t(zh,en));load();decorate();paint();}
 window.addEventListener('console-state',e=>{state=e.detail;gloveStatus.textContent=t('手套：','Glove: ')+(state.glove?.connection==='receiving'?t('正在接收','Receiving'):state.glove?.connection==='connecting'?t('正在连接','Connecting'):t('未连接','Disconnected'));load();paint()});
 window.addEventListener('console-offline',()=>{state=null;gloveStatus.textContent=t('手套：状态不可用','Glove: status unavailable');paint()});
 window.addEventListener('wuji-language',labels);window.addEventListener('workspace-page',e=>{if(e.detail==='connection'){if(!info)requestKey='';load()}});
 labels();
})();
