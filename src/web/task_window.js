/* A task window hosts one existing scoped frame. It never starts discovery or device keepalives. */
(()=>{'use strict';
 const frames=new Map(),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 window.WujiWorkspace={views:{},showPage(){document.body.dataset.page='tasks';window.dispatchEvent(new CustomEvent('workspace-page',{detail:'tasks'}));}};
 window.HandSessions={taskFrames:window.WujiTaskFrames.create({frames})};
 function appearance(){let theme='system';try{theme=localStorage.getItem('wuji-workbench-theme')||theme;document.documentElement.dataset.reduceMotion=localStorage.getItem('wuji-reduceMotion')||'false';}catch{}
  document.documentElement.dataset.theme=theme==='dark'||theme==='system'&&matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';document.documentElement.lang=window.WujiLocale.lang==='en'?'en':'zh-CN';
  for(const item of frames.values())if(item.ready){item.frame.contentWindow?.postMessage({type:'wb-theme',value:theme},item.origin);item.frame.contentWindow?.postMessage({type:'wb-task-language',task_id:item.frame.dataset.taskId,language:window.WujiLocale.lang},item.origin);}
 }
 window.addEventListener('storage',event=>{if(event.key==='wuji-language'&&['zh','en'].includes(event.newValue))window.WujiLocale.setLanguage(event.newValue);appearance();});window.addEventListener('wuji-language',appearance);matchMedia('(prefers-color-scheme:dark)').addEventListener('change',appearance);appearance();
 document.title=t('设备任务','Device task')+' · Hand Workbench';
})();
