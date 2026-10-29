/* Demonstration model choice is local display state, never SDK ownership. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),ws=window.WujiWorkspace;if(!ws)return;
 const valid=id=>typeof id==='string'&&/^hand[12]_(left|right)$/.test(id),key='wuji-demonstration-profile.v1';
 const text=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const control=document.createElement('div');control.className='device-profile-control';
 const label=document.createElement('label'),select=document.createElement('select'),note=document.createElement('p');
 select.id='device-profile';label.htmlFor=select.id;note.className='hint';note.setAttribute('role','status');control.append(label,select,note);
 // The connection layout initializes before the demonstration header. Reuse
 // the same control node when the final source strip mounts below.
 ws.views.connection.prepend(control);
 let selected='hand2_left',revision=0,stored=false,state=null,optionLanguage='';
 try{const value=localStorage.getItem(key);if(valid(value)){selected=value;stored=true;}}catch{}
 const profiles=['hand2_left','hand2_right','hand1_left','hand1_right'];
 const name=id=>'Wuji Hand '+(id.startsWith('hand2')?'2':'1')+' · '+text(id.endsWith('left')?'左手':'右手',id.endsWith('left')?'Left':'Right');
 function labels(){
  label.textContent=text('展示手型','Demonstration model');select.setAttribute('aria-label',label.textContent);
  const language=window.WujiLocale?.lang||'zh';if(optionLanguage!==language){optionLanguage=language;select.replaceChildren(...profiles.map(id=>new Option(name(id),id)));}if(select.value!==selected)select.value=selected;
  note.hidden=true;
  if(document.body.dataset.page==='library'){
   const heading=$('pose-console')?.querySelector('h2');if(heading)heading.textContent=name(selected);
   if($('pose-image'))$('pose-image').alt=name(selected)+text('原生模型',' native model');
  }
 }
 function choose(value,persist=true){
  if(!valid(value))return false;if(value===selected){labels();return true;}
  selected=value;revision++;if(persist)try{localStorage.setItem(key,value);}catch{}
  labels();window.dispatchEvent(new CustomEvent('demonstration-profile',{detail:{profile:selected,revision}}));return true;
 }
 select.addEventListener('change',()=>choose(select.value));
 window.WujiDemonstration={profile:()=>selected,revision:()=>revision,selectProfile:choose,
  mount(host){if(!host)return;host.append(control);control.append(note);labels();},
  playback(value=state){return value?.demonstration_playback?.[selected]||{active:false,display_profile:selected};},
  profileLabel:()=>name(selected)};
 window.addEventListener('console-state',event=>{state=event.detail;labels();});
 window.addEventListener('wuji-language',labels);window.addEventListener('workspace-page',labels);
 labels();
 const initialRevision=revision;
 fetch('/api/installation',{cache:'no-store',signal:AbortSignal.timeout(6000)}).then(r=>{if(!r.ok)throw Error('installation');return r.json();}).then(info=>{if(!stored&&revision===initialRevision&&valid(info.selected_profile))choose(info.selected_profile,false);}).catch(()=>{});
})();
