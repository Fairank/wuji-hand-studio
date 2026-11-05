/* One demonstration entry; route changes reuse the existing live workspaces. */
(() => {
  'use strict';

  function init() {
    const workspace = window.WujiWorkspace;
    const directory = document.querySelector('.directory');
    const singlePage = document.getElementById('page-library');
    const singleLink = directory?.querySelector('[data-page="library"]');
    if (!workspace || !singlePage || !singleLink || document.getElementById('demonstration-navigation')) return;

    const multiPage = document.getElementById('page-devices');
    const multiLink = directory.querySelector('[data-page="devices"]');
    const group = document.createElement('div');
    group.id = 'demonstration-navigation';
    group.className = 'hub-tabs demonstration-navigation';
    group.setAttribute('role', 'tablist');
    group.setAttribute('aria-orientation', 'horizontal');
    const options = [
      { route: 'library', id: 'demonstration-single', zh: '单手', en: 'Single hand', page: singlePage },
      { route: 'devices', id: 'demonstration-multi', zh: '多手', en: 'Multiple hands', page: multiPage },
    ];
    let pendingFocus = null;
    const text = (zh, en) => window.WujiLocale?.lang === 'en' ? en : zh;
    const setText = (element, value) => {
      if (element && element.textContent !== value) element.textContent = value;
    };
    const sources = document.createElement('section');sources.className='demonstration-sources';
    const modelSlot=document.createElement('div');modelSlot.className='demonstration-model';
    const handField=document.createElement('div');handField.className='demonstration-hand';
    const handLabel=document.createElement('label'),handSelect=document.createElement('select'),connect=document.createElement('button'),status=document.createElement('p');
    handSelect.id='demonstration-hand';handLabel.htmlFor=handSelect.id;connect.id='demonstration-connect';connect.type='button';status.id='demonstration-connection-status';status.setAttribute('role','status');status.hidden=true;
    const retryCatalog=document.createElement('button');retryCatalog.id='demonstration-catalog-retry';retryCatalog.type='button';retryCatalog.hidden=true;retryCatalog.onclick=()=>window.WujiDemonstrationCatalog?.retry();
    handField.append(handLabel,handSelect);sources.append(modelSlot,handField,connect,status);
    sources.append(retryCatalog);
    let consoleState=null,online=false,connecting=null,wantedHand=null,sourceEpoch=0,sourceError='',sourceOptions='';
    const serial=row=>row?.serial||row?.sn||'',desired=()=>window.WujiDemonstration?.profile()||'hand2_left';
    const supported=row=>row?.kind==='hand'&&['hand1','hand2'].includes(row.generation)&&(row.side==null||['left','right'].includes(row.side));
    const available=row=>supported(row)&&row.selectable===true&&row.stale===false&&row.conflict!==true;
    const verified=(value,sn)=>{const identity=value?.identified_device||{},owner=value?.connection_switch?.current_owner;return value?.connection==='connected'&&value.stale===false&&value.device_id===sn&&identity.source==='sdk_identity'&&identity.serial===sn&&/^hand[12]_(left|right)$/.test(value.device_profile?.id||'')&&identity.profile===value.device_profile.id&&owner?.mode==='hand'&&owner.kind==='hand'&&owner.serial===sn&&Number.isSafeInteger(owner.session)&&owner.session>0&&owner.session===value.session_generation;};
    const matching=value=>verified(value,handSelect.value)&&value.device_profile.id===desired();
    if(window.WujiDemonstration){window.WujiDemonstration.selectedHand=()=>handSelect.value;window.WujiDemonstration.connectionMatches=matching;}
    function renderSources(){
      const inventory=window.WujiDeviceInventory?.snapshot(),rows=(Array.isArray(inventory?.devices)?inventory.devices:[]).filter(row=>row.kind==='hand'&&serial(row));
      const active=consoleState?.connection==='connected'&&consoleState.device_profile?.id===desired()?consoleState.device_id:'';
      const selected=wantedHand!==null?wantedHand:active||handSelect.value;
      const options=[new Option(text('选择灵巧手','Choose a hand'),'')];
      for(const row of rows){const option=new Option(serial(row)+(supported(row)&&row.side==null?text(' · 连接后识别左右手',' · Side identified on connection'):''),serial(row));option.disabled=!available(row);options.push(option);}
      if(active&&!rows.some(row=>serial(row)===active))options.push(new Option(active+text(' · 当前连接',' · Connected'),active));
      const key=JSON.stringify(options.map(option=>[option.text,option.value,option.disabled]));if(key!==sourceOptions){sourceOptions=key;handSelect.replaceChildren(...options);}const choice=options.some(option=>option.value===selected)?selected:'';if(handSelect.value!==choice)handSelect.value=choice;
      const row=rows.find(row=>serial(row)===handSelect.value),current=online&&matching(consoleState);
      const sharedPending=window.WujiDeviceUse?.snapshot?.().pending===true||window.WujiConnectionSwitch?.busy?.()===true;
      const canConnect=online&&!connecting&&!sharedPending&&!current&&available(row)&&consoleState?.connection_switch?.can_switch===true&&consoleState.connection_switch.busy!==true;
      handSelect.disabled=connecting;connect.disabled=!canConnect;connect.textContent=connecting?text('正在连接…','Connecting…'):current?text('已连接','Connected'):text('连接灵巧手','Connect hand');
      handLabel.textContent=text('实机目标','Physical hand');connect.title=canConnect||current?'':consoleState?.connection_switch?.blocked_reason||text('选择本次已发现的可用灵巧手。','Choose an available hand discovered now.');
      const catalog=window.WujiDemonstrationCatalog?.snapshot();retryCatalog.textContent=text('重新载入动作','Reload actions');retryCatalog.hidden=!catalog?.error;retryCatalog.disabled=!!catalog?.loading;
      status.textContent=sourceError||catalog?.error||'';status.hidden=!status.textContent;
    }
    handSelect.onchange=()=>{wantedHand=handSelect.value;sourceEpoch++;connecting=null;sourceError='';renderSources();window.dispatchEvent(new Event('demonstration-target'));};
    connect.onclick=async()=>{
      if(connect.disabled)return;const selected=handSelect.value,model=desired(),own=++sourceEpoch,ticket={token:consoleState?.csrf,owner:consoleState?.connection_switch?.expected_owner};connecting=ticket;sourceError='';renderSources();
      const current=()=>connecting===ticket&&online&&own===sourceEpoch&&consoleState?.csrf===ticket.token&&desired()===model&&handSelect.value===selected;
      try{
        await window.WujiDeviceInventory?.refresh();
        const row=window.WujiDeviceInventory?.snapshot()?.devices?.find(row=>serial(row)===selected);
        if(!current())return;
        if(consoleState?.connection_switch?.expected_owner!==ticket.owner)throw Error(text('当前设备连接已变化，请重新选择后连接。','The device connection changed. Select it again before connecting.'));
        if(!available(row))throw Error(text('设备已变化，请重新选择。','The device changed. Select it again.'));
        if(!window.WujiDeviceUse?.open)throw Error(text('连接入口尚未就绪，请稍后重试。','Connection switching is not ready. Retry shortly.'));
        const result=await window.WujiDeviceUse.open('hand',{...row,profile:model},{navigate:false,expectedContext:ticket.owner});
        if(!current()||result?.routed)return;
        if(!result)throw Error(text('设备用途切换正在进行，请等待当前操作完成。','A device switch is in progress. Wait for it to finish.'));
        const incoming=result?.state,owner=consoleState?.connection_switch?.expected_owner;
        if(incoming?.csrf!==ticket.token||!verified(incoming,selected)||owner&&owner!==ticket.owner&&owner!==incoming.connection_switch?.expected_owner)throw Error(text('实际灵巧手身份或连接归属尚未核对，未允许开始动作。','Actual hand identity or connection ownership is unverified. Starting is blocked.'));
        consoleState=incoming;connecting=null;window.WujiDemonstration.selectProfile(incoming.device_profile.id);wantedHand=selected;handSelect.value=selected;renderSources();window.dispatchEvent(new Event('demonstration-target'));
      }catch(error){if(current())sourceError=error?.message||text('连接未完成','Connection incomplete');}
      finally{if(connecting===ticket){connecting=null;renderSources();window.dispatchEvent(new Event('demonstration-target'));}}
    };
    window.addEventListener('console-state',event=>{if(consoleState?.csrf&&event.detail?.csrf!==consoleState.csrf){sourceEpoch++;connecting=null;sourceError='';}consoleState=event.detail;online=true;renderSources();});
    window.addEventListener('console-offline',()=>{sourceEpoch++;connecting=null;consoleState=null;online=false;renderSources();});
    window.addEventListener('device-inventory-update',renderSources);
    window.addEventListener('device-use-update',renderSources);
    window.addEventListener('demonstration-catalog',renderSources);
    const refreshSources=()=>window.WujiDeviceInventory?.refresh()?.catch(()=>{});
    window.addEventListener('workspace-page',event=>{if(event.detail==='library')refreshSources();});
    setInterval(()=>{if(document.body.dataset.page==='library'&&!document.hidden)refreshSources();},5000);
    window.addEventListener('demonstration-profile',()=>{sourceEpoch++;connecting=null;wantedHand=null;handSelect.value='';sourceError='';renderSources();window.dispatchEvent(new Event('demonstration-target'));});
    const activeRoute = () => document.body.dataset.page || location.hash.slice(1);

    function select(option, focus) {
      if (!option.page) return;
      if (activeRoute() === option.route) {
        if (focus) option.button.focus({ preventScroll: true });
        return;
      }
      pendingFocus = focus ? option.button : null;
      window.WujiNavigationMotion?.mount().prepareMove(group);
      // The existing hash router owns viewer placement and page lifecycle.
      // A choice never connects a device or starts/stops an action.
      location.hash = option.route;
    }

    for (const [index, option] of options.entries()) {
      const button = document.createElement('button');
      option.button = button;
      button.type = 'button';
      button.id = option.id;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', option.page?.id || 'page-devices');
      button.disabled = !option.page;
      button.addEventListener('click', () => select(option, document.activeElement === button));
      button.addEventListener('keydown', event => {
        const available = options.filter(item => item.page);
        const position = available.indexOf(option);
        let next;
        if (event.key === 'ArrowRight') next = available[(position + 1) % available.length];
        else if (event.key === 'ArrowLeft') next = available[(position + available.length - 1) % available.length];
        else if (event.key === 'Home') next = available[0];
        else if (event.key === 'End') next = available[available.length - 1];
        if (!next) return;
        event.preventDefault();
        select(next, true);
      });
      button.tabIndex = index === 0 ? 0 : -1;
      group.append(button);
    }

    // Keep the legacy link in the DOM: the original router still addresses it.
    if (multiLink) {
      multiLink.dataset.demonstrationHidden = 'true';
      multiLink.hidden = true;
      multiLink.setAttribute('aria-hidden', 'true');
      multiLink.tabIndex = -1;
    }

    function labels() {
      const title = text('灵巧手展示', 'Hand demonstrations');
      group.setAttribute('aria-label', title);
      const linkLabel = singleLink.querySelector('span');
      linkLabel?.removeAttribute('data-i18n');
      setText(linkLabel, title);
      for (const option of options) {
        setText(option.button, text(option.zh, option.en));
        const heading = option.page?.querySelector(':scope > .page-heading h2');
        heading?.removeAttribute('data-i18n');
        setText(heading, title);
      }
      renderSources();
      const multiHeading = multiPage?.querySelector('#wsx-title');
      // This title is now owned by the shared navigation, not the old page label.
      multiHeading?.removeAttribute('data-wsx-t');
      multiHeading?.removeAttribute('data-i18n');
      setText(multiHeading, title);
      if (['library', 'devices'].includes(activeRoute())) document.title = title + ' · Hand Workbench';
    }

    function sync() {
      const route = activeRoute();
      const selected = route === 'devices' ? 'devices' : 'library';
      const heading = route === 'devices'
        ? multiPage?.querySelector('.wsx-head') || multiPage?.querySelector(':scope > .page-heading')
        : route === 'library' ? singlePage.querySelector(':scope > .page-heading') : null;
      const moving = heading && heading.nextElementSibling !== group;
      if (moving) window.WujiNavigationMotion?.mount().prepareMove(group);
      for (const option of options) {
        const active = option.route === selected;
        option.button.setAttribute('aria-selected', String(active));
        option.button.tabIndex = active ? 0 : -1;
      }
      if (route === 'library' || route === 'devices') {
        // Move only this one group. Existing headers, controls, frames and state
        // remain untouched, including the page-specific header actions.
        if (moving) heading.after(group);
        window.WujiNavigationMotion?.mount().finishMove(group);
        singleLink.setAttribute('aria-current', 'page');
        multiLink?.removeAttribute('aria-current');
      }
      labels();
      if (pendingFocus && !group.hidden) {
        const button = pendingFocus;
        pendingFocus = null;
        button.focus({ preventScroll: true });
      }
    }

    window.addEventListener('workspace-page', sync);
    window.addEventListener('wuji-language', sync);
    window.addEventListener('experience-mode', sync);
    // Mount while another page is active too, so its DOM identity is stable.
    singlePage.querySelector(':scope > .page-heading')?.after(group);
    group.after(sources);window.WujiDemonstration?.mount(modelSlot);renderSources();refreshSources();
    sync();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
