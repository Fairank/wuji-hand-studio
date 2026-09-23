(function(){
  const heading=document.querySelector('.top h1');
  if(heading){const logo=document.createElement('img');logo.src='/wuji-logo.png';logo.alt='WUJI';logo.className='studio-brand';heading.before(logo);}
  const footer=document.querySelector('.page-area>footer,.app>footer');
  const note=document.createElement('span');note.className='unofficial-notice';footer?.prepend(note);
  const rail=document.createElement('span');rail.className='nav-brand-note';document.querySelector('.directory')?.append(rail);
  const disclaimer=document.createElement('span');
  for(const child of [...footer.childNodes])if(child.nodeType===Node.TEXT_NODE)child.remove();
  footer.append(disclaimer);
  function label(){const en=document.documentElement.lang==='en';note.textContent=en?'Unofficial personal demonstration tool. Not endorsed or maintained by Wuji Technology.':'非官方个人展示工具 · 非舞肌科技官方发布或维护';rail.textContent=en?'Unofficial · Personal demonstration':'非官方 · 个人展示工具';}
  function footerLabel(){const en=document.documentElement.lang==='en';disclaimer.textContent=en?'Stopping capture only ends recording; it is not a hardware emergency stop. Preview and measured feedback do not certify touch recognition.':'停止采集仅停止记录，不是硬件急停。画面显示模型状态或实测反馈，不代表触碰识别已经通过。';}
  label();footerLabel();window.addEventListener('wuji-language',()=>{label();footerLabel();});
})();
