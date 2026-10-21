/* Selection follows existing ARIA state. No routing, control requests or frame loop. */
(()=>{
  'use strict';
  const HOSTS='.directory,.hub-tabs,.parameter-tabs,.playback-modes,.symbol-kinds,.symbol-options,.wsx-manage-tabs,.rt-segments,.hg-seg,.hg-targets,.di-filters,.pe-mode,.view-sources,.ji-fingers,.ji-joints';
  const ITEMS='a[aria-current],a[data-page],button[aria-selected],button[aria-pressed],[role=tab],label:has(> input[type=radio])';
  const SELECTED='[aria-current=page],[aria-selected=true],[aria-pressed=true],label:has(> input[type=radio]:checked)';
  let mounted=null;
  const near=(a,b)=>a&&b&&['x','y','w','h'].every(key=>Math.abs(a[key]-b[key])<.25);
  const transform=box=>`translate(${box.x}px,${box.y}px) scale(${box.sx},${box.sy})`;

  function mount(scope=document){
    if(mounted)return mounted.api;
    if(!scope?.querySelectorAll)throw TypeError('Navigation motion requires a DOM scope');
    const root=document.documentElement,groups=new Map(),dirty=new Map();
    const reducedSystem=matchMedia('(prefers-reduced-motion: reduce)'),forced=matchMedia('(forced-colors: active)');
    let frame=0,destroyed=false;
    const reduced=()=>root.dataset.reduceMotion==='true'||reducedSystem.matches;

    function schedule(group,snap=false){
      if(destroyed)return;
      dirty.set(group,!!snap||dirty.get(group)===true);
      if(!frame&&!document.hidden)frame=requestAnimationFrame(flush);
    }
    function flush(){
      frame=0;const work=[...dirty];dirty.clear();
      for(const [group,snap] of work){if(group.host.isConnected)group.update(snap);else remove(group);}
    }
    function refresh(snap=true){for(const group of groups.values())schedule(group,snap);}

    class Group{
      constructor(host){
        this.host=host;this.items=new Set();this.target=null;this.goal=null;this.animation=null;this.visible=false;
        this.position=host.style.position;this.positionChanged=getComputedStyle(host).position==='static';
        if(this.positionChanged)host.style.position='relative';
        host.classList.add('nm-host');
        this.indicator=document.createElement('span');this.indicator.className='nm-indicator';
        this.indicator.setAttribute('aria-hidden','true');this.indicator.hidden=true;host.append(this.indicator);
        this.resize=new ResizeObserver(()=>schedule(this,true));this.resize.observe(host);
        this.intersection=new IntersectionObserver(entries=>{
          if(entries.some(entry=>!entry.isIntersecting))this.suspend();
          else schedule(this,true);
        });this.intersection.observe(host);
        this.collect();schedule(this,true);
      }
      collect(){
        // Rebuilt option lists may replace every child. Reuse the same surface.
        if(this.indicator.parentNode!==this.host)this.host.append(this.indicator);
        const next=new Set([...this.host.querySelectorAll(ITEMS)].filter(item=>item.closest(HOSTS)===this.host));
        for(const item of this.items)if(!next.has(item)){this.resize.unobserve(item);item.removeAttribute('data-nm-item');}
        for(const item of next)if(!this.items.has(item)){item.dataset.nmItem='';this.resize.observe(item);}
        this.items=next;
      }
      selected(){
        const selected=[...this.items].find(item=>item.matches(SELECTED));
        if(!selected)return null;
        // A closed tool disclosure represents its selected, currently hidden page.
        const closed=selected.closest('details:not([open])');
        return closed&&this.host.contains(closed)?closed.querySelector(':scope > summary'):selected;
      }
      mark(target){
        if(this.target===target)return;
        if(this.target){
          if(this.target.dataset.nmProxy==='true')this.target.removeAttribute('data-nm-item');
          this.target.removeAttribute('data-nm-visual-selected');this.target.removeAttribute('data-nm-proxy');
        }
        this.target=target;
        if(target){
          target.dataset.nmVisualSelected='true';
          if(!this.items.has(target)){target.dataset.nmItem='';target.dataset.nmProxy='true';}
        }
      }
      cancel(){
        if(this.animation){this.animation.cancel();this.animation=null;}
        this.indicator.style.willChange='';
      }
      suspend(){
        this.cancel();this.visible=false;this.indicator.hidden=true;this.host.dataset.nmReady='false';
      }
      geometry(target,hostRect,hostScale){
        const r=target.getBoundingClientRect(),style=getComputedStyle(target);
        // Individual press scale is visual only. Track the unpressed hit-area box.
        const scales=style.scale==='none'?[1,1]:style.scale.split(/\s+/).map(Number);
        const sx=Number.isFinite(scales[0])&&scales[0]>0?scales[0]:1;
        const sy=Number.isFinite(scales[1])&&scales[1]>0?scales[1]:sx;
        const w=r.width/sx/hostScale.x,h=r.height/sy/hostScale.y;
        return {x:(r.left+(r.width-w*hostScale.x)/2-hostRect.left)/hostScale.x-this.host.clientLeft+this.host.scrollLeft,
          y:(r.top+(r.height-h*hostScale.y)/2-hostRect.top)/hostScale.y-this.host.clientTop+this.host.scrollTop,w,h};
      }
      keyframes(from,to){
        const distance=Math.max(Math.abs(to.x-from.x),Math.abs(to.y-from.y),Math.abs(to.w-from.w),Math.abs(to.h-from.h),1);
        const cap=Math.min(1,2.4/(distance*.02)),zeta=.78,omega=11.5,damped=omega*Math.sqrt(1-zeta*zeta),frames=[];
        for(let i=0;i<=50;i++){
          const time=i/50;
          let p=i===50?1:1-Math.exp(-zeta*omega*time)*(Math.cos(damped*time)+zeta/Math.sqrt(1-zeta*zeta)*Math.sin(damped*time));
          if(p>1)p=1+(p-1)*cap;
          const value=key=>from[key]+(to[key]-from[key])*p;
          frames.push({offset:time,transform:transform({x:value('x'),y:value('y'),sx:Math.max(.01,value('w')/to.w),sy:Math.max(.01,value('h')/to.h)})});
        }
        return frames;
      }
      update(snap){
        this.collect();const target=this.selected(),previous=this.target;this.mark(target);
        if(!target||document.hidden||forced.matches||!this.host.getClientRects().length||!target.getClientRects().length){this.suspend();return;}
        const bounds=this.host.getBoundingClientRect(),style=getComputedStyle(this.host);
        if(style.visibility==='hidden'||bounds.bottom<=0||bounds.top>=innerHeight||bounds.right<=0||bounds.left>=innerWidth){this.suspend();return;}
        const size=(axis,extra)=>parseFloat(style[axis])+(style.boxSizing==='border-box'?0:extra.reduce((sum,key)=>sum+(parseFloat(style[key])||0),0));
        const hostScale={x:bounds.width/(size('width',['paddingLeft','paddingRight','borderLeftWidth','borderRightWidth'])||bounds.width),
          y:bounds.height/(size('height',['paddingTop','paddingBottom','borderTopWidth','borderBottomWidth'])||bounds.height)};
        const goal=this.geometry(target,bounds,hostScale);
        if(goal.w<=0||goal.h<=0){this.suspend();return;}
        if(this.visible&&previous===target&&near(goal,this.goal)){
          if(reduced())this.cancel();
          return;
        }
        // Read the compositor's current position once before cancelling an interrupted spring.
        const current=this.visible?this.indicator.getBoundingClientRect():null;
        const from=current?{x:(current.left-bounds.left)/hostScale.x-this.host.clientLeft+this.host.scrollLeft,
          y:(current.top-bounds.top)/hostScale.y-this.host.clientTop+this.host.scrollTop,w:current.width/hostScale.x,h:current.height/hostScale.y}:goal;
        const animate=this.visible&&!snap&&!reduced()&&!near(from,goal)&&typeof this.indicator.animate==='function';
        this.cancel();this.goal=goal;this.visible=true;this.indicator.hidden=false;this.host.dataset.nmReady='true';
        Object.assign(this.indicator.style,{width:goal.w+'px',height:goal.h+'px',transform:transform({x:goal.x,y:goal.y,sx:1,sy:1})});
        if(!animate)return;
        this.indicator.style.willChange='transform';
        const animation=this.indicator.animate(this.keyframes(from,goal),{duration:460,easing:'linear',iterations:1});
        this.animation=animation;
        animation.finished.then(()=>{if(this.animation===animation){this.animation=null;this.indicator.style.willChange='';}},()=>{});
      }
      destroy(){
        this.cancel();this.resize.disconnect();this.intersection.disconnect();this.mark(null);
        for(const item of this.items)item.removeAttribute('data-nm-item');
        this.indicator.remove();this.host.classList.remove('nm-host');this.host.removeAttribute('data-nm-ready');
        if(this.positionChanged)this.host.style.position=this.position;
      }
    }

    function add(host){if(!groups.has(host)&&host.isConnected){const group=new Group(host);groups.set(host,group);}}
    function remove(group){group.destroy();groups.delete(group.host);dirty.delete(group);}
    function discover(node){
      if(node.nodeType===1&&node.matches(HOSTS))add(node);
      if(node.querySelectorAll)for(const host of node.querySelectorAll(HOSTS))add(host);
    }
    const own=node=>node.nodeType===1&&(node.matches('.nm-indicator')||!!node.closest('.nm-indicator'));
    const observer=new MutationObserver(records=>{
      for(const record of records){
        if(own(record.target))continue;
        if(record.type==='attributes'&&record.oldValue===record.target.getAttribute(record.attributeName))continue;
        if(record.type==='characterData'&&record.oldValue===record.target.data)continue;
        if(record.type==='childList'){
          const nodes=[...record.addedNodes,...record.removedNodes];if(nodes.length&&nodes.every(own))continue;
          // Existing locale refreshes rewrite identical labels on every route change.
          // They must not turn a simultaneous selection spring into a layout snap.
          if(nodes.length&&nodes.every(node=>node.nodeType===3)&&
            [...record.addedNodes].map(node=>node.data).join('')===[...record.removedNodes].map(node=>node.data).join(''))continue;
          for(const node of record.addedNodes)if(node.nodeType===1)discover(node);
        }
        const selection=['aria-current','aria-selected','aria-pressed'].includes(record.attributeName);
        for(const group of groups.values())if(group.host.contains(record.target)||record.target.contains?.(group.host))schedule(group,!selection);
      }
      for(const group of [...groups.values()])if(!group.host.isConnected)remove(group);
    });
    observer.observe(scope,{subtree:true,childList:true,characterData:true,characterDataOldValue:true,attributes:true,attributeOldValue:true,
      attributeFilter:['aria-current','aria-selected','aria-pressed','hidden','open','lang','dir']});
    const preferences=new MutationObserver(records=>{if(records.some(record=>record.oldValue!==record.target.getAttribute(record.attributeName)))refresh(true);});
    preferences.observe(root,{attributes:true,attributeOldValue:true,attributeFilter:['data-reduce-motion','data-theme']});
    if(document.body)preferences.observe(document.body,{attributes:true,attributeOldValue:true,attributeFilter:['class','style']});
    const onLayout=()=>refresh(true),onPage=()=>refresh(false),onPreference=()=>{for(const group of groups.values())if(reduced()||forced.matches)group.cancel();refresh(true);};
    // Native radios change their checked property without an attribute mutation.
    // Observe the user event; leave selection, keyboard and command handling owned
    // by the existing panel rather than mirroring state in the motion layer.
    const onChange=event=>{
      if(!event.target.matches?.('input[type=radio]'))return;
      const group=groups.get(event.target.closest(HOSTS));
      if(group)schedule(group,false);
    };
    const onSync=event=>{
      const group=groups.get(event.target.closest?.(HOSTS));
      if(group)schedule(group,false);
    };
    const onVisibility=()=>{
      if(document.hidden){if(frame)cancelAnimationFrame(frame);frame=0;for(const group of groups.values())group.suspend();}
      else refresh(true);
    };
    window.addEventListener('resize',onLayout);window.addEventListener('wuji-language',onLayout);
    window.addEventListener('workspace-page',onPage);
    document.addEventListener('visibilitychange',onVisibility);
    scope.addEventListener('change',onChange);
    scope.addEventListener('wuji-selection-sync',onSync);
    reducedSystem.addEventListener('change',onPreference);forced.addEventListener('change',onPreference);
    const fonts=document.fonts;fonts?.addEventListener('loadingdone',onLayout);
    discover(scope);
    const api={refresh:()=>refresh(true),destroy(){
      destroyed=true;if(frame)cancelAnimationFrame(frame);observer.disconnect();preferences.disconnect();
      for(const group of [...groups.values()])remove(group);
      window.removeEventListener('resize',onLayout);window.removeEventListener('wuji-language',onLayout);
      window.removeEventListener('workspace-page',onPage);
      document.removeEventListener('visibilitychange',onVisibility);reducedSystem.removeEventListener('change',onPreference);
      scope.removeEventListener('change',onChange);
      scope.removeEventListener('wuji-selection-sync',onSync);
      forced.removeEventListener('change',onPreference);fonts?.removeEventListener('loadingdone',onLayout);mounted=null;
    }};
    mounted={scope,api};return api;
  }
  window.WujiNavigationMotion={mount};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>mount(),{once:true});else mount();
})();
