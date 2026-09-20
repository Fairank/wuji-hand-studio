/* Presentation only: returns an existing catalog id; never sends commands. */
(() => {
  'use strict';
  window.WujiActionPicker={create({root,text,onSelect}){
    let catalog=[],selected='official_opposition',lastHardware=selected,kind='letters',busy=false,allowPreview=false,phrase='WUJI TECH';
    const letters='ABCDEFGHIJKLMNOPQRSTUVWXYZ',digits='0123456789';
    root.className='compact-picker';
    root.innerHTML=`<label for="studio-action" id="studio-action-label"></label><div class="action-select-row"><select id="studio-action"></select><div class="symbol-shortcuts"><button type="button" id="choose-letter"></button><button type="button" id="choose-digit"></button></div></div><p class="action-selection-note" id="action-selection-note"></p><div id="phrase-strip" class="phrase-strip"></div><p id="performance-status" class="performance-status" role="status"></p><a id="performance-export" class="performance-export" hidden></a>`;
    const dialog=document.createElement('dialog');dialog.className='symbol-dialog';dialog.setAttribute('aria-labelledby','symbol-title');
    dialog.innerHTML=`<form id="symbol-form"><header><h2 id="symbol-title"></h2><button type="button" id="symbol-close">×</button></header><div class="symbol-kinds"><button type="button" data-kind="letters"></button><button type="button" data-kind="numbers"></button></div><label for="symbol-input" id="symbol-input-label"></label><input id="symbol-input" autocomplete="off" spellcheck="false" maxlength="16" aria-describedby="symbol-help symbol-error"><p id="symbol-error" role="status"></p><div id="symbol-options" class="symbol-options"></div><p id="symbol-help"></p><footer><button type="button" id="symbol-cancel"></button><button type="submit" id="symbol-apply" class="primary"></button></footer></form>`;
    document.body.append(dialog);
    const $=id=>document.getElementById(id), select=$('studio-action'), input=$('symbol-input');
    input.maxLength=144;
    const valid=()=>{if(kind==='letters'){if(!/^[A-Za-z0-9 \t\r\n]+$/.test(input.value))return null;const v=input.value.trim().replace(/[ \t\r\n]+/g,' ').toUpperCase();return v.length&&v.length<=48?v:null;}const v=input.value.trim();return v.length===1&&digits.includes(v)?v:null;};
    function draft(){
      const value=valid();$('symbol-apply').disabled=!value||busy;
      $('symbol-error').textContent=input.value.trim()&&!value?text(kind==='letters'?'请输入 A–Z、0–9 和空格，最多 48 个字符':'请输入一位数字 0–9',kind==='letters'?'Use A–Z, 0–9 and spaces; up to 48 characters.':'Enter one digit, 0–9.'):'\u00a0';
      for(const b of $('symbol-options').children)b.setAttribute('aria-pressed',String(b.textContent===value));
      if(kind==='numbers'){
        const item=catalog.find(x=>x.id==='digit_'+value);
        $('symbol-help').textContent=(item?text(item.description_zh||'',item.description_en||'')+'。 ':'')+text('采用中国大陆常用比数方式；各地有不同习惯。选择后点击播放。','Uses a common mainland Chinese convention; regional variants exist. Press play after selecting.');
      }
    }
    function dialogLabels(){
      $('symbol-title').textContent=text(kind==='letters'?'让手说一段话':'选择数字',kind==='letters'?'Say it with the hand':'Choose a digit');
      $('symbol-input-label').textContent=text(kind==='letters'?'输入字母或一句英文':'输入一位数字',kind==='letters'?'Enter letters or an English phrase':'Enter one digit');
      input.placeholder=kind==='letters'?'例如 wuji tech':'0';input.inputMode=kind==='letters'?'text':'numeric';
      $('symbol-close').setAttribute('aria-label',text('关闭选择窗','Close picker'));
      $('symbol-cancel').textContent=text('取消','Cancel');$('symbol-apply').textContent=text('使用所选内容','Apply selection');
      $('symbol-help').textContent=text(kind==='letters'?'参考美式手语 ASL 指拼的项目近似造型，不是 WUJI 官方动作或通用手语。24 个静态字母不含 J / Z；J / Z 为动态近似，固定底座不能完整表达手腕与掌心朝向。按输入顺序播放，空格处停顿。':'选择后仍需点击播放，不会自动启动机械手。',kind==='letters'?'Project approximations inspired by ASL fingerspelling, not official WUJI actions or universal signs. The 24 static letters exclude moving J/Z. A fixed base cannot fully reproduce wrist motion and palm orientation. Plays your text in order with word pauses.':'Selection does not start the hand. Press play separately.');
      for(const b of dialog.querySelectorAll('[data-kind]')){b.textContent=b.dataset.kind==='letters'?text('字母','Letters'):text('数字','Digits');b.setAttribute('aria-pressed',String(b.dataset.kind===kind));}
      const nodes=[...(kind==='letters'?letters:digits)].map(c=>{const b=document.createElement('button');b.type='button';b.textContent=c;b.addEventListener('click',()=>{input.value=kind==='letters'?input.value+c:c;input.focus();draft();});return b;});
      $('symbol-options').replaceChildren(...nodes);draft();
    }
    function choose(id){
      if(busy||!catalog.some(x=>x.id===id))return;
      selected=id;if(catalog.find(x=>x.id===id)?.hardware!==false)lastHardware=id;render();onSelect(id);
    }
    function open(next){
      if(busy)return;
      kind=next;const prefix=kind==='letters'?'letter_':'digit_';input.value=selected==='text_sequence'&&kind==='letters'?phrase:selected.startsWith(prefix)?selected.slice(prefix.length):kind==='letters'?'wuji tech':'';
      dialogLabels();dialog.showModal();input.focus();input.select();
    }
    function render(){
      const groups=[['dance',text('手指舞','Finger dance')],['letters',text('文字与字母','Text and letters')],['basic',text('常用动作','Everyday')],['official',text('官方示例','Official examples')],['numbers',text('报数与报时','Counting and clock')]];
      if(allowPreview)groups.push(['preview',text('单指 / 关节（仅预览）','Finger / joint (preview only)')]);
      const opts=[];
      for(const [key,label] of groups){const group=document.createElement('optgroup');group.label=label;for(const item of catalog.filter(x=>x.group===key&&!/^(letter_|digit_)/.test(x.id)))group.append(new Option(text(item.zh,item.en),item.id));if(group.children.length)opts.push(group);}
      if(/^(letter_|digit_)/.test(selected)){
        const item=catalog.find(x=>x.id===selected);if(item){const g=document.createElement('optgroup');g.label=text('当前选择','Current selection');g.append(new Option(text(item.zh,item.en),item.id));opts.unshift(g);}
      }
      select.replaceChildren(...opts);select.value=selected;
      $('studio-action-label').textContent=text('展示动作','Selected action');
      $('choose-letter').textContent=text('输入文字…','Enter text…');$('choose-digit').textContent=text('选择数字…','Choose digit…');
      const item=catalog.find(x=>x.id===selected);
      $('action-selection-note').textContent=!item?text('正在加载动作','Loading actions'):item.hardware===false?text('仅供画面预览；切回真实手时恢复上次实机动作。','Preview only; switching to Real hand restores its previous selection.'):item.group==='letters'?text('近似手指造型 · 尚未实机验收','Approximate finger shape · not hardware-validated'):item.id==='clock'?text('播放时读取本机 HH:MM，依次展示四位数字。','Captures local HH:MM at start and displays four digits.'):item.source==='official'?text('官方源码适配 · 按当前幅度和速度播放','Adapted official example · uses selected amplitude and speed'):text('项目编排动作 · 新选择在下次播放时生效','Scripted motion · changes apply on the next playback');
      [select,$('choose-letter'),$('choose-digit')].forEach(x=>x.disabled=busy||!catalog.length);
      if(selected==='text_sequence')$('action-selection-note').textContent=text('依次展示：','Sequence: ')+phrase;
      if(item?.group==='dance')$('action-selection-note').textContent=text('真人教程启发 · 连续关节曲线 · 固定底座改编','Inspired by human tutorials · continuous curves · fixed-base adaptation');
      if(item?.id.startsWith('digit_'))$('action-selection-note').textContent=text(item.description_zh,item.description_en);
      const exportable=selected==='text_sequence'||selected==='letter_J'||selected==='letter_Z'||item?.group==='dance';
      const link=$('performance-export');link.hidden=!exportable;link.textContent=text('下载关节轨迹 · 1000 Hz CSV','Download joint trajectory · 1000 Hz CSV');
      link.href='/api/performance?action='+encodeURIComponent(selected)+'&text='+encodeURIComponent(phrase)+'&format=csv';
      $('phrase-strip').replaceChildren(...(selected==='text_sequence'?[...phrase].map(c=>{const s=document.createElement('span');s.textContent=c===' '?'·':c;s.dataset.space=String(c===' ');return s;}):[]));
      if(dialog.open)dialogLabels();
    }
    select.addEventListener('change',()=>choose(select.value));
    $('choose-letter').addEventListener('click',()=>open('letters'));$('choose-digit').addEventListener('click',()=>open('numbers'));
    input.addEventListener('input',draft);
    for(const b of dialog.querySelectorAll('[data-kind]'))b.addEventListener('click',()=>{kind=b.dataset.kind;input.value='';dialogLabels();input.focus();});
    $('symbol-close').addEventListener('click',()=>dialog.close());$('symbol-cancel').addEventListener('click',()=>dialog.close());
    $('symbol-form').addEventListener('submit',e=>{e.preventDefault();const c=valid();if(c&&!busy){if(kind==='letters'){phrase=c;choose('text_sequence');}else choose('digit_'+c);dialog.close();}});
    // HTML dialog supplies Escape dismissal, focus containment and focus return.
    render();
    return {show(value){if(value==='closed')dialog.close();else open(value);return {ok:true,kind:value};},restore(id,value){if(catalog.some(x=>x.id===id)){selected=id;if(typeof value==="string"&&value)phrase=value;render();}},setCatalog(data){catalog=data;render();},setPreview(value){allowPreview=value;if(!value&&catalog.find(x=>x.id===selected)?.hardware===false){selected=lastHardware;onSelect(selected);}render();},render,setBusy(value){busy=value;[select,$('choose-letter'),$('choose-digit')].forEach(x=>x.disabled=busy||!catalog.length);if(dialog.open)draft();},update(state){const p=state.playback,h=state.hardware;const active=p?.active&&p.action===selected;const idx=h?.active&&h.action===selected&&h.text===phrase?h.token_index:active&&p.text===phrase?p.token_index:-1;[...$('phrase-strip').children].forEach((e,i)=>e.setAttribute('aria-current',String(i===idx)));$('performance-status').textContent=h?.active?h.trial_phase||'':active?p.label+' · '+text('预览','Preview')+' '+Math.round(100*((!p.running&&p.cycles&&p.elapsed_s>=p.duration_s*p.cycles)?1:(p.elapsed_s%p.duration_s)/p.duration_s))+'%':'';},get payload(){return selected==='text_sequence'?{text:phrase}:{};},get selected(){return selected;}};
  }};
})();
