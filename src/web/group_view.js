/* Actual per-session MuJoCo images. Display only: no motion or target synthesis. */
(()=>{
 'use strict';
 window.HandGroupView={mount(root,{onCamera}){
  const rows=new Map();let language='zh',dead=false,timer=null;
  const text=(zh,en)=>language==='en'?en:zh;
  const host=document.createElement('div');host.className='hgv-grid';root.append(host);
  function add(member){
   const card=document.createElement('article');card.className='hgv-card';
   card.innerHTML='<header><strong></strong><button type="button"></button></header><div class="hgv-picture"><img draggable="false"><span class="hgv-unavailable"></span></div><p class="hgv-source"></p>';
   const row={card,img:card.querySelector('img'),status:card.querySelector('.hgv-source'),notice:card.querySelector('.hgv-unavailable'),button:card.querySelector('button'),member,busy:false,cameraBusy:false,camera:null,request:null};
   row.button.addEventListener('click',async()=>{
    if(row.cameraBusy)return;row.cameraBusy=true;row.button.disabled=true;
    try{await onCamera(row.member.id,{azimuth:row.member.side==='left'?270:90,elevation:-5,distance:.38,lookat:[0,0,.115]});}
    catch(e){row.status.textContent=String(e.message||e);}finally{row.cameraBusy=false;row.button.disabled=false;}
   });
   let pointer=null;
   row.img.addEventListener('pointerdown',e=>{if(e.button!==0||!row.camera)return;pointer={x:e.clientX,y:e.clientY,camera:structuredClone(row.camera)};row.img.setPointerCapture(e.pointerId);});
   row.img.addEventListener('pointerup',async e=>{
    if(!pointer)return;const p=pointer;pointer=null;if(row.cameraBusy)return;
    const camera={...p.camera,azimuth:p.camera.azimuth-(e.clientX-p.x)*.45,elevation:p.camera.elevation+(e.clientY-p.y)*.35};
    row.cameraBusy=true;try{await onCamera(row.member.id,camera);}catch(error){row.status.textContent=String(error.message||error);}finally{row.cameraBusy=false;}
   });
   row.img.addEventListener('pointercancel',()=>{pointer=null;});
   host.append(card);return row;
  }
  async function frame(row){
   if(row.busy)return;row.busy=true;const controller=new AbortController();row.request=controller;
   const timeout=setTimeout(()=>controller.abort(),1800);
   try{
    const reply=await fetch('/api/workspace-view?member='+encodeURIComponent(row.member.id),{cache:'no-store',signal:controller.signal});
    if(!reply.ok)throw new Error('HTTP '+reply.status);
    const result=await reply.json();if(dead||!row.card.isConnected)return;
    const m=result.meta||{},preview=m.mode==='demo'||m.mode==='glove_preview';
    const measured=!preview&&m.feedback?.stale===false&&m.active_joints>0;
    row.camera=m.camera||null;
    // A stale/unknown source is explicitly covered, never presented as current feedback.
    const valid=m.ready===true&&typeof result.image==='string'&&result.image.startsWith('data:image/jpeg;base64,');
    if(valid)row.img.src=result.image;
    row.img.hidden=!valid;row.notice.hidden=valid;
    row.notice.textContent=text('画面暂不可用','View unavailable');
    const source=preview?text('动作预览 · 未驱动实机','Action preview · no physical motion'):measured?text('实测关节反馈','Measured joint feedback'):text('模型参考姿态 · 无实时反馈','Reference pose · no live feedback');
    row.status.textContent=source+(valid?' · '+Math.round(m.render_hz||0)+' fps':' · '+text('画面已中断','View interrupted'));
    row.card.dataset.source=preview?'preview':measured?'measured':'reference';
   }catch(e){if(!dead&&row.card.isConnected){row.img.hidden=true;row.notice.hidden=false;row.notice.textContent=text('画面连接中断','View connection interrupted');row.status.textContent=String(e.message||e);}}
   finally{clearTimeout(timeout);row.busy=false;row.request=null;}
  }
  function tick(){
   if(dead)return;
   if(!document.hidden&&root.getClientRects().length)for(const row of rows.values())frame(row);
   timer=setTimeout(tick,100); // at most one outstanding request per session
  }
  function render(snapshot,lang){
   language=lang;const members=[...(snapshot.members||[])];
   // Native front cameras: right hand at viewer left, left hand at viewer right.
   members.sort((a,b)=>(a.side==='right'?0:1)-(b.side==='right'?0:1));
   const ids=new Set(members.map(m=>m.id));
   for(const [id,row]of rows)if(!ids.has(id)){row.request?.abort();row.card.remove();rows.delete(id);}
   members.forEach((m,i)=>{
    let row=rows.get(m.id);if(!row){row=add(m);rows.set(m.id,row);}row.member=m;
    row.card.querySelector('strong').textContent=(m.side==='left'?text('左手','Left hand'):text('右手','Right hand'))+' · '+m.label;
    row.img.alt=text('原生 MuJoCo 手部姿态','Native MuJoCo hand pose');row.img.title=text('拖动后松开调整视角','Drag and release to rotate the view');row.button.textContent=text('正面视角','Front view');
    if(host.children[i]!==row.card)host.insertBefore(row.card,host.children[i]||null);
   });
   root.hidden=!members.length;
  }
  tick();return {render,destroy(){dead=true;clearTimeout(timer);for(const r of rows.values())r.request?.abort();host.remove();rows.clear();}};
 }};
})();
