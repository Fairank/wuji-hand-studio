(function(){
  const heading=document.querySelector('.top h1');
  if(heading){const logo=document.createElement('img');logo.src='/wuji-logo.png';logo.alt='WUJI';logo.width=48;logo.height=14;logo.className='studio-brand';heading.before(logo);}
  // Product attribution remains in Settings/About and the desktop menu.
  // Do not recreate persistent rail/footer notices on language changes.
  for(const node of document.querySelectorAll('.nav-brand-note,.unofficial-notice,.page-area>footer,.app>footer'))node.remove();
})();
