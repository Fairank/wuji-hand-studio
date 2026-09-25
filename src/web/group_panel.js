/* group_panel.js — HandGroupPanel: presentation plus explicit user commands only.
   The backend stays authoritative: no fetching, timers, leases, retries or motion logic here. */
(function () {
  'use strict';
  let seq = 0;
  const SRC = ['编排动作 · 实机效果待验证','Authored choreography · physical result not yet verified'];
  const SPEEDS = [0.1, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4], CYCLES = [1, 3], AMPS = [0.25, 0.5, 0.75, 1];
  const T = {
    title: ['多手编排', 'Hand group choreography'], status: ['运行状态', 'Run status'],
    mode: ['模式', 'Mode'], elapsed: ['已用时间', 'Elapsed'], reason: ['原因', 'Reason'],
    authority: ['各只手的进度显示在这里，可随时停止整组。', 'Track each hand here and stop the entire group at any time.'],
    stop: ['停止', 'Stop'], setup: ['编排设置', 'Setup'],
    members: ['参与的手', 'Participating hands'], count: ['已选 {n} / {t}', '{n} of {t} selected'],
    empty: ['工作区暂无手部设备，可用后会显示在这里。', 'No hands in the workspace yet. They will appear here when available.'],
    type: ['编排类型', 'Choreography type'],
    same: ['同步动作', 'Same action'], sameDesc: ['所选的每只手执行同一动作', 'Every selected hand performs the same action'],
    pair: ['双手衔接', 'Paired routine'], pairDesc: ['一只左手与一只右手连贯配合', 'One left and one right hand in a connected routine'],
    pairNote: ['摆放：掌心朝向观众，拇指朝内。这些功能无法移动底座，请手动摆放双手。',
      'Arrangement: palms toward the viewer, thumbs pointing inward. These functions cannot move the base; place the hands manually.'],
    action: ['动作', 'Action'], noAction: ['此类型暂无动作', 'No actions for this type'], options: ['循环与幅度', 'Cycles & amplitude'],
    speed: ['速度', 'Speed'], cycles: ['循环次数', 'Cycles'], amp: ['幅度', 'Amplitude'],
    clear: ['我已确认工作区内无人员和障碍物', 'I confirm the workspace is clear of people and obstacles'],
    preview: ['开始预览', 'Start preview'], hardware: ['开始实机播放', 'Play on real hands'],
    destination: ['播放到', 'Play on'], targetPreview: ['屏幕预览', 'Screen preview'], targetHardware: ['真实手', 'Real hands'],
    previewReady: ['将在屏幕中展示所选手的动作。', 'The selected hands will play on screen.'],
    hActive: ['正在运行，设置已锁定；停止按钮始终可用。', 'Run in progress. Setup is locked; Stop stays available.'],
    hPending: ['正在等待响应…', 'Waiting for a response…'], hStale: ['状态暂不可用，请等待重新连接。','Status unavailable. Waiting for reconnection.'],
    hEmpty: ['工作区至少需要两只手才能开始。', 'At least two hands must be in the workspace to start.'],
    hMin: ['请至少选择两只手。', 'Select at least two hands.'],
    hPair: ['左右配对需恰好一只左手和一只右手（当前：左 {l}，右 {r}，共 {n}）。', 'A pair needs exactly one left and one right hand (now: {l} left, {r} right, {n} total).'],
    hNoAct: ['请选择一个动作。', 'Choose an action.'],
    hOffline: ['可以预览。实机运行需要所选的手全部在线。', 'Preview is available. Real hands need every selected hand online.'],
    hGen: ['可以预览。实机运行仅支持二代手（hand2）。', 'Preview is available. Real hands support hand2 units only.'],
    hClear: ['可以预览。确认工作区无障碍后可实机运行。', 'Preview is available. Confirm the workspace is clear to use real hands.'],
    hReady: ['已就绪：可预览或实机运行。', 'Ready to preview or run on real hands.'],
    mSending: ['正在发送启动请求…', 'Sending start request…'], mStopping: ['正在发送停止请求…', 'Sending stop request…'],
    mStarted: ['启动请求已发送，请以运行状态为准。', 'Start request sent. Rely on the run status for the outcome.'],
    mStopped: ['停止请求已发送，请以运行状态为准。', 'Stop request sent. Rely on the run status for the outcome.'],
    mStartErr: ['启动未完成：', 'Start not completed: '], mStopErr: ['停止请求失败：', 'Stop request failed: '],
    mNoHandler: ['未配置指令处理程序。', 'No command handler is configured.'], mUnknown: ['未知错误', 'unknown error'],
    left: ['左手', 'Left'], right: ['右手', 'Right'], hand1: ['一代', 'Hand 1'], hand2: ['二代', 'Hand 2'],
    online: ['在线', 'Online'], offline: ['离线', 'Offline'],
    modePreview: ['预览', 'Preview'], modeHardware: ['实机', 'Real hands'], sec: [' 秒', ' s']
  };
  const P = {
    idle: ['空闲', 'Idle'], preparing: ['准备中', 'Preparing'], arming: ['使能中', 'Arming'], waiting: ['等待中', 'Waiting'],
    playing: ['执行中', 'Playing'], completed: ['已完成', 'Completed'], stopped: ['已停止', 'Stopped'], failed: ['未完成', 'Failed'], stop_unconfirmed: ['停止待确认','Stop unconfirmed'], returning: ['返回中','Returning'], ready: ['已就绪','Ready']
  };
  const TONE = { preparing: 'busy', arming: 'busy', waiting: 'busy', playing: 'live', completed: 'ok', failed: 'bad' };

  const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  const fmt = (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => (own(v, k) ? String(v[k]) : ''));
  const setText = (n, s) => { s = s == null ? '' : String(s); if (n.textContent !== s) n.textContent = s; };
  const setTone = (n, v) => { if (n.getAttribute('data-tone') !== v) n.setAttribute('data-tone', v); };

  // Human-readable text from an error/result; never serializes whole objects.
  function pickText(v) {
    if (v == null) return '';
    if (typeof v !== 'object') return String(v);
    for (const k of ['message', 'error', 'reason', 'detail']) {
      const s = v[k];
      if (typeof s === 'string' && s) return s;
      if (s && typeof s === 'object' && typeof s.message === 'string') return s.message;
    }
    return '';
  }

  function norm(s) {
    s = s && typeof s === 'object' ? s : {};
    const seen = new Set(), members = [];
    for (const m of Array.isArray(s.members) ? s.members : []) {
      if (!m || m.id == null || seen.has(String(m.id))) continue;
      const key = String(m.id);
      seen.add(key);
      members.push({ id: m.id, key, label: m.label == null || m.label === '' ? key : String(m.label),
        side: m.side, generation: m.generation, connected: m.connected === true });
    }
    const actions = (Array.isArray(s.actions) ? s.actions : []).filter(a => a && a.id != null);
    return { workspaceKey:String(s.workspace?.id||''), members, actions, stale: s.stale === true, run: s.run && typeof s.run === 'object' ? s.run : {} };
  }

  // Keyed reconcile: reuses nodes (keeping focus/checked state) and moves them only when out of order.
  function syncList(box, map, items, keyOf, make, patch) {
    const next = new Map();
    let i = 0;
    for (const it of items) {
      const k = keyOf(it);
      if (next.has(k)) continue;
      const n = map.get(k) || make(it);
      patch(n, it);
      next.set(k, n);
      if (box.children[i] !== n) box.insertBefore(n, box.children[i] || null);
      i++;
    }
    map.forEach((n, k) => { if (!next.has(k)) n.remove(); });
    map.clear();
    next.forEach((n, k) => map.set(k, n));
  }

  function markup(p) {
    const opt = (list, lab) => list.map(v => `<option value="${v}"${v === 1 ? ' selected' : ''}>${lab(v)}</option>`).join('');
    const field = (k, inner) => `<div class="hg-field"><label class="hg-label" for="${p}${k}" data-t="${k}"></label>` +
      `<select class="hg-select" id="${p}${k}" data-k="${k}">${inner}</select></div>`;
    const kind = k => `<label class="hg-opt"><input type="radio" name="${p}kind" value="${k}"${k === 'same' ? ' checked' : ''}>` +
      `<span class="hg-opt-text"><span class="hg-opt-title" data-t="${k}"></span><span class="hg-opt-desc" data-t="${k}Desc"></span></span></label>`;
    return `<header class="hg-head"><h2 class="hg-h" data-t="title"></h2></header>
<section class="hg-card hg-status" aria-labelledby="${p}st">
  <div class="hg-row"><h3 class="hg-title" id="${p}st" data-t="status"></h3><span class="hg-badge hg-phase"></span></div>
  <dl class="hg-facts">
    <div class="hg-fact"><dt data-t="mode"></dt><dd class="hg-mode"></dd></div>
    <div class="hg-fact"><dt data-t="elapsed"></dt><dd class="hg-elapsed"></dd></div>
    <div class="hg-fact hg-reason-row"><dt data-t="reason"></dt><dd class="hg-reason"></dd></div>
  </dl>
  <ul class="hg-runlist"></ul>
  <div class="hg-row"><p class="hg-foot" data-t="authority"></p><button type="button" class="hg-btn hg-stop" data-cmd="stop" data-t="stop"></button></div>
</section>
<section class="hg-card hg-setup" aria-labelledby="${p}su">
  <h3 class="hg-title" id="${p}su" data-t="setup"></h3>
  <fieldset class="hg-config">
    <fieldset class="hg-group"><legend class="hg-legend"><span data-t="members"></span><span class="hg-count"></span></legend>
      <div class="hg-body"><p class="hg-empty" data-t="empty"></p><div class="hg-members"></div></div></fieldset>
    <fieldset class="hg-group"><legend class="hg-legend" data-t="type"></legend>
      <div class="hg-body"><div class="hg-seg">${kind('same')}${kind('pair')}</div><p class="hg-note" data-t="pairNote"></p></div></fieldset>
    <div class="hg-grid hg-main-fields">${field('action', '')}${field('speed', opt(SPEEDS, v => v + '×'))}</div>
    <details class="hg-options"><summary data-t="options"></summary><div class="hg-grid">${field('cycles', opt(CYCLES, String))}${field('amp', opt(AMPS, v => Math.round(v * 100) + '%'))}</div></details>
    <fieldset class="hg-destination"><legend class="hg-legend" data-t="destination"></legend><div class="hg-targets"><label><input type="radio" name="${p}destination" value="preview" checked><span data-t="targetPreview"></span></label><label><input type="radio" name="${p}destination" value="hardware"><span data-t="targetHardware"></span></label></div></fieldset>
    <label class="hg-clear" hidden><input type="checkbox" class="hg-clearbox"><span data-t="clear"></span></label>
    <div class="hg-actions"><button type="button" class="hg-btn" data-cmd="preview" data-t="preview" aria-describedby="${p}hint"></button><button type="button" class="hg-btn hg-primary" data-cmd="hardware" data-t="hardware" aria-describedby="${p}hint"></button></div>
  </fieldset>
  <p class="hg-hint" id="${p}hint" aria-live="polite" aria-atomic="true"></p>
  <p class="hg-msg" aria-live="polite" aria-atomic="true"></p>
</section><p class="hg-src hg-source-note"></p>`;
  }

  function mount(root, opts) {
    if (!root || !root.ownerDocument) throw new TypeError('HandGroupPanel.mount: root element required');
    const onCommand = opts && opts.onCommand;
    const doc = root.ownerDocument;
    const p = `hg${++seq}${Math.random().toString(36).slice(2, 6)}-`;
    const el = doc.createElement('div');
    el.className = 'hg-panel';
    el.innerHTML = markup(p); // static template only; runtime data goes through textContent / value
    root.appendChild(el);
    const $ = s => el.querySelector(s);
    el.insertBefore($('.hg-status'),$('.hg-src')); // visual, reading and focus order agree
    const E = {
      phase: $('.hg-phase'), mode: $('.hg-mode'), elapsed: $('.hg-elapsed'), reasonRow: $('.hg-reason-row'),
      reason: $('.hg-reason'), runlist: $('.hg-runlist'), stop: $('[data-cmd="stop"]'), config: $('.hg-config'),
      count: $('.hg-count'), empty: $('.hg-empty'), members: $('.hg-members'), note: $('.hg-note'),
      action: $('[data-k="action"]'), speed: $('[data-k="speed"]'), cycles: $('[data-k="cycles"]'), amp: $('[data-k="amp"]'),
      clear: $('.hg-clearbox'), preview: $('[data-cmd="preview"]'), hw: $('[data-cmd="hardware"]'),
      hint: $('.hg-hint'), msg: $('.hg-msg')
    };
    const st = { L: 'zh', snap: norm(null), kind: 'same', destination: 'preview', pref: { same: '', pair: '' }, sig: '', sel: new Set(),
      pendStart: false, pendStop: false, cmd: 0, msg: null, dead: false };
    const memMap = new Map(), runMap = new Map();
    const ix = () => (st.L === 'en' ? 1 : 0);
    const t = k => (T[k] ? T[k][ix()] : k);
    const phaseOf = v => (v == null || v === '' ? 'idle' : String(v));
    const phaseText = ph => (own(P, ph) ? P[ph][ix()] : ph); // unknown phases shown verbatim
    const toneOf = ph => (own(TONE, ph) ? TONE[ph] : 'idle');
    const mk = (tag, cls) => { const n = doc.createElement(tag); if (cls) n.className = cls; return n; };

    function applyStatic() {
      setText($('.hg-src'), SRC[ix()]);
      el.lang = st.L === 'en' ? 'en' : 'zh-CN';
      el.querySelectorAll('[data-t]').forEach(n => setText(n, t(n.dataset.t)));
    }

    function makeMember(m) {
      const row = mk('label', 'hg-member'), cb = mk('input', 'hg-check');
      const main = mk('span', 'hg-member-main'), meta = mk('span', 'hg-meta');
      cb.type = 'checkbox';
      cb.value = m.key;
      cb.checked = st.sel.has(m.key); // only set on creation; never overwritten by later renders
      meta.append(mk('span', 'hg-chip'), mk('span', 'hg-chip'), mk('span', 'hg-chip'));
      main.append(mk('span', 'hg-member-name'), meta);
      row.append(cb, main);
      return row;
    }

    function patchMember(row, m) {
      const [side, gen, conn] = row.querySelectorAll('.hg-chip');
      setText(row.querySelector('.hg-member-name'), m.label);
      setText(side, m.side === 'left' || m.side === 'right' ? t(m.side) : m.side || '?');
      setText(gen, m.generation === 'hand1' || m.generation === 'hand2' ? t(m.generation) : m.generation || '?');
      setTone(gen, m.generation === 'hand2' ? '' : 'warn');
      setText(conn, t(m.connected ? 'online' : 'offline'));
      setTone(conn, m.connected ? 'ok' : 'bad');
    }

    function renderActions() {
      const list = st.snap.actions.filter(a => a.kind === st.kind);
      const name = a => String((st.L === 'en' ? a.en || a.zh : a.zh || a.en) || a.id);
      const sig = JSON.stringify([st.kind, st.L, list.map(a => [String(a.id), name(a)])]);
      if (sig === st.sig) return; // unchanged options: never touch the (possibly focused) select
      st.sig = sig;
      const sel = E.action;
      sel.textContent = '';
      if (!list.length) { const o = mk('option'); o.value = ''; o.textContent = t('noAction'); sel.append(o); }
      for (const a of list) { const o = mk('option'); o.value = String(a.id); o.textContent = name(a); sel.append(o); }
      const want = st.pref[st.kind];
      sel.value = list.some(a => String(a.id) === want) ? want : list.length ? String(list[0].id) : '';
    }

    function renderStatus() {
      const run = st.snap.run, ph = phaseOf(run.phase), sec = run.elapsed_s;
      setText(E.phase, ph==='arming'&&run.mode==='preview'?(st.L==='en'?'Preparing preview':'准备预览'):phaseText(ph));
      setTone(E.phase, toneOf(ph));
      setText(E.mode, run.mode === 'preview' ? t('modePreview') : run.mode === 'hardware' ? t('modeHardware') : run.mode ? String(run.mode) : '—');
      setText(E.elapsed, typeof sec === 'number' && isFinite(sec) ? sec.toFixed(1) + t('sec') : '—');
      const reason = pickText(run.reason);
      setText(E.reason, reason);
      E.reasonRow.hidden = !reason;
      const names = new Map(st.snap.members.map(m => [m.key, m.label]));
      const rows = (Array.isArray(run.members) ? run.members : []).filter(x => x && x.id != null);
      E.runlist.hidden = !rows.length;
      syncList(E.runlist, runMap, rows, x => String(x.id), () => {
        const item = mk('li', 'hg-runitem');
        item.append(mk('span', 'hg-runname'), mk('span', 'hg-badge'));
        return item;
      }, (item, x) => {
        const k = String(x.id), xp = phaseOf(x.phase);
        setText(item.firstChild, names.get(k) || k);
        setText(item.lastChild, phaseText(xp));
        setTone(item.lastChild, toneOf(xp));
      });
    }

    // Client-side gating is a convenience only; the main program re-validates every command.
    function evaluate() {
      const active = !!st.snap.run.active, pending = st.pendStart || st.pendStop;
      const all = st.snap.members, sel = all.filter(m => st.sel.has(m.key));
      const l = sel.filter(m => m.side === 'left').length, r = sel.filter(m => m.side === 'right').length;
      const act = st.snap.actions.find(a => a.kind === st.kind && String(a.id) === E.action.value);
      const ev = { active, locked: active || pending, sel, act, canPreview: false, canHw: false, hint: 'hReady', vars: { l, r, n: sel.length } };
      if (st.snap.stale) ev.hint = 'hStale';
      else if (active) ev.hint = 'hActive';
      else if (pending) ev.hint = 'hPending';
      else if (all.length < 2) ev.hint = 'hEmpty';
      else if (st.kind === 'pair' && !(sel.length === 2 && l === 1 && r === 1)) ev.hint = 'hPair';
      else if (st.kind === 'same' && sel.length < 2) ev.hint = 'hMin';
      else if (!act) ev.hint = 'hNoAct';
      else {
        ev.canPreview = true;
        if (!sel.every(m => m.connected)) ev.hint = 'hOffline';
        else if (!sel.every(m => m.generation === 'hand2')) ev.hint = 'hGen';
        else if (!E.clear.checked) ev.hint = 'hClear';
        else ev.canHw = true;
      }
      return ev;
    }

    function update() {
      const ev = evaluate(), m = st.msg, n = st.snap.members.length;
      if(ev.active&&['preview','hardware'].includes(st.snap.run.mode))st.destination=st.snap.run.mode;
      el.querySelectorAll(`input[name="${p}destination"]`).forEach(x=>{x.checked=x.value===st.destination;});
      E.config.disabled = ev.locked;
      E.preview.disabled = !ev.canPreview;
      E.hw.disabled = !ev.canHw;
      E.preview.hidden = st.destination !== 'preview';
      E.hw.hidden = st.destination !== 'hardware';
      E.clear.closest('label').hidden = st.destination !== 'hardware';
      E.preview.classList.toggle('hg-primary',st.destination === 'preview');
      E.stop.disabled = st.pendStop;
      E.stop.classList.toggle('hg-danger', ev.active || st.pendStart);
      E.note.hidden = st.kind !== 'pair';
      E.empty.hidden = n > 0;
      E.members.hidden = n === 0;
      setText(E.count, n ? fmt(t('count'), { n: ev.sel.length, t: n }) : '');
      setText(E.hint, fmt(t(st.destination==='preview'&&ev.canPreview?'previewReady':ev.hint), ev.vars));
      setTone(E.hint, ev.canHw ? 'ok' : ev.canPreview ? 'warn' : '');
      const failure=st.snap.run.phase==='failed'?pickText(st.snap.run.reason):'';
      const acknowledged=m&&!m.bad&&['mStarted','mStopped'].includes(m.key)&&st.snap.run.phase&&st.snap.run.phase!=='idle';
      setText(E.msg, failure || (m&&!acknowledged ? t(m.key) + (m.bad ? m.raw || t('mUnknown') : '') : ''));
      setTone(E.msg, failure?'bad':m ? m.tone : '');
      return ev;
    }

    async function send(name, payload) {
      if (typeof onCommand !== 'function') { st.msg = { key: 'mNoHandler', tone: 'bad' }; update(); return; }
      const isStart = name === 'start', id = ++st.cmd;
      if (isStart) st.pendStart = true; else st.pendStop = true;
      st.msg = { key: isStart ? 'mSending' : 'mStopping', tone: '' };
      update();
      let msg;
      try {
        const res = await onCommand(name, payload);
        const bad = !!res && typeof res === 'object' && (res.ok === false || (res.ok !== true && !!res.error));
        msg = bad ? { key: isStart ? 'mStartErr' : 'mStopErr', raw: pickText(res), bad: true, tone: 'bad' }
          : { key: isStart ? 'mStarted' : 'mStopped', tone: 'ok' };
      } catch (err) {
        msg = { key: isStart ? 'mStartErr' : 'mStopErr', raw: pickText(err), bad: true, tone: 'bad' };
      }
      if (st.dead) return;
      if (isStart) st.pendStart = false; else st.pendStop = false;
      if (id === st.cmd) st.msg = msg; // the newest command's message wins
      update();
    }

    function onChange(e) {
      const x = e.target;
      if (!x || !x.classList) return;
      if (x.classList.contains('hg-check')) { if (x.checked) st.sel.add(x.value); else st.sel.delete(x.value); }
      else if (x.name === p + 'kind') { st.kind = x.value === 'pair' ? 'pair' : 'same'; renderActions(); }
      else if (x.name === p + 'destination') st.destination = x.value === 'hardware' ? 'hardware' : 'preview';
      else if (x === E.action) st.pref[st.kind] = x.value;
      update();
    }

    function onClick(e) {
      const b = e.target && e.target.closest ? e.target.closest('[data-cmd]') : null;
      if (!b || !el.contains(b) || b.disabled || st.dead) return;
      if (b.dataset.cmd === 'stop') { send('stop', {}); return; }
      const mode = b.dataset.cmd === 'hardware' ? 'hardware' : 'preview', ev = update();
      if (mode === 'hardware' ? !ev.canHw : !ev.canPreview) return; // re-checked at click time
      send('start', {
        members: ev.sel.map(m => m.id), kind: st.kind, action: ev.act.id, mode,
        speed: Number(E.speed.value), cycles: Number(E.cycles.value), amplitude: Number(E.amp.value),
        workspace_clear: E.clear.checked
      });
    }

    function paint() {
      syncList(E.members, memMap, st.snap.members, m => m.key, makeMember, patchMember);
      renderActions();
      renderStatus();
      update();
    }

    function render(snapshot, lang) {
      if (st.dead) return;
      const next=norm(snapshot);
      if(next.workspaceKey!==st.snap.workspaceKey)st.msg=null;
      st.snap = next;
      const L = lang === 'en' || lang === 'zh' ? lang : st.L;
      if (L !== st.L) { st.L = L; applyStatic(); }
      paint();
    }

    function destroy() {
      if (st.dead) return;
      st.dead = true;
      el.removeEventListener('change', onChange);
      el.removeEventListener('click', onClick);
      memMap.clear();
      runMap.clear();
      el.remove();
    }

    el.addEventListener('change', onChange);
    el.addEventListener('click', onClick);
    setText($('.hg-src'), SRC[ix()]);
    applyStatic();
    paint();
    return { render, destroy };
  }

  window.HandGroupPanel = { mount };
})();
