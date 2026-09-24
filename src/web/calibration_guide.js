/* calibration_guide.js
 * Unofficial, display-only hand-model calibration guide (zh/en).
 * No dependencies, network, storage, timers, robot commands, or HTML from input.
 * window.CalibrationGuide.mount(host, { language, onPreview }) -> { render(state), destroy() }
 */
(function (global) {
  'use strict';

  var MAX_JSON = 16000, STEP_MAX = 600, TEXT_MAX = 2000;
  var KNOWN = ['idle', 'collecting', 'solving', 'completed', 'cancelled', 'error', 'cancelling', 'unconfirmed'];
  var ACTIVE = { collecting: 1, solving: 1, cancelling: 1 };
  var TERMINAL = { completed: 1, cancelled: 1, error: 1 };

  /* Copy is selected by the host language picker. */
  var L = {
    title: '六姿势指南 / Six-pose guide',
    sub: '非官方界面 · 不发送机器人指令 / Unofficial UI · sends no robot commands',
    left: '左手 / Left hand',
    right: '右手 / Right hand',
    yes: '已校准 / Calibrated',
    no: '未校准 / Not calibrated',
    unknown: '未知 / Unknown',
    thisRun: '本次运行 / This run',
    owner: '当前档案所有者 / Current profile owner',
    keepNamed: '保留命名档案 / Preserves named profile',
    noDefault: '默认档案不保存 / Default not saved',
    unavailable: '校准信息不可用 / Calibration info unavailable',
    sequence: '姿势序列 / Pose sequence',
    reference: '姿势参考 / Pose reference',
    official: '官方步骤 / Official step',
    waiting: '等待官方姿势 / Waiting for official pose',
    current: '当前 / Current',
    viewing: '查看中 / Viewing',
    schematic: '示意，非测量 / Schematic, not measured',
    openHand: '姿势之间请张开手 / Open hand between poses',
    side: '侧 / Side',
    sideLeft: '左手 / Left',
    sideRight: '右手 / Right',
    user: '用户 / User',
    serial: '序列号 / Serial',
    phase: '阶段 / Phase',
    step: '原始步骤 / Raw step',
    events: '事件数 / Events',
    progress: '进度 / Progress',
    progressUnknown: '进度未知 / Progress unknown',
    errorDetail: '错误信息 / Error message',
    resultMeta: '结果元数据 / Result metadata',
    none: '无 / None',
    preview: '查看实时映射 / View live mapping',
    diagnostics: '官方诊断 / Official diagnostics',
    noDiag: '无诊断数据 / No diagnostics',
    truncated: '…（已截断 / truncated）'
  };

  var STATUS = {
    idle: '空闲 / Idle',
    collecting: '采集中 / Collecting',
    solving: '求解中 / Solving',
    cancelling: '正在取消 / Cancelling',
    completed: '已完成 / Completed',
    cancelled: '已取消 / Cancelled',
    error: '错误 / Error',
    unconfirmed: '未确认 / Unconfirmed',
    unknown: '未知状态 / Unknown status'
  };

  var MSG = {
    zh: {
      idle: '可选择姿势卡片查看参考。开始或停止请使用主机控件。',
      collecting: '请按官方步骤摆出当前姿势。',
      solving: '正在求解。主机确认结果后才算完成。',
      cancelling: '正在取消，等待主机确认。',
      completed: '主机已确认官方结果。',
      cancelled: '校准已取消。请使用主机控件重新开始。',
      error: '校准出错。请使用主机控件重新开始。',
      unconfirmed: '结果未确认，不视为完成。',
      unknown: '主机报告未知状态，不视为完成。'
    },
    en: {
      idle: 'Select a pose card to inspect it. Use the host controls to start or stop.',
      collecting: 'Form the pose named by the official step.',
      solving: 'Solving. Not complete until the host confirms the result.',
      cancelling: 'Cancelling; waiting for the host to confirm.',
      completed: 'The host has validated the official result.',
      cancelled: 'Calibration was cancelled. Use the host controls to restart.',
      error: 'Calibration stopped with an error. Use the host controls to restart.',
      unconfirmed: 'Result not confirmed; not treated as completed.',
      unknown: 'Unknown status from host; not treated as completed.'
    }
  };

  var GUIDE = {
    zh: ['从张开的手开始。', '保持姿势直到主机进入下一步，然后张开手。'],
    en: ['Start from an open hand.', 'Hold until the host moves on, then open your hand.']
  };
  var WAIT_TITLE = { zh: '等待主机报告当前姿势', en: 'Waiting for the host to report the current pose' };
  var FINGERS = { zh: ['拇指', '食指', '中指', '无名指', '小指'], en: ['Thumb', 'Index', 'Middle', 'Ring', 'Pinky'] };
  var ROLES = {
    zh: { t: '指尖相触', b: '弯曲 90°', f: '伸平并拢', r: '放松', n: '—' },
    en: { t: 'Tip touch', b: 'Bend 90°', f: 'Flat, together', r: 'Relaxed', n: '—' }
  };
  function P(zh, en, azh, aen, roles) { return { zh: zh, en: en, act: { zh: azh, en: aen }, roles: roles }; }
  var POSES = [
    P('拇指与食指指尖相触', 'Thumb–index tip touch', '拇指指尖与食指指尖相触。', 'Touch the thumb tip to the index fingertip.', 'ttnnn'),
    P('拇指与中指指尖相触', 'Thumb–middle tip touch', '拇指指尖与中指指尖相触。', 'Touch the thumb tip to the middle fingertip.', 'tntnn'),
    P('拇指与无名指指尖相触', 'Thumb–ring tip touch', '拇指指尖与无名指指尖相触。', 'Touch the thumb tip to the ring fingertip.', 'tnntn'),
    P('拇指与小指指尖相触', 'Thumb–pinky tip touch', '拇指指尖与小指指尖相触。', 'Touch the thumb tip to the pinky fingertip.', 'tnnnt'),
    P('四指弯曲 90°，拇指放松', 'Four fingers bent 90°, thumb relaxed', '四指弯曲至 90°，拇指保持放松。', 'Bend the four fingers to 90°; keep the thumb relaxed.', 'rbbbb'),
    P('四指伸平并拢，拇指放松', 'Four fingers flat together, thumb relaxed', '四指完全伸平并拢，拇指保持放松。', 'Hold the four fingers fully flat and together; keep the thumb relaxed.', 'rffff')
  ];

  /* ---------- Pure helpers (exposed as CalibrationGuide.helpers) ---------- */
  function isObj(v) { return v !== null && typeof v === 'object'; }
  function isInt(v) { return typeof v === 'number' && isFinite(v) && Math.floor(v) === v; }
  function clip(s, n) { s = String(s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function plain(v, n) {
    if (typeof v === 'string') return clip(v, n);
    return typeof v === 'number' && isFinite(v) ? String(v) : '';
  }

  /* Budgeted JSON: stops work at `max` chars, handles cycles/depth, never throws. */
  function boundedJson(value, max, indent) {
    max = isInt(max) && max > 0 ? max : MAX_JSON;
    var pad = typeof indent === 'string' ? indent : '  ';
    var nl = pad ? '\n' : '', colon = pad ? ': ' : ':';
    var out = [], len = 0, cut = false, stack = [];
    function put(s) {
      if (cut) return;
      if (len + s.length > max) { out.push(s.slice(0, max - len)); len = max; cut = true; }
      else { out.push(s); len += s.length; }
    }
    function str(s) { put(JSON.stringify(s.length > max ? s.slice(0, max) : s)); }
    function walk(v, depth) {
      if (cut) return;
      if (isObj(v) && typeof v.toJSON === 'function') {
        try { v = v.toJSON(); } catch (e) { v = '[toJSON error]'; }
      }
      var t = typeof v;
      if (t === 'string') return str(v);
      if (t === 'number') return put(isFinite(v) ? String(v) : 'null');
      if (t === 'boolean' || t === 'bigint') return put(String(v));
      if (!isObj(v)) return put('null');
      if (stack.indexOf(v) !== -1) return put('"[Circular]"');
      if (depth >= 40) return put('"[Depth limit]"');
      stack.push(v);
      var arr = Array.isArray(v), keys = arr ? null : Object.keys(v);
      var n = arr ? v.length : keys.length, first = true, inner = nl + pad.repeat(depth + 1);
      put(arr ? '[' : '{');
      for (var i = 0; i < n && !cut; i++) {
        var item = arr ? v[i] : v[keys[i]], it = typeof item;
        if (!arr && (item === undefined || it === 'function' || it === 'symbol')) continue;
        put((first ? '' : ',') + inner);
        first = false;
        if (!arr) { str(keys[i]); put(colon); }
        walk(item, depth + 1);
      }
      if (!first) put(nl + pad.repeat(depth));
      put(arr ? ']' : '}');
      stack.pop();
    }
    walk(value, 0);
    var s = out.join('');
    if (cut) {
      var mark = (nl || ' ') + L.truncated;
      s = s.slice(0, Math.max(0, max - mark.length)) + mark;
    }
    return s;
  }

  function formatStep(step) {
    if (step === undefined || step === null) return '';
    if (typeof step === 'string') return clip(step, STEP_MAX);
    if (typeof step === 'number') return String(step);
    return boundedJson(step, STEP_MAX, '');
  }
  /* Explicit normalized 0..1 only; anything else is "unknown" (never clamped). */
  function normalizeProgress(p) { return typeof p === 'number' && isFinite(p) && p >= 0 && p <= 1 ? p : null; }
  function normalizePoseIndex(i) { return isInt(i) && i >= 0 && i < POSES.length ? i : null; }
  function statusKey(s) {
    if (s === undefined || s === null || s === '') return 'idle';
    return typeof s === 'string' && KNOWN.indexOf(s) !== -1 ? s : 'unknown';
  }
  function isActive(run, key) { return !TERMINAL[key] && (run.running === true || ACTIVE[key] === 1); }
  function sideState(available, hand) {
    if (available !== true || !isObj(hand) || typeof hand.calibrated !== 'boolean') return 'unknown';
    return hand.calibrated ? 'yes' : 'no';
  }

  /* ---------- Component ---------- */
  var uid = 0, NONE = {};

  function mount(host, opts) {
    if (!host || host.nodeType !== 1) throw new TypeError('CalibrationGuide.mount: host must be an Element');
    opts = opts || {};
    // Workbench language picker selects one language, rather than displaying
    // both labels in every control. Preserve Claude's original bilingual copy.
    var labelPairs = {};
    Object.keys(L).forEach(function(k){addPair(L[k]);});
    Object.keys(STATUS).forEach(function(k){addPair(STATUS[k]);});
    function addPair(value){var at=value.indexOf(' / ');if(at>=0)labelPairs[value]={zh:value.slice(0,at),en:value.slice(at+3)};}
    function translated(value){return labelPairs[value] ? labelPairs[value][lang()] : value;}
    var paintedLanguage=null;
    var doc = host.ownerDocument || document;
    var id = 'cg-' + (++uid);
    var cache = new WeakMap();
    var destroyed = false, state = {}, selected = 0, diagOpen = false;
    var wasActive = false, lastCount = null, prevEvent = null, staleEvent = NONE, staleCount = null;
    var diagFor = NONE, diagCount = null, resFor = NONE, curActive = false, curKey = 'idle';

    function h(tag, cls, text, parent) {
      var e = doc.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) {e.textContent = translated(text);if(labelPairs[text])e.setAttribute('data-cg-label',text);}
      if (parent) parent.appendChild(e);
      return e;
    }
    /* Cached writers: unchanged values never touch the DOM. */
    function memo(e) { var m = cache.get(e); if (!m) { m = {}; cache.set(e, m); } return m; }
    function setText(e, v) { v=translated(v);var m = memo(e); if (m.t !== v) { m.t = v; e.textContent = v; } }
    function setAttr(e, n, v) {
      var m = memo(e), k = 'a:' + n;
      if (m[k] === v) return;
      m[k] = v;
      if (v === null) e.removeAttribute(n); else e.setAttribute(n, v);
    }
    function setHidden(e, hide) {
      var m = memo(e);
      if (m.h === hide) return;
      m.h = hide;
      if (hide) {
        var a = doc.activeElement;
        if (a && e.contains(a)) { try { title.focus({ preventScroll: true }); } catch (x) { title.focus(); } }
      }
      e.hidden = hide;
    }

    /* ---- Build DOM once ---- */
    var root = h('section', 'cg-root');
    root.setAttribute('aria-labelledby', id + '-title');

    var head = h('header', 'cg-head', null, root);
    var title = h('h2', 'cg-title', L.title, head);
    title.id = id + '-title';
    title.tabIndex = -1;
    h('p', 'cg-sub', L.sub, head);

    var stBox = h('div', 'cg-status', null, root);
    stBox.setAttribute('role', 'status');
    stBox.setAttribute('aria-live', 'polite');
    stBox.setAttribute('aria-atomic', 'true');
    var stLabel = h('strong', 'cg-status-label', null, stBox);
    var stMsg = h('span', 'cg-status-msg', null, stBox);
    var stPose = h('span', 'cg-status-pose', null, stBox);

    var profile = h('div', 'cg-profile', null, root);
    function sideCard(label) {
      var card = h('div', 'cg-card cg-side', null, profile);
      h('span', 'cg-card-label', label, card);
      return { card: card, value: h('span', 'cg-card-value', null, card), tag: h('span', 'cg-tag', L.thisRun, card) };
    }
    var leftCard = sideCard(L.left), rightCard = sideCard(L.right);
    var ownerCard = h('div', 'cg-card cg-owner', null, profile);
    h('span', 'cg-card-label', L.owner, ownerCard);
    var ownerVal = h('span', 'cg-card-value', null, ownerCard);
    var notes = h('ul', 'cg-notes', null, ownerCard);
    h('li', null, L.keepNamed, notes);
    h('li', null, L.noDefault, notes);
    var unavail = h('p', 'cg-unavail', L.unavailable, ownerCard);

    var main = h('div', 'cg-main', null, root);
    var seq = h('div', 'cg-seq', null, main);
    seq.setAttribute('role', 'group');
    seq.setAttribute('aria-labelledby', id + '-seq');
    var seqHead = h('div', 'cg-seq-head', null, seq);
    h('h3', 'cg-h3', L.sequence, seqHead).id = id + '-seq';
    var seqCount = h('span', 'cg-count', null, seqHead);
    var list = h('ol', 'cg-pose-list', null, seq);
    var cards = POSES.map(function (p, i) {
      var btn = h('button', 'cg-pose', null, h('li', null, null, list));
      btn.type = 'button';
      btn.setAttribute('data-index', String(i));
      h('span', 'cg-pose-num', String(i + 1), btn);
      return { btn: btn, name: h('span', 'cg-pose-name', null, btn), tag: h('span', 'cg-pose-tag', null, btn) };
    });

    var guide = h('section', 'cg-guide', null, main);
    guide.setAttribute('aria-labelledby', id + '-guide');
    var gHead = h('div', 'cg-guide-head', null, guide);
    var badge = h('span', 'cg-badge', null, gHead);
    var gCount = h('span', 'cg-count', null, gHead);
    var gTitle = h('h3', 'cg-guide-title', null, guide);
    gTitle.id = id + '-guide';
    var stepsOl = h('ol', 'cg-steps', null, guide);
    var stepLis = [0, 1, 2].map(function () { return h('li', null, null, stepsOl); });
    var fingersWrap = h('div', 'cg-fingers-wrap', null, guide);
    h('span', 'cg-caption', L.schematic, fingersWrap);
    var fingersUl = h('ul', 'cg-fingers', null, fingersWrap);
    var fingers = [0, 1, 2, 3, 4].map(function () {
      var li = h('li', 'cg-finger', null, fingersUl);
      return { li: li, name: h('span', 'cg-finger-name', null, li), role: h('span', 'cg-finger-role', null, li) };
    });
    h('p', 'cg-note', L.openHand, guide);

    var live = h('div', 'cg-live', null, guide);
    var meta = h('dl', 'cg-meta', null, live);
    function metaRow(label) {
      var row = h('div', 'cg-meta-row', null, meta);
      h('dt', null, label, row);
      return { row: row, val: h('dd', null, null, row) };
    }
    var mSide = metaRow(L.side), mUser = metaRow(L.user), mSerial = metaRow(L.serial);
    var mPhase = metaRow(L.phase), mStep = metaRow(L.step), mEvents = metaRow(L.events);
    var prog = h('div', 'cg-progress', null, live);
    prog.setAttribute('role', 'progressbar');
    prog.setAttribute('aria-label', L.progress);
    prog.setAttribute('aria-valuemin', '0');
    prog.setAttribute('aria-valuemax', '100');
    var progFill = h('div', 'cg-progress-fill', null, prog);
    var progText = h('span', 'cg-progress-text', null, live);

    var outcome = h('section', 'cg-outcome', null, root);
    outcome.setAttribute('aria-labelledby', id + '-out');
    var oTitle = h('h3', 'cg-h3', null, outcome);
    oTitle.id = id + '-out';
    var oMsg = h('p', 'cg-outcome-msg', null, outcome);
    var oErrWrap = h('div', 'cg-outcome-error', null, outcome);
    h('span', 'cg-caption', L.errorDetail, oErrWrap);
    var oErr = h('p', 'cg-error-text', null, oErrWrap);
    var resWrap = h('div', 'cg-result', null, outcome);
    h('span', 'cg-caption', L.resultMeta, resWrap);
    var resPre = h('pre', 'cg-pre', null, resWrap);
    resPre.tabIndex = 0;
    resPre.setAttribute('aria-label', L.resultMeta);
    var previewBtn = h('button', 'cg-btn cg-btn-primary', L.preview, outcome);
    previewBtn.type = 'button';

    var diag = h('section', 'cg-diag', null, root);
    var diagBtn = h('button', 'cg-btn cg-disclosure', null, diag);
    diagBtn.type = 'button';
    diagBtn.setAttribute('aria-controls', id + '-diag');
    h('span', 'cg-chevron', null, diagBtn).setAttribute('aria-hidden', 'true');
    h('span', null, L.diagnostics, diagBtn);
    var diagPre = h('pre', 'cg-pre', null, diag);
    diagPre.id = id + '-diag';
    diagPre.tabIndex = 0;
    diagPre.setAttribute('aria-label', L.diagnostics);

    function lang() {
      try { return typeof opts.language === 'function' && opts.language() === 'zh' ? 'zh' : 'en'; }
      catch (e) { return 'en'; }
    }
    function paintSide(c, st, inRun) {
      setText(c.value, L[st]);
      setAttr(c.card, 'class', 'cg-card cg-side cg-side--' + st + (inRun ? ' cg-side--run' : ''));
      setHidden(c.tag, !inRun);
    }
    function setRow(r, v) { setText(r.val, v); setHidden(r.row, !v); }

    /* ---- Update: diff-only writes ---- */
    function update() {
      var run = isObj(state.run) ? state.run : {};
      var cur = isObj(state.current) ? state.current : {};
      var tx = lang(), zh = tx === 'zh';
      if(paintedLanguage!==tx){
        paintedLanguage=tx;
        root.querySelectorAll('[data-cg-label]').forEach(function(e){setText(e,e.getAttribute('data-cg-label'));});
        root.querySelectorAll('[data-cg-aria]').forEach(function(e){setAttr(e,'aria-label',translated(e.getAttribute('data-cg-aria')));});
      }
      var key = statusKey(run.status);
      var active = isActive(run, key);
      var pose = normalizePoseIndex(run.pose_index);
      var count = isInt(run.event_count) ? run.event_count : null;
      var avail = state.available === true;
      var i;

      /* New run: drop previous result/diagnostics. */
      if ((active && !wasActive) || (count !== null && lastCount !== null && count < lastCount)) {
        staleEvent = prevEvent;
        staleCount = count;
        diagFor = NONE;
        resFor = NONE;
        setText(diagPre, '');
        setText(resPre, '');
      }
      wasActive = active;
      lastCount = count;
      prevEvent = run.event;
      curActive = active;
      curKey = key;
      setAttr(root, 'lang', zh ? 'zh-CN' : 'en');

      /* Status region */
      var tone = key === 'completed' ? 'ok' : key === 'error' ? 'err'
        : (key === 'cancelled' || key === 'unconfirmed' || key === 'unknown') ? 'warn'
        : active ? 'busy' : 'idle';
      setAttr(stBox, 'class', 'cg-status cg-tone-' + tone);
      setText(stLabel, STATUS[key] + (key === 'unknown' ? ': ' + clip(String(run.status), 60) : ''));
      setText(stMsg, MSG[tx][key]);
      var poseLine = !active ? '' : pose === null ? L.waiting
        : (zh ? '姿势 ' : 'Pose ') + (pose + 1) + ' / 6 · ' + POSES[pose][tx];
      setText(stPose, poseLine);
      setHidden(stPose, !poseLine);

      /* Profile cards */
      paintSide(leftCard, sideState(state.available, cur.left_hand), active && run.side === 'left');
      paintSide(rightCard, sideState(state.available, cur.right_hand), active && run.side === 'right');
      setText(ownerVal, !avail ? L.unknown
        : typeof cur.name === 'string' && cur.name.trim() ? clip(cur.name, 120) : '—');
      setHidden(unavail, avail);

      /* Sequence sidebar: idle = selectable reference, active = official pose only */
      var view = active ? pose : selected;
      setText(seqCount, (view === null ? '—' : view + 1) + ' / 6');
      for (i = 0; i < POSES.length; i++) {
        var c = cards[i], isCur = active && pose === i, isSel = !active && selected === i;
        setText(c.name, POSES[i][tx]);
        setAttr(c.btn, 'aria-label', (zh ? '姿势 ' + (i + 1) + '：' : 'Pose ' + (i + 1) + ': ') + POSES[i][tx]);
        setAttr(c.btn, 'aria-pressed', active ? null : String(isSel));
        setAttr(c.btn, 'aria-disabled', active ? 'true' : null);
        setAttr(c.btn, 'aria-current', isCur ? 'step' : null);
        setAttr(c.btn, 'class', 'cg-pose' + (isCur ? ' cg-pose--current' : '') +
          (isSel ? ' cg-pose--selected' : '') + (active ? ' cg-pose--locked' : ''));
        setText(c.tag, isCur ? L.current : isSel ? L.viewing : '');
        setHidden(c.tag, !isCur && !isSel);
      }

      /* Center guidance */
      var waiting = view === null;
      setText(badge, !active ? L.reference : waiting ? L.waiting : L.official);
      setAttr(badge, 'class', 'cg-badge cg-badge--' + (!active ? 'ref' : waiting ? 'wait' : 'live'));
      setText(gCount, waiting ? '' : (view + 1) + ' / 6');
      setHidden(gCount, waiting);
      setHidden(stepsOl, waiting);
      setHidden(fingersWrap, waiting);
      if (waiting) {
        setText(gTitle, WAIT_TITLE[tx]);
      } else {
        var p = POSES[view];
        setText(gTitle, p[tx]);
        setText(stepLis[0], GUIDE[tx][0]);
        setText(stepLis[1], p.act[tx]);
        setText(stepLis[2], GUIDE[tx][1]);
        for (i = 0; i < 5; i++) {
          var r = p.roles.charAt(i);
          setText(fingers[i].name, FINGERS[tx][i]);
          setText(fingers[i].role, ROLES[tx][r]);
          setAttr(fingers[i].li, 'class', 'cg-finger cg-finger--' + r);
        }
      }

      /* Active run details (raw host values only) */
      setHidden(live, !active);
      if (active) {
        setRow(mSide, run.side === 'left' ? L.sideLeft : run.side === 'right' ? L.sideRight : '');
        setRow(mUser, plain(run.user, 120));
        setRow(mSerial, plain(run.serial, 120));
        setRow(mPhase, plain(run.phase, 200));
        setRow(mStep, formatStep(run.step));
        setRow(mEvents, count === null ? '' : String(count));
        var pr = normalizeProgress(run.progress), pct = pr === null ? null : Math.round(pr * 100);
        setAttr(prog, 'class', 'cg-progress' + (pr === null ? ' cg-progress--unknown' : ''));
        setAttr(prog, 'aria-valuenow', pr === null ? null : String(pct));
        setAttr(prog, 'aria-valuetext', pr === null ? L.progressUnknown : pct + '%');
        var tf = pr === null ? '' : 'scaleX(' + pr.toFixed(3) + ')', pm = memo(progFill);
        if (pm.tf !== tf) { pm.tf = tf; progFill.style.transform = tf; }
        setText(progText, pr === null ? L.progressUnknown : L.progress + ' ' + pct + '%');
      }

      /* Outcome: completed / error / cancelled */
      var oc = TERMINAL[key] ? key : null;
      setHidden(outcome, !oc);
      if (oc) {
        setAttr(outcome, 'class', 'cg-outcome cg-outcome--' + oc);
        setText(oTitle, STATUS[oc]);
        setText(oMsg, MSG[tx][oc]);
        var err = oc === 'error' ? plain(run.error, TEXT_MAX) : '';
        setText(oErr, err);
        setHidden(oErrWrap, !err);
        var done = oc === 'completed';
        setHidden(resWrap, !done);
        setHidden(previewBtn, !done);
        if (done && run.result !== resFor) {
          resFor = run.result;
          setText(resPre, isObj(run.result) ? boundedJson(run.result, MAX_JSON) : L.none);
        }
      }

      /* Diagnostics: serialized only while expanded and only when event/count changes */
      var ev = isObj(run.event) && count !== 0 &&
        !(run.event === staleEvent && count === staleCount) ? run.event : null;
      setAttr(diagBtn, 'aria-expanded', diagOpen ? 'true' : 'false');
      setHidden(diagPre, !diagOpen);
      if (diagOpen && (ev !== diagFor || count !== diagCount)) {
        diagFor = ev;
        diagCount = count;
        setText(diagPre, ev ? boundedJson(ev, MAX_JSON) : L.noDiag);
      }
    }

    function onClick(e) {
      if (destroyed) return;
      var b = e.target && e.target.closest ? e.target.closest('button') : null;
      if (!b || !root.contains(b)) return;
      if (b === previewBtn) {
        if (curKey === 'completed' && typeof opts.onPreview === 'function') opts.onPreview();
      } else if (b === diagBtn) {
        diagOpen = !diagOpen;
        update();
      } else if (b.hasAttribute('data-index') && !curActive) {
        selected = Number(b.getAttribute('data-index'));
        update();
      }
    }

    root.addEventListener('click', onClick);
    root.querySelectorAll('[aria-label]').forEach(function(e){var v=e.getAttribute('aria-label');if(labelPairs[v])e.setAttribute('data-cg-aria',v);});
    host.appendChild(root);
    update();

    return {
      render: function (next) {
        if (destroyed) return;
        state = isObj(next) ? next : {};
        update();
      },
      destroy: function () {
        if (destroyed) return;
        destroyed = true;
        root.removeEventListener('click', onClick);
        if (root.parentNode) root.parentNode.removeChild(root);
        state = {};
      }
    };
  }

  global.CalibrationGuide = {
    mount: mount,
    helpers: {
      boundedJson: boundedJson,
      formatStep: formatStep,
      normalizeProgress: normalizeProgress,
      normalizePoseIndex: normalizePoseIndex,
      statusKey: statusKey,
      isActive: isActive,
      sideState: sideState,
      MAX_JSON: MAX_JSON,
      POSE_COUNT: POSES.length
    }
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
