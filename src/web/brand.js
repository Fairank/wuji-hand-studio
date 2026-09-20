(function(){
  const heading=document.querySelector('.top h1');
  if(heading){const logo=document.createElement('img');logo.src='/wuji-logo.png';logo.alt='WUJI';logo.className='studio-brand';heading.before(logo);}
  const note=document.createElement('span');note.className='unofficial-notice';document.querySelector('.app>footer')?.prepend(note);
  const rail=document.createElement('span');rail.className='nav-brand-note';document.querySelector('.directory')?.append(rail);
  function label(){const en=document.documentElement.lang==='en';note.textContent=en?'Unofficial personal demonstration tool. Not endorsed or maintained by Wuji Technology.':'非官方个人展示工具 · 非舞肌科技官方发布或维护';rail.textContent=en?'Unofficial · Personal demonstration':'非官方 · 个人展示工具';}
  label();window.addEventListener('wuji-language',label);
})();
