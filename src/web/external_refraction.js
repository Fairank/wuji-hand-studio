/* Real background edge tiles are supplied by the opted-in native capture session.
   No browser screen-capture API, HTTP pixel endpoint, persistent preference or disk writes. */
(()=>{
 'use strict';
 const t=(a,b)=>window.WujiLocale?.lang==='en'?b:a,root=document.documentElement;
 let native=null,enabled=false,busy=false,last=0,timer=null,epoch=0;
 const row=document.createElement('label');row.className='desktop-menu-row';
 const label=document.createElement('span'),toggle=document.createElement('input');toggle.type='checkbox';toggle.id='external-refraction-toggle';toggle.disabled=true;row.append(label,toggle);
 const note=document.createElement('p');note.className='desktop-about';note.id='external-refraction-note';
 document.querySelector('#desktop-menu .desktop-menu-section')?.append(row,note);
 const canvases=new Map();
 const vertex='attribute vec2 p; varying vec2 v; void main(){v=(p+1.0)*0.5;gl_Position=vec4(p,0.,1.);}';
 const fragment=`precision mediump float;uniform sampler2D image;uniform vec2 dimensions;uniform float pad;uniform float side;varying vec2 v;
 void main(){vec2 uv=vec2(v.x,1.-v.y);vec2 size=dimensions-2.*pad;
 float cross=side<1.5?uv.y:uv.x;float lens=sin(cross*3.14159265);float bend=sin((cross-.5)*3.14159265)*lens*pad*.70;
 vec2 offset=side<1.5?vec2(0.,bend):vec2(bend,0.);vec2 sampleUV=(vec2(pad)+uv*size+offset)/dimensions;
 vec3 c=texture2D(image,sampleUV).rgb;float rim=pow(abs(cross*2.-1.),9.);
 c=mix(c,vec3(1.),.12+.36*rim);gl_FragColor=vec4(c,1.);}`;
 function shader(gl,type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('Refraction shader unavailable');return s;}
 function create(side){const canvas=document.createElement('canvas');canvas.className='external-glass-canvas';canvas.setAttribute('aria-hidden','true');canvas.dataset.side=side;document.body.append(canvas);
  const gl=canvas.getContext('webgl',{alpha:false,antialias:false,preserveDrawingBuffer:false});if(!gl){canvas.remove();throw Error('WebGL unavailable');}
  const program=gl.createProgram(),vs=shader(gl,gl.VERTEX_SHADER,vertex),fs=shader(gl,gl.FRAGMENT_SHADER,fragment);gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('Refraction shader unavailable');gl.useProgram(program);
  const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);const loc=gl.getAttribLocation(program,'p');gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
  const texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  canvas.addEventListener('webglcontextlost',()=>stop());return {canvas,gl,program,texture,buffer};
 }
 async function draw(tile,token){let item=canvases.get(tile.side);if(!item){item=create(tile.side);canvases.set(tile.side,item);}const img=new Image();img.src=tile.url;await img.decode();if(token!==epoch)return;
  const {canvas,gl,program}=item,[x,y,w,h]=tile.rect;Object.assign(canvas.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px'});canvas.width=Math.max(1,Math.round(w*devicePixelRatio));canvas.height=Math.max(1,Math.round(h*devicePixelRatio));gl.viewport(0,0,canvas.width,canvas.height);gl.useProgram(program);gl.bindTexture(gl.TEXTURE_2D,item.texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,img);gl.uniform2f(gl.getUniformLocation(program,'dimensions'),...tile.texture_size);gl.uniform1f(gl.getUniformLocation(program,'pad'),tile.pad);gl.uniform1f(gl.getUniformLocation(program,'side'),['top','bottom','left','right'].indexOf(tile.side));gl.drawArrays(gl.TRIANGLES,0,6);
 }
 function clear(){root.dataset.externalRefraction='false';for(const {canvas,gl,program,texture,buffer} of canvases.values()){gl.deleteTexture(texture);gl.deleteBuffer(buffer);gl.deleteProgram(program);canvas.remove();}canvases.clear();}
 function labels(){label.textContent=t('外部背景折射 · 实验','External refraction · Experimental');note.textContent=t('开启后 Windows 会采集当前显示器，在本机仅保留四周窄边数据；不保存、不上传。重启后默认关闭。','Windows captures this display; only narrow edge tiles are retained locally, without saving or upload. Off after restart.');}
 async function stop(){enabled=false;epoch++;clearTimeout(timer);toggle.checked=false;clear();if(native)await native.set_external_refraction(false);}
 async function poll(){if(!enabled||busy)return;busy=true;const token=epoch;try{const frame=await native.refraction_frame(last);if(token!==epoch)return;if(!frame.enabled){await stop();note.textContent=t('折射已停止，使用系统背景。','Refraction stopped; system backdrop is active.');return;}if(frame.reason!=='active'){clear();last=0;}else if(frame.tiles.length){last=frame.seq;await Promise.all(frame.tiles.map(tile=>draw(tile,token)));if(token===epoch)root.dataset.externalRefraction='true';}}catch(e){await stop();note.textContent=t('折射不可用：','Refraction unavailable: ')+e.message;}finally{busy=false;if(enabled)timer=setTimeout(poll,50);}}
 toggle.addEventListener('change',async()=>{if(!toggle.checked){await stop();return;}if(root.dataset.reduceTransparency==='true'){toggle.checked=false;note.textContent=t('请先关闭“减少透明效果”。','Turn off Reduce transparency first.');return;}toggle.disabled=true;try{const result=await native.set_external_refraction(true);enabled=result.enabled;toggle.checked=enabled;last=0;epoch++;if(enabled)poll();}catch(e){await stop();note.textContent=e.message;}finally{toggle.disabled=!native;}});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
 new MutationObserver(()=>{if(root.dataset.reduceTransparency==='true'||matchMedia('(forced-colors: active)').matches)stop();}).observe(root,{attributes:true,attributeFilter:['data-reduce-transparency']});
 function ready(){native=window.pywebview?.api||null;toggle.disabled=!native;}
 window.addEventListener('pywebviewready',ready);window.addEventListener('wuji-language',labels);ready();labels();
 window.WujiRefraction={redact(value){root.dataset.redactExternal=String(value);return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))));},
  async selfTest(){if(enabled)throw Error('Turn off external refraction before self-test');const c=document.createElement('canvas');c.width=500;c.height=70;const g=c.getContext('2d');g.fillStyle='#f5f5f7';g.fillRect(0,0,500,70);for(let x=0;x<500;x+=16){g.fillStyle=x%32===0?'#007aff':'#ff9f0a';g.fillRect(x,0,8,70);}const token=++epoch;await draw({side:'top',rect:[240,0,472,42],texture_size:[500,70],pad:14,url:c.toDataURL()},token);root.dataset.externalRefraction='true';setTimeout(()=>{if(!enabled)clear();},2500);return {ok:true,scope:'generated_reference_only',desktop_capture:false};},clearTest:clear};
})();
