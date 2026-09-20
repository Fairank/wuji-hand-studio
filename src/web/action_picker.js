/* Presentation only: returns an existing catalog id; never sends commands. */
(() => {
  'use strict';
  window.WujiActionPicker={create({root,text,onSelect}){
    let catalog=[],selected='official_opposition',lastHardware=selected,kind='letters',busy=false,allowPreview=false;
    const letters='ABCDEFGHIKLMNOPQRSTUVWXY',digits='0123456789';
    root.className='compact-picker';
    root.innerHTML=`<label for="studio-action" id="studio-action-label"></label><div class="action-select-row"><select id="studio-action"></select><button type="button" id="choose-letter"></button><button type="button" id="choose-digit"></button></div><p class="action-selection-note" id="action-selection-note"></p>`;
    const dialog=document.createElement('dialog');dialog.className='symbol-dialog';dialog.setAttribute('aria-labelledby','symbol-title');
    dialog.innerHTML=`<form id="symbol-form"><header><h2 id="symbol-title"></h2><button type="button" id="symbol-close">×</button></header><div class="symbol-kinds"><button type="button" data-kind="letters"></button><button type="button" data-kind="numbers"></button></div><label for="symbol-input" id="symbol-input-label"></label><input id="symbol-input" autocomplete="off" spellcheck="false" maxlength="16" aria-describedby="symbol-help symbol-error"><p id="symbol-error" role="status"></p><div id="symbol-options" class="symbol-options"></div><p id="symbol-help"></p><footer><button type="button" id="symbol-cancel"></button><button type="submit" id="symbol-apply" class="primary"></button></footer></form>`;
    document.body.append(dialog);
    const $=id=>document.getElementById(id), select=$('studio-action'), input=$('symbol-input');
    const valid=()=>{const value=input.value.trim().toUpperCase();return value.length===1&&(kind==='letters'?letters:digits).includes(value)?value:null;};
    function draft(){
      const value=valid();$('symbol-apply').disabled=!value||busy;
      $('symbol-error').textContent=input.value.trim()&&!value?text(kind==='letters'?'请输入一个支持的字母（不含 J、Z）':'请输入一位数字 0–9',kind==='letters'?'Enter one supported letter (excluding J/Z).':'Enter one digit, 0–9.'):'\u00a0';
      for(const b of $('symbol-options').children)b.setAttribute('aria-pressed',String(b.textContent===value));
    }
    function dialogLabels(){
      $('symbol-title').textContent=text(kind==='letters'?'选择字母':'选择数字',kind==='letters'?'Choose a letter':'Choose a digit');
      $('symbol-input-label').textContent=text(kind==='letters'?'输入一个字母':'输入一位数字',kind==='letters'?'Enter one letter':'Enter one digit');
      input.placeholder=kind==='letters'?'A':'0';input.inputMode=kind==='letters'?'text':'numeric';
      $('symbol-close').setAttribute('aria-label',text('关闭选择窗','Close picker'));
      $('symbol-cancel').textContent=text('取消','Cancel');$('symbol-apply').textContent=text('使用所选内容','Apply selection');
      $('symbol-help').textContent=text(kind==='letters'?'24 个近似手指造型，不含动态字母 J、Z。选择后仍需点击播放。':'选择后仍需点击播放，不会自动启动机械手。',kind==='letters'?'24 approximate finger shapes; moving J/Z excluded. Press play separately.':'Selection does not start the hand. Press play separately.');
      for(const b of dialog.querySelectorAll('[data-kind]')){b.textContent=b.dataset.kind==='letters'?text('字母','Letters'):text('数字','Digits');b.setAttribute('aria-pressed',String(b.dataset.kind===kind));}
      const nodes=[...(kind==='letters'?letters:digits)].map(c=>{const b=document.createElement('button');b.type='button';b.textContent=c;b.addEventListener('click',()=>{input.value=c;draft();});return b;});
      $('symbol-options').replaceChildren(...nodes);draft();
    }
    function choose(id){
      if(busy||!catalog.some(x=>x.id===id))return;
      selected=id;if(catalog.find(x=>x.id===id)?.hardware!==false)lastHardware=id;render();onSelect(id);
    }
    function open(next){
      if(busy)return;
      kind=next;const prefix=kind==='letters'?'letter_':'digit_';input.value=selected.startsWith(prefix)?selected.slice(prefix.length):'';
      dialogLabels();dialog.showModal();input.focus();input.select();
    }
    function render(){
      const groups=[['basic',text('常用动作','Everyday')],['official',text('官方示例','Official examples')],['numbers',text('报数与报时','Counting and clock')],['letters',text('字母顺序展示','Alphabet sequence')]];
      if(allowPreview)groups.push(['preview',text('单指 / 关节（仅预览）','Finger / joint (preview only)')]);
      const opts=[];
      for(const [key,label] of groups){const group=document.createElement('optgroup');group.label=label;for(const item of catalog.filter(x=>x.group===key&&!/^(letter_|digit_)/.test(x.id)))group.append(new Option(text(item.zh,item.en),item.id));if(group.children.length)opts.push(group);}
      if(/^(letter_|digit_)/.test(selected)){
        const item=catalog.find(x=>x.id===selected);if(item){const g=document.createElement('optgroup');g.label=text('当前选择','Current selection');g.append(new Option(text(item.zh,item.en),item.id));opts.unshift(g);}
      }
      select.replaceChildren(...opts);select.value=selected;
      $('studio-action-label').textContent=text('展示动作','Selected action');
      $('choose-letter').textContent=text('字母…','Letter…');$('choose-digit').textContent=text('数字…','Digit…');
      const item=catalog.find(x=>x.id===selected);
      $('action-selection-note').textContent=!item?text('正在加载动作','Loading actions'):item.hardware===false?text('仅供画面预览；切回真实手时恢复上次实机动作。','Preview only; switching to Real hand restores its previous selection.'):item.group==='letters'?text('近似手指造型 · 尚未实机验收','Approximate finger shape · not hardware-validated'):item.id==='clock'?text('播放时读取本机 HH:MM，依次展示四位数字。','Captures local HH:MM at start and displays four digits.'):item.source==='official'?text('官方源码适配 · 按当前幅度和速度播放','Adapted official example · uses selected amplitude and speed'):text('项目编排动作 · 新选择在下次播放时生效','Scripted motion · changes apply on the next playback');
      [select,$('choose-letter'),$('choose-digit')].forEach(x=>x.disabled=busy||!catalog.length);
      if(dialog.open)dialogLabels();
    }
    select.addEventListener('change',()=>choose(select.value));
    $('choose-letter').addEventListener('click',()=>open('letters'));$('choose-digit').addEventListener('click',()=>open('numbers'));
    input.addEventListener('input',draft);
    for(const b of dialog.querySelectorAll('[data-kind]'))b.addEventListener('click',()=>{kind=b.dataset.kind;input.value='';dialogLabels();input.focus();});
    $('symbol-close').addEventListener('click',()=>dialog.close());$('symbol-cancel').addEventListener('click',()=>dialog.close());
    $('symbol-form').addEventListener('submit',e=>{e.preventDefault();const c=valid();if(c&&!busy){choose((kind==='letters'?'letter_':'digit_')+c);dialog.close();}});
    // HTML dialog supplies Escape dismissal, focus containment and focus return.
    render();
    return {setCatalog(data){catalog=data;render();},setPreview(value){allowPreview=value;if(!value&&catalog.find(x=>x.id===selected)?.hardware===false){selected=lastHardware;onSelect(selected);}render();},render,setBusy(value){busy=value;[select,$('choose-letter'),$('choose-digit')].forEach(x=>x.disabled=busy||!catalog.length);if(dialog.open)draft();},get selected(){return selected;}};
  }};
})();
