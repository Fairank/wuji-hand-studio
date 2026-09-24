/* Real background edge tiles are supplied by the opted-in native capture session.
   No browser screen-capture API, HTTP pixel endpoint, persistent preference or disk writes. */
(()=>{
 'use strict';
 const t=(a,b)=>window.WujiLocale?.lang==='en'?b:a,root=document.documentElement;
 let native=null,enabled=false,busy=false,last=0,raf=null,motionUntil=0,epoch=0,stopping=null,lastStop=null,captureTargetHz=60;
 const health=new window.WujiRefractionHealth.RefractionHealth();
 const defaults={tint:35,feather:85,distortion:55};
 const preferences=Object.fromEntries(Object.entries(defaults).map(([key,value])=>{
  try{const raw=localStorage.getItem('wuji-refraction-'+key),saved=Number(raw);if(raw!==null&&Number.isFinite(saved)&&saved>=0&&saved<=100)return [key,saved];}catch{}
  return [key,value];
 }));
 const panel=document.createElement('div');panel.id='external-refraction-settings';panel.className='external-refraction-settings';
 const row=document.createElement('label');row.className='desktop-menu-row';
 const label=document.createElement('span'),toggle=document.createElement('input');toggle.type='checkbox';toggle.id='external-refraction-toggle';toggle.disabled=true;row.append(label,toggle);
 const note=document.createElement('p');note.className='desktop-about';note.id='external-refraction-note';
 panel.append(row,note);
 function slider(key){const line=document.createElement('div');line.className='refraction-control';
  const label=document.createElement('label');label.htmlFor='external-refraction-'+key;
  const input=document.createElement('input');input.type='range';input.id=label.htmlFor;input.min='0';input.max='100';input.step='1';input.value=String(preferences[key]);
  const output=document.createElement('output');output.htmlFor=input.id;output.textContent=input.value+'%';
  line.append(label,input,output);return {line,label,input,output};
 }
 const tint=slider('tint'),feather=slider('feather'),distortion=slider('distortion');
 const scaleLabels=document.createElement('div');scaleLabels.className='refraction-scale';
 const clearLabel=document.createElement('span'),tintedLabel=document.createElement('span');scaleLabels.append(clearLabel,tintedLabel);
 const advanced=document.createElement('details');advanced.className='refraction-advanced';
 const advancedTitle=document.createElement('summary');advanced.append(advancedTitle,feather.line,distortion.line);
 panel.append(tint.line,scaleLabels,advanced);
 document.querySelector('#desktop-menu .desktop-menu-section')?.append(panel);
 const canvases=new Map();
 const vertex='attribute vec2 p; varying vec2 v; void main(){v=(p+1.0)*0.5;gl_Position=vec4(p,0.,1.);}';
 const fragment=`precision mediump float;uniform sampler2D image;uniform vec2 dimensions;uniform float pad;uniform float side;uniform float tint;uniform float feather;uniform float distortion;uniform float shade;varying vec2 v;
 void main(){vec2 uv=vec2(v.x,1.-v.y);vec2 size=dimensions-2.*pad;
 float cross=side<1.5?uv.y:uv.x;float lens=sin(cross*3.14159265);float bend=sin((cross-.5)*3.14159265)*lens*pad*(.12+distortion*.88);
 vec2 offset=side<1.5?vec2(0.,bend):vec2(bend,0.);vec2 sampleUV=(vec2(pad)+uv*size+offset)/dimensions;
 vec3 c=texture2D(image,sampleUV).rgb;float rim=pow(abs(cross*2.-1.),9.);
 c=mix(c,vec3(shade),.05+tint*.36+.18*rim);
 float inward=side<.5?uv.y:side<1.5?1.-uv.y:side<2.5?uv.x:1.-uv.x;
 float fade=1.-smoothstep(1.-feather,1.,inward);
 float opacity=fade*(.68+.28*tint);gl_FragColor=vec4(c*opacity,opacity);}`;
 function shader(gl,type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('Refraction shader unavailable');return s;}
 function create(side){const canvas=document.createElement('canvas');canvas.className='external-glass-canvas';canvas.setAttribute('aria-hidden','true');canvas.dataset.side=side;document.body.append(canvas);
  const gl=canvas.getContext('webgl',{alpha:true,antialias:false,preserveDrawingBuffer:false});if(!gl){canvas.remove();throw Error('WebGL unavailable');}
  const program=gl.createProgram(),vs=shader(gl,gl.VERTEX_SHADER,vertex),fs=shader(gl,gl.FRAGMENT_SHADER,fragment);gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('Refraction shader unavailable');gl.useProgram(program);
  const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);const loc=gl.getAttribLocation(program,'p');gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
  const texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  canvas.addEventListener('webglcontextlost',()=>stop());return {canvas,gl,program,texture,buffer,textureWidth:0,textureHeight:0,
   dimensions:gl.getUniformLocation(program,'dimensions'),pad:gl.getUniformLocation(program,'pad'),side:gl.getUniformLocation(program,'side'),
   tint:gl.getUniformLocation(program,'tint'),feather:gl.getUniformLocation(program,'feather'),
   distortion:gl.getUniformLocation(program,'distortion'),shade:gl.getUniformLocation(program,'shade'),lastTile:null};
 }
 function paint(item,tile){const {gl,program}=item;gl.useProgram(program);gl.bindTexture(gl.TEXTURE_2D,item.texture);
  gl.uniform2f(item.dimensions,...tile.texture_size);gl.uniform1f(item.pad,tile.pad);
  gl.uniform1f(item.side,['top','bottom','left','right'].indexOf(tile.side));
  gl.uniform1f(item.tint,preferences.tint/100);gl.uniform1f(item.feather,.35+preferences.feather*.0065);
  gl.uniform1f(item.distortion,preferences.distortion/100);gl.uniform1f(item.shade,root.dataset.theme==='dark'?.16:1);
  gl.drawArrays(gl.TRIANGLES,0,6);item.lastTile=tile;
 }
 function draw(tile,img){let item=canvases.get(tile.side);if(!item){item=create(tile.side);canvases.set(tile.side,item);}
  const {canvas,gl,program}=item,[x,y,w,h]=tile.rect;Object.assign(canvas.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px'});
  const width=Math.max(1,Math.round(w*devicePixelRatio)),height=Math.max(1,Math.round(h*devicePixelRatio));
  if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;gl.viewport(0,0,width,height);}
  gl.useProgram(program);gl.bindTexture(gl.TEXTURE_2D,item.texture);
  if(item.textureWidth===img.naturalWidth&&item.textureHeight===img.naturalHeight){gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,gl.RGB,gl.UNSIGNED_BYTE,img);}
  else{gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,img);item.textureWidth=img.naturalWidth;item.textureHeight=img.naturalHeight;}
  paint(item,tile);
 }
 function repaint(){for(const item of canvases.values())if(item.lastTile)paint(item,item.lastTile);}
 async function decode(tile){const img=new Image();img.src=tile.url;await img.decode();return {tile,img};}
 function clear(){root.dataset.externalRefraction='false';for(const {canvas,gl,program,texture,buffer} of canvases.values()){gl.deleteTexture(texture);gl.deleteBuffer(buffer);gl.deleteProgram(program);canvas.remove();}canvases.clear();}
 function labels(){label.textContent=t('外部背景折射 · 实验','External refraction · Experimental');
  note.textContent=t('开启后仅在本机处理显示器四周窄边；不保存、不上传，重启后默认关闭。','Captures narrow display edges locally, without saving or upload. Off after restart.');
  tint.label.textContent=t('玻璃外观','Glass appearance');clearLabel.textContent=t('清透','Clear');tintedLabel.textContent=t('染色','Tinted');
  advancedTitle.textContent=t('更多效果调节','More effect controls');feather.label.textContent=t('边缘过渡','Edge softness');distortion.label.textContent=t('折射幅度','Refraction');
 }
 for(const [key,control] of Object.entries({tint,feather,distortion}))control.input.addEventListener('input',()=>{
  preferences[key]=Number(control.input.value);control.output.textContent=control.input.value+'%';
  try{localStorage.setItem('wuji-refraction-'+key,control.input.value);}catch{}
  repaint();
 });
 async function stop(fault=null){
  if(stopping)return stopping;
  enabled=false;epoch++;motionUntil=0;if(raf!==null)cancelAnimationFrame(raf);raf=null;toggle.checked=false;clear();
  if(fault){lastStop=fault;note.textContent=fault.gap_ms!==undefined
   ?t('折射检测到 '+fault.gap_ms+' 毫秒停顿，已自动关闭。','Refraction stopped after a '+fault.gap_ms+' ms stall.')
   :t('折射刷新降至 '+fault.hz+' Hz，已自动关闭。','Refraction fell to '+fault.hz+' Hz and stopped.');}
  stopping=(async()=>{try{if(native)await native.set_external_refraction(false);}finally{stopping=null;}})();
  return stopping;
 }
 async function poll(){
  if(!enabled||busy)return;busy=true;const token=epoch;
  try{
   const frame=await native.refraction_frame(last);if(token!==epoch)return;
   if(Number.isFinite(frame.capture_target_hz))captureTargetHz=frame.capture_target_hz;
   if(!frame.enabled){await stop();note.textContent=t('折射已停止，使用系统背景。','Refraction stopped; system backdrop is active.');return;}
   const captureFault=health.capture(frame.recent_capture_hz,frame.recent_capture_frames);
   if(captureFault){await stop(captureFault);return;}
   if(frame.reason!=='active'){clear();last=0;}
   else if(frame.tiles.length){last=frame.seq;const began=performance.now();
    const decoded=await Promise.all(frame.tiles.map(decode));if(token!==epoch)return;
    await new Promise(resolve=>requestAnimationFrame(resolve));if(token!==epoch)return;
    for(const {tile,img} of decoded)draw(tile,img);root.dataset.externalRefraction='true';
    const now=performance.now(),fault=health.processing(now-began,true)||health.present(now,frame.recent_capture_frames);
    if(fault){void stop(fault);return;}
    if(frame.recent_capture_frames>=2){motionUntil=now+100;if(raf===null)raf=requestAnimationFrame(tick);}}
  }catch(e){await stop();note.textContent=t('折射不可用：','Refraction unavailable: ')+e.message;}
  finally{busy=false;if(enabled&&token===epoch)queueMicrotask(()=>{void poll();});}
 }
 function tick(now){
  if(!enabled)return;
  const fault=health.display(now,root.dataset.externalRefraction==='true');
  if(fault){void stop(fault);return;}
  if(now>=motionUntil){health.display(now,false);raf=null;return;}
  raf=requestAnimationFrame(tick);
 }
 toggle.addEventListener('change',async()=>{if(!toggle.checked){await stop();return;}if(root.dataset.reduceTransparency==='true'){toggle.checked=false;note.textContent=t('请先关闭“减少透明效果”。','Turn off Reduce transparency first.');return;}toggle.disabled=true;try{if(stopping)await stopping;const result=await native.set_external_refraction(true);enabled=result.enabled;toggle.checked=enabled;last=0;epoch++;motionUntil=0;health.reset();lastStop=null;if(enabled){note.textContent=t('跟随显示器刷新；静止时等待新画面，卡顿会自动关闭。','Display-paced; waits for changes when idle and stops on a stall.');void poll();}}catch(e){await stop();note.textContent=e.message;}finally{toggle.disabled=!native;}});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
 new MutationObserver(()=>{if(root.dataset.reduceTransparency==='true'||matchMedia('(forced-colors: active)').matches)stop();}).observe(root,{attributes:true,attributeFilter:['data-reduce-transparency']});
 function ready(){native=window.pywebview?.api||null;toggle.disabled=!native;}
 window.addEventListener('pywebviewready',ready);window.addEventListener('wuji-language',labels);ready();labels();
 new MutationObserver(repaint).observe(root,{attributes:true,attributeFilter:['data-theme']});
 window.WujiRefraction={metrics(){return {...health.stats(),capture_target_hz:captureTargetHz,active:enabled&&root.dataset.externalRefraction==='true',last_stop:lastStop};},redact(value){root.dataset.redactExternal=String(value);return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))));},
  async selfTest(){if(enabled)throw Error('Turn off external refraction before self-test');const c=document.createElement('canvas');c.width=500;c.height=70;const g=c.getContext('2d');g.fillStyle='#f5f5f7';g.fillRect(0,0,500,70);for(let x=0;x<500;x+=16){g.fillStyle=x%32===0?'#007aff':'#ff9f0a';g.fillRect(x,0,8,70);}epoch++;const tile={side:'top',rect:[240,0,472,42],texture_size:[500,70],pad:14,url:c.toDataURL()};const {img}=await decode(tile);draw(tile,img);draw(tile,img);root.dataset.externalRefraction='true';setTimeout(()=>{if(!enabled)clear();},2500);return {ok:true,scope:'generated_reference_only',desktop_capture:false};},clearTest:clear};
})();
