/* workspaces.js — multi-hand workspace manager for #page-devices (vanilla JS, no dependencies). */
(() => {
  'use strict';
  if (new URLSearchParams(location.search).get('embedded') === '1') return;

  const STORE = 'wsx.workspace', TIMEOUT_MS = 8000;
  const PROFILES = [['hand2_left', 2, 'left'], ['hand2_right', 2, 'right'], ['hand1_left', 1, 'left'], ['hand1_right', 1, 'right']];
  const S = {
    title: ['多手工作区', 'Multi-hand workspaces'], hands: ['多手', 'Hands'], workspace: ['工作区', 'Workspace'], group: ['组控制', 'Group control'],
    stopAll: ['停止全部手','Stop all hands'], removeWs: ['删除空工作区', 'Remove empty workspace'], members: ['工作区内的手', 'Hands in workspace'], create: ['新建', 'Create'],
    emptyWs: ['此工作区还没有手', 'No hands in this workspace yet'], previewModel: ['预览型号', 'Preview model'],
    addPreview: ['添加预览手', 'Add preview hand'], noPanel: ['组控制面板未加载', 'Group panel not loaded'], manage: ['新建与发现', 'Create & discover'],
    newName: ['新工作区名称', 'New workspace name'], namePh: ['如：双手工位', 'e.g. Bench pair'], scan: ['发现设备', 'Discover'],
    address: ['自定义地址（可选）', 'Custom address (optional)'], addrPh: ['留空为默认扫描', 'Blank = default scan'],
    scanning: ['正在扫描…', 'Scanning…'], updated: ['更新于', 'Updated'], devices: ['台设备', 'device(s)'], target: ['目标工作区', 'Target workspace'],
    addSel: ['加入所选工作区', 'Add to selected workspace'], newConn: ['新工作区并连接', 'New workspace and connect'],
    already: ['已在工作区：', 'Already in: '], settings: ['单手设置', 'Hand settings'], disconnect: ['断开', 'Disconnect'], forget: ['移除', 'Remove'],
    moveTo: ['移至工作区', 'Move to'], move: ['移动', 'Move'], online: ['在线', 'Online'], offline: ['离线', 'Offline'], busy: ['忙碌', 'Busy'],
    unknown: ['状态未知', 'Status unknown'], inRun: ['组运行中', 'In group run'], noSerial: ['无设备序列号', 'No device serial'],
    running: ['运行中', 'Running'], idle: ['空闲', 'Idle'], starting: ['正在启动…', 'Starting…'], loading: ['加载中…', 'Loading…'],
    left: ['左手', 'Left'], right: ['右手', 'Right'], autoLabel: ['工作区', 'Workspace'], created: ['已创建工作区', 'Workspace created'],
    addReq: ['已提交加入（只读连接），实际状态以卡片为准', 'Add submitted (read-only connect); the card shows actual status'],
    createdNoAdd: ['工作区已创建，但加入设备失败：', 'Workspace created, but adding the device failed: '],
    scanReq: ['已请求扫描', 'Scan requested'], moveReq: ['已提交移动', 'Move submitted'], discReq: ['已提交断开', 'Disconnect submitted'],
    forgetReq: ['已提交移除', 'Remove submitted'], rmReq: ['已提交删除工作区', 'Workspace removal submitted'], failed: ['失败：', 'Failed: '],
    prevReq: ['已添加预览手（仅预览，未连接设备）', 'Preview hand added (preview only, no device)'],
    startReq: ['已提交启动', 'Start submitted'], stopReq: ['已提交停止', 'Stop submitted'], needName: ['请输入工作区名称', 'Enter a workspace name'],
    gone: ['设备不在最新发现结果中，请重新发现', 'Device not in latest discovery; discover again'], badSnap: ['快照格式无效', 'Invalid snapshot'],
    pollFail: ['状态刷新失败：', 'Refresh failed: '], beatFail: ['编排连接中断：', 'Group connection interrupted: '], noLease: ['启动未取得控制连接', 'Start did not establish a control connection'],
    timeout: ['请求超时（8 秒）', 'Request timed out (8 s)'], badCmd: ['未知组命令', 'Unknown group command'],
    noSessions: ['单手会话不可用', 'Hand sessions unavailable'], noId: ['未返回工作区 ID', 'No workspace id returned'],
  };
  const SKELETON = `<header class="wsx-head"><h2 id="wsx-title" class="wsx-title" data-wsx-t="title"></h2>
  <label class="wsx-field wsx-pick"><span data-wsx-t="workspace"></span><select class="wsx-ws"></select></label><span class="wsx-run"></span>
  <button type="button" class="wsx-btn wsx-stopall" data-wsx-t="stopAll"></button><button type="button" class="wsx-btn wsx-danger wsx-rmws" data-wsx-t="removeWs" hidden></button></header>
<div class="wsx-msgs"><p class="wsx-status" role="status"></p><p class="wsx-err" aria-live="polite"></p></div>
<div class="wsx-grid"><section class="wsx-card"><h3 data-wsx-t="members"></h3><p class="wsx-note wsx-empty" data-wsx-t="emptyWs" hidden></p>
  <div class="wsx-list wsx-members"></div><div class="wsx-row"><label class="wsx-field"><span data-wsx-t="previewModel"></span>
  <select class="wsx-prof"></select></label><button type="button" class="wsx-btn wsx-addprev" data-wsx-t="addPreview"></button></div></section>
<section class="wsx-card wsx-group-card"><h3 data-wsx-t="group"></h3><div class="wsx-views"></div><p class="wsx-note wsx-nopanel" data-wsx-t="noPanel"></p><div class="wsx-panel"></div></section>
<section class="wsx-card"><h3 data-wsx-t="manage"></h3>
  <form class="wsx-row wsx-create"><label class="wsx-field"><span data-wsx-t="newName"></span><input name="label" maxlength="40"
  autocomplete="off" data-wsx-tp="namePh"></label><button class="wsx-btn wsx-primary" data-wsx-t="create"></button></form>
  <form class="wsx-row wsx-scan"><label class="wsx-field"><span data-wsx-t="address"></span><input name="address" autocomplete="off"
  spellcheck="false" data-wsx-tp="addrPh"></label><button class="wsx-btn" data-wsx-t="scan"></button></form>
  <p class="wsx-note wsx-dstate"></p><div class="wsx-list wsx-disc"></div></section></div>`;
  const MEMBER = `<div class="wsx-hc-top"><strong class="wsx-name"></strong><span class="wsx-badge"></span></div>
<div class="wsx-meta"></div><div class="wsx-msg"></div><div class="wsx-row"><button type="button" class="wsx-btn" data-wsx="settings"></button>
<button type="button" class="wsx-btn" data-wsx="disc"></button><button type="button" class="wsx-btn wsx-danger" data-wsx="forget"></button></div>
<div class="wsx-row"><label class="wsx-field"><span class="wsx-lbl"></span><select class="wsx-sel"></select></label>
<button type="button" class="wsx-btn" data-wsx="move"></button></div>`;
  const DISC = `<div class="wsx-hc-top"><strong class="wsx-name"></strong><span class="wsx-meta"></span></div><div class="wsx-msg"></div>
<div class="wsx-row"><label class="wsx-field"><span class="wsx-lbl"></span><select class="wsx-sel"></select></label></div>
<div class="wsx-row"><button type="button" class="wsx-btn wsx-primary" data-wsx="add"></button>
<button type="button" class="wsx-btn" data-wsx="new"></button></div>`;

  let root = null, topBtn = null, panel = null, viewer = null, panelKey = '', snap = null, csrf = null, forced = null;
  let sel = 'main', selAt = 0, polling = false, again = false, pollErr = '';
  const leases = new Map(), leaseAt = new Map(), beatErr = new Map(), beating = new Set();
  const starting = new Set(), stopAsked = new Set(), inflight = new Set();
  try { sel = localStorage.getItem(STORE) || 'main'; } catch (_) { /* storage blocked */ }

  const lang = () => forced || (window.WujiLocale && window.WujiLocale.lang === 'en' ? 'en' : 'zh');
  const T = k => (S[k] || [k, k])[lang() === 'en' ? 1 : 0];
  const $ = s => root.querySelector(s);
  const tx = (el, s) => { if (el && el.textContent !== s) el.textContent = s; };
  const errMsg = x => (x && x.message) || String(x);
  const model = (generation, side) => { const g = String(generation).replace('hand',''); return [g == null || g === '' ? '' : lang() === 'en' ? `Gen ${g}` : `${'一二三四五'[g - 1] || g}代`,
    side === 'left' || side === 'right' ? T(side) : ''].filter(Boolean).join(' · '); };
  const phaseLabel = (v,mode) => ({preparing:['准备中','Preparing'],arming:mode==='preview'?['准备预览','Preparing preview']:['使能中','Arming'],waiting:['等待开始','Waiting'],playing:['播放中','Playing'],returning:['返回中','Returning'],completed:['已完成','Completed'],stopped:['已停止','Stopped'],failed:['未完成','Failed']})[v]?.[lang()==='en'?1:0] || v || '';
  const clock =


 u => { const d = new Date(typeof u === 'number' && u < 1e12 ? u * 1000 : u); return isNaN(d) ? '' : d.toLocaleTimeString(); };
  const wss = () => (snap && Array.isArray(snap.workspaces) ? snap.workspaces : []);
  const eps = () => (snap && Array.isArray(snap.endpoints) ? snap.endpoints : []);
  const cur = () => { const l = wss(); return l.find(w => String(w.id) === sel) || l.find(w => w.id === 'main') || l[0] || null; };
  const wsId = r => { const w = r && r.workspace; return w && typeof w === 'object' ? w.id : w; };
  const fresh = k => {                                                  // adds only from the latest discovery snapshot
    const d = ((snap && snap.discovery && snap.discovery.devices) || []).find(x => x && String(x.serial) === k);
    if (!d) throw new Error(T('gone'));
    return d;
  };

  async function req(url, opt = {}) {                                   // 8 s timeout on every request
    const ac = new AbortController(), timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
    try {
      const r = await fetch(url, { cache: 'no-store', credentials: 'same-origin', ...opt, signal: ac.signal });
      const text = await r.text();
      let j = null;
      try { j = text ? JSON.parse(text) : null; } catch (_) { j = null; }
      if (!r.ok || (j && j.error)) throw Object.assign(new Error(j && j.error ? errMsg(j.error) : `HTTP ${r.status}`), { status: r.status });
      return j || {};
    } catch (e) {
      throw e && e.name === 'AbortError' ? new Error(T('timeout')) : e;
    } finally { clearTimeout(timer); }
  }
  async function act(command, args = {}) {                              // single /api/action envelope; never auto-resent
    if (!csrf) csrf = (await req('/api/state')).csrf || null;
    if (!csrf) throw new Error('CSRF token unavailable');
    try {
      return await req('/api/action', { method: 'POST', body: JSON.stringify({ ...args, name: command }),
        headers: { 'Content-Type': 'application/json', 'X-Console-Token': csrf } });
    } catch (e) { if (e.status === 401 || e.status === 403) csrf = null; throw e; }
  }
  function say(msg, bad) {
    if (!root) return;
    const el = $('.wsx-status'); tx(el, msg); el.classList.toggle('wsx-bad', !!bad);
  }
  async function op(key, fn, okKey) {                                   // one in-flight request per button key
    if (inflight.has(key)) return;
    inflight.add(key); render();
    try { await fn(); if (okKey) say(T(okKey)); } catch (e) { say(T('failed') + errMsg(e), true); }
    finally { inflight.delete(key); render(); kick(); }
  }
  async function poll(force) {
    if (polling) { if (force) again = true; return; }                   // never overlap snapshot requests
    if (!force && document.hidden && !leases.size) return;              // paused while hidden unless a lease is held
    polling = true;
    const t0 = performance.now();
    try {
      const s = await req('/api/workspaces');
      if (!Array.isArray(s.workspaces)) throw new Error(T('badSnap'));
      snap = s; pollErr = '';
      for (const ws of [...leases.keys()]) {                            // drop leases whose run ended, unless start pending
        const w = wss().find(x => x.id === ws);
        if (!(w && w.run && w.run.active) && !starting.has(ws) && (leaseAt.get(ws) || 0) < t0) {
          leases.delete(ws); leaseAt.delete(ws); beatErr.delete(ws);
        }
      }
      if (t0 > selAt && !wss().some(w => String(w.id) === sel) && cur()) sel = String(cur().id);
    } catch (e) { pollErr = T('pollFail') + errMsg(e); }
    finally { polling = false; render(); if (again) { again = false; poll(true); } }
  }
  const kick = () => poll(true);
  function beat() {                                                     // 250 ms tick; one in-flight beat per workspace
    for (const [ws, lease] of leases) {
      if (beating.has(ws)) continue;
      beating.add(ws);
      act('ensemble_beat', { workspace: ws, lease })
        .then(() => beatErr.delete(ws), e => { if (leases.get(ws) === lease) beatErr.set(ws, T('beatFail') + errMsg(e)); })
        .finally(() => { beating.delete(ws); renderErr(); });
    }
  }
  function stopAll() {                                                  // pagehide: best-effort stop, fire and forget
    for (const ws of leases.keys()) {
      try {
        fetch('/api/action', { method: 'POST', keepalive: true, credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-Console-Token': csrf || '' },
          body: JSON.stringify({ workspace: ws, name: 'ensemble_stop' }) }).catch(() => {});
      } catch (_) { /* ignore */ }
    }
    leases.clear(); leaseAt.clear(); beatErr.clear();
  }
  async function onCommand(a, b) {                                      // the only path that can start a group run
    const env = a && typeof a === 'object' ? a : null;
    const name = String(env ? env.command || env.type || env.name || '' : a || '');
    const raw = env ? env.payload || env : b;
    const args = Object.assign({}, raw && typeof raw === 'object' ? raw : null);
    if (raw === env) ['command', 'type', 'name', 'payload'].forEach(k => delete args[k]);
    const w = cur(), ws = w ? w.id : null;                              // capture target workspace before any await
    if (ws == null) throw new Error(T('loading'));
    if (/stop/i.test(name)) {
      leases.delete(ws); leaseAt.delete(ws); beatErr.delete(ws);
      if (starting.has(ws)) stopAsked.add(ws);
      try { const r = await act('ensemble_stop', { workspace: ws }); say(T('stopReq')); return r; }
      catch (e) { say(T('failed') + errMsg(e), true); throw e; } finally { renderErr(); kick(); }
    }
    if (!/start/i.test(name)) throw new Error(T('badCmd'));
    if (starting.has(ws)) throw new Error(T('starting'));
    starting.add(ws); stopAsked.delete(ws); render();
    try {
      const r = await act('ensemble_start', { ...args, workspace: ws });
      const g = r.group_lease, lease = g && typeof g === 'object' ? g.id : g;
      if (lease == null || lease === '') throw new Error(T('noLease'));
      if (!stopAsked.has(ws)) { leases.set(ws, String(lease)); leaseAt.set(ws, performance.now()); beat(); }
      else await act('ensemble_stop', { workspace: ws });
      say(T('startReq')); return r;
    } catch (e) { say(T('failed') + errMsg(e), true); throw e; }
    finally { starting.delete(ws); stopAsked.delete(ws); render(); kick(); }
  }
  function syncSel(s, opts, want) {                                     // keeps value; never touches a focused select
    if (document.activeElement === s) return false;
    const sig = JSON.stringify(opts), val = want !== undefined ? want : s.value;
    if (s._wsx !== sig) { s._wsx = sig; s.replaceChildren(...opts.map(([v, t]) => new Option(t, v))); }
    s.value = val;
    if (s.selectedIndex < 0 && s.options.length) s.selectedIndex = 0;
    return true;
  }
  function keyed(box, items, html, upd) {                              // stable keyed cards; DOM touched only on change
    const m = box._wsx || (box._wsx = new Map()), keep = new Set(items.map(i => i.k)), done = new Set();
    for (const [k, r] of m) if (!keep.has(k)) { r.el.remove(); m.delete(k); }
    let prev = null;
    for (const { k, v } of items) {
      if (done.has(k)) continue;
      done.add(k);
      let r = m.get(k);
      if (!r) {
        const el = document.createElement('article');
        el.className = 'wsx-hc'; el.dataset.k = k; el.innerHTML = html;
        m.set(k, (r = { el, sig: '' }));
      }
      const sig = JSON.stringify(v);
      if (sig !== r.sig) r.sig = upd(r.el, v) === false ? '' : sig;
      const want = prev ? prev.nextSibling : box.firstChild;
      if (r.el !== want) box.insertBefore(r.el, want);
      prev = r.el;
    }
  }
  function updMember(el, v) {
    const q = s => el.querySelector(s), badge = q('.wsx-badge'), pick = q('.wsx-sel');
    el.setAttribute('aria-label', v.label || v.id);
    tx(q('.wsx-name'), v.label || v.id);
    tx(badge, T(v.stale ? 'unknown' : v.on ? 'online' : 'offline'));
    badge.classList.toggle('wsx-on', v.on && !v.stale);
    tx(q('.wsx-meta'), [v.serial || T('noSerial'), model(v.g, v.side)].filter(Boolean).join(' · '));
    tx(q('.wsx-msg'), [v.inRun ? [T('inRun'), v.phase].filter(Boolean).join(' · ') : '', v.busy ? T('busy') : '', v.conn, v.msg]
      .filter(Boolean).join(' · '));
    for (const [a, key, off, hide] of [['settings', 'settings'], ['disc', 'disconnect', !v.on && !v.busy],
      ['forget', 'forget', false, v.id === 'main'], ['move', 'move', !v.others.length]]) {
      const b = q(`[data-wsx="${a}"]`);
      tx(b, T(key)); b.disabled = !!off || v.fl.includes(a); b.hidden = !!hide;
    }
    tx(q('.wsx-lbl'), T('moveTo'));
    pick.disabled = !v.others.length;
    return syncSel(pick, v.others);
  }
  function updDisc(el, v) {
    const q = s => el.querySelector(s), pick = q('.wsx-sel');
    el.setAttribute('aria-label', v.s);
    tx(q('.wsx-name'), v.s);
    tx(q('.wsx-meta'), [model(v.g, v.side), v.addr].filter(Boolean).join(' · '));
    tx(q('.wsx-msg'), v.own ? T('already') + v.own : '');
    tx(q('.wsx-lbl'), T('target'));
    for (const [a, key] of [['add', 'addSel'], ['new', 'newConn']]) {
      const b = q(`[data-wsx="${a}"]`);
      tx(b, T(key)); b.disabled = v.fl.length > 0 || (a === 'add' && !v.opts.length);
    }
    pick.disabled = !v.opts.length;
    return syncSel(pick, v.opts, pick._touched ? undefined : v.cur);
  }


  function render() {
    if (!root) return;
    const L = lang(), list = wss(), w = cur(), run = (w && w.run) || {}, stale = !!pollErr;
    const wsSel = $('.wsx-ws'), rb = $('.wsx-run'), rm = $('.wsx-rmws');
    const name = x => (x.label ? String(x.label) : String(x.id)), opts = list.map(x => [String(x.id), name(x)]);
    syncSel(wsSel, list.map(x => [String(x.id), `${name(x)} (${(x.members || []).length})`]), w ? String(w.id) : undefined);
    wsSel.disabled = !list.length || starting.size > 0;                 // no workspace switching while a start is pending
    tx(rb, !w || stale ? T(stale ? 'unknown' : 'loading') : starting.has(w.id) ? T('starting') : run.active
      ? [T('running'), phaseLabel(run.phase,run.mode), typeof run.elapsed_s === 'number' ? `${Math.round(run.elapsed_s)} s` : ''].filter(Boolean).join(' · ')
      : [T('idle'), run.reason].filter(Boolean).join(' · '));
    rb.classList.toggle('wsx-live', !!run.active && !stale);
    rm.hidden = !w || w.id === 'main' || (w.members || []).length > 0;
    rm.disabled = inflight.has('rm');
    const byId = new Map(eps().map(e => [e.id, e])), mem = w && Array.isArray(w.members) ? w.members : [];
    const others = opts.filter(([id]) => !w || id !== String(w.id));
    keyed($('.wsx-members'), mem.map(id => {
      const e = byId.get(id) || {}, inRun = !!run.active && (!Array.isArray(run.members) || run.members.some(m => (typeof m === 'string' ? m : m.id) === id));
      return { k: String(id), v: { L, id: String(id), label: e.label ? String(e.label) : '', serial: e.serial == null ? '' : String(e.serial),
        g: e.generation, side: e.side, on: !!e.connected, busy: !!e.busy, msg: e.message ? String(e.message) : '', stale, others,
        conn: e.connection==='connecting' ? (L==='en'?'Connecting':'连接中') : '', inRun, phase: inRun ? phaseLabel(run.phase,run.mode) : '',
        fl: ['settings', 'disc', 'forget', 'move'].filter(a => inflight.has(`${a}:${id}`)) } };
    }), MEMBER, updMember);
    $('.wsx-empty').hidden = !w || mem.length > 0;
    syncSel($('.wsx-prof'), PROFILES.map(([v, g, s]) => [v, model(g, s)]));
    $('.wsx-addprev').disabled = !w || inflight.has('prev');
    const d = (snap && snap.discovery) || {}, devs = Array.isArray(d.devices) ? d.devices : [];
    const owner = new Map(), bySerial = new Map();
    list.forEach(x => (x.members || []).forEach(m => owner.set(m, name(x))));
    eps().forEach(e => { if (e.serial != null && e.serial !== '') bySerial.set(String(e.serial), e.id); });
    keyed($('.wsx-disc'), devs.filter(x => x && x.serial != null && x.serial !== '').map(x => {
      const s = String(x.serial), eid = bySerial.get(s);
      return { k: s, v: { L, s, g: x.generation, side: x.side_hint, addr: x.address ? String(x.address) : '', opts, cur: w ? String(w.id) : '',
        own: eid != null && owner.has(eid) ? owner.get(eid) : '', fl: ['add', 'new'].filter(a => inflight.has(`${a}:${s}`)) } };
    }), DISC, updDisc);
    tx($('.wsx-dstate'), d.running ? T('scanning') : d.error ? T('failed') + errMsg(d.error)
      : d.updated ? `${T('updated')} ${clock(d.updated)} · ${devs.length} ${T('devices')}` : '');
    $('.wsx-create button').disabled = inflight.has('create');
    $('.wsx-scan button').disabled = inflight.has('scan') || !!d.running;
    renderPanel(w, byId);
    renderErr();
  }
  function renderPanel(w, byId) {                                       // HandGroupPanel is provided separately; mount once, lazily
    const HGP = window.HandGroupPanel;
    if (panel === null && HGP && typeof HGP.mount === 'function') {
      try { panel = HGP.mount($('.wsx-panel'), { onCommand }) || false; } catch (e) { panel = false; say(T('failed') + errMsg(e), true); }
    }
    $('.wsx-nopanel').hidden = !!panel;
    if (!panel || !w) return;
    const members = (w.members || []).map(id => {
      const e = byId.get(id) || {};
      return { id, label: e.label || String(id), side: e.side, generation: e.generation, connected: !!e.connected };
    });
    const ps = { workspace: { id: w.id, label: w.label || String(w.id) }, members, actions: (snap && snap.actions) || [],
      run: w.run || {}, pending: starting.has(w.id), stale: !!pollErr };
    if (!viewer && window.HandGroupView) viewer = window.HandGroupView.mount($('.wsx-views'), {onCamera: (member,camera) => act('workspace_camera',{workspace:cur().id,member,camera})});
    if (viewer) viewer.render(ps,lang());
    const key = JSON.stringify(ps) + lang();
    if (key === panelKey) return;
    panelKey = key;
    try { panel.render(ps, lang()); } catch (e) { say(T('failed') + errMsg(e), true); }
  }
  function renderErr() {                                                // concise poll/heartbeat failures only
    if (root) tx($('.wsx-err'), [...new Set([pollErr, ...beatErr.values()])].filter(Boolean).join(' · '));
  }
  const CARD = {
    settings: k => op(`settings:${k}`, async () => {
      if (k === 'main') { location.hash = 'connection'; return; }       // primary session uses the connection page
      const H = window.HandSessions;
      if (!H || typeof H.show !== 'function') throw new Error(T('noSessions'));
      if (typeof H.refresh === 'function') await H.refresh();
      await H.show(k);
    }),
    disc: (k, p, ws) => op(`disc:${k}`, () => act('workspace_disconnect', { workspace: ws, member: k }), 'discReq'),
    forget: (k, p, ws) => op(`forget:${k}`, () => act('workspace_forget', { workspace: ws, member: k }), 'forgetReq'),
    move: (k, p) => { const to = p && p.value; if (to) op(`move:${k}`, () => act('workspace_move', { workspace: to, member: k }), 'moveReq'); },
    add: (k, p) => {
      const to = p && p.value;
      if (to) op(`add:${k}`, () => act('workspace_add', { workspace: to, serial: fresh(k).serial }), 'addReq');
    },
    new: k => op(`new:${k}`, async () => {
      const d = fresh(k), id = wsId(await act('workspace_create', { label: `${T('autoLabel')} ${String(d.serial).slice(-4)}` }));
      if (id == null || id === '') throw new Error(T('noId'));
      choose(id);
      try { await act('workspace_add', { workspace: id, serial: d.serial }); say(T('addReq')); }
      catch (e) { say(T('createdNoAdd') + errMsg(e), true); }            // never report "connected" on failure
    }),
  };
  function onCard(e) {
    const b = e.target.closest('button[data-wsx]'), c = b && b.closest('.wsx-hc'), f = b && CARD[b.dataset.wsx], w = cur();
    if (c && f && w && !b.disabled) f(c.dataset.k, c.querySelector('.wsx-sel'), w.id);
  }
  function choose(id) {
    if (starting.size || id == null) return;
    sel = String(id); selAt = performance.now();
    try { localStorage.setItem(STORE, sel); } catch (_) { /* storage blocked */ }
    render();
  }
  function onCreate(e) {
    e.preventDefault();
    const inp = e.target.querySelector('input'), label = inp.value.trim();
    if (!label) { say(T('needName'), true); inp.focus(); return; }
    op('create', async () => {
      const id = wsId(await act('workspace_create', { label }));
      inp.value = '';
      if (id != null && id !== '') choose(id);
    }, 'created');
  }
  function applyLang() {
    root.querySelectorAll('[data-wsx-t]').forEach(el => tx(el, T(el.dataset.wsxT)));
    root.querySelectorAll('[data-wsx-tp]').forEach(el => { el.placeholder = T(el.dataset.wsxTp); });
    root.lang = lang() === 'en' ? 'en' : 'zh-CN';
    if (topBtn) tx(topBtn, T('hands'));
    panelKey = ''; render();
  }
  function init() {
    const body = document.querySelector('#page-devices .page-body');
    if (!body || body.querySelector('.wsx-root')) return;
    body.classList.add('wsx-host');                                     // CSS hides the legacy .wb-card blocks here
    const old = document.getElementById('wb-device-switch');
    if (old) old.classList.add('wsx-legacy');
    root = document.createElement('section');
    root.className = 'wsx-root'; root.setAttribute('aria-labelledby', 'wsx-title'); root.innerHTML = SKELETON;
    body.prepend(root);
    const top = document.querySelector('.top-actions');
    if (top) {
      topBtn = document.createElement('button'); topBtn.type = 'button'; topBtn.className = 'wsx-top';
      topBtn.addEventListener('click', () => {
        try { const r = window.HandSessions && window.HandSessions.show('main'); if (r && r.catch) r.catch(() => {}); } catch (_) { /* ignore */ }
        location.hash = 'devices';
      });
      top.prepend(topBtn);
    }
    $('.wsx-ws').addEventListener('change', e => choose(e.target.value));
    $('.wsx-members').addEventListener('click', onCard);
    $('.wsx-disc').addEventListener('click', onCard);
    $('.wsx-disc').addEventListener('change', e => { if (e.target.classList.contains('wsx-sel')) e.target._touched = true; });
    $('.wsx-create').addEventListener('submit', onCreate);
    $('.wsx-scan').addEventListener('submit', e => {
      e.preventDefault();
      const address = e.target.querySelector('input').value.trim();
      op('scan', () => act('workspace_scan', { address }), 'scanReq');
    });
    $('.wsx-addprev').addEventListener('click', () => {
      const w = cur(), p = $('.wsx-prof').value;
      if (w && p) op('prev', () => act('workspace_add', { workspace: w.id, preview_profile: p }), 'prevReq');
    });
    $('.wsx-stopall').addEventListener('click',()=>op('stopAll',async()=>{stopAll();const result=await act('fleet_stop');if(result.errors?.length)throw new Error(result.errors.map(e=>e.id+': '+e.error).join('; '));},'stopReq'));
    $('.wsx-rmws').addEventListener('click', () => { const w = cur(); if (w) op('rm', () => act('workspace_remove', { workspace: w.id }), 'rmReq'); });
    window.addEventListener('wuji-language', e => {                    // capture phase: window- or document-dispatched
      const d = e.detail, v = typeof d === 'string' ? d : d && d.lang;
      forced = v === 'en' || v === 'zh' ? v : null;
      applyLang();
    }, true);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) kick(); });
    window.addEventListener('pagehide', stopAll);
    applyLang(); kick();
    setInterval(() => poll(false), 1000);
    setInterval(beat, 250);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
})();
