/* pose_grid.js - dependency-free, accessible six-item calibration grid (display only).
 * window.PoseGrid.mount(host, { onSelect(index) }) -> { render(model), destroy() }
 * The main code owns calibration semantics: states are shown verbatim, never inferred.
 * No storage, network, timers or action execution. All text is written via textContent. */
(function () {
  'use strict';

  const COUNT = 6;
  const HTML_NS = 'http://www.w3.org/1999/xhtml';
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const STATES = ['pending', 'current', 'captured', 'error'];
  const ICON = { pending: '○', current: '▶', captured: '✓', error: '!' }; // shape cue besides color
  const TEXT = {
    en: {
      group: 'Calibration poses',
      state: { pending: 'Pending', current: 'Current', captured: 'Captured', error: 'Error' },
      selected: 'Selected',
      caption: 'Schematic reference, not measured',
      poses: ['Thumb–index pinch', 'Thumb–middle pinch', 'Thumb–ring pinch',
        'Thumb–little pinch', 'Four fingers bent', 'Four fingers flat']
    },
    zh: {
      group: '校准姿势',
      state: { pending: '待采集', current: '当前', captured: '已采集', error: '错误' },
      selected: '已选中',
      caption: '示意参考图，非实测',
      poses: ['拇指捏食指', '拇指捏中指', '拇指捏无名指', '拇指捏小指', '四指弯曲', '四指伸平']
    }
  };
  const BX = [44, 60, 76, 92]; // drawing-only finger base x: index, middle, ring, little
  const TY = [16, 10, 14, 22]; // drawing-only extended fingertip y

  function fail(Kind, msg) { throw new Kind('PoseGrid: ' + msg); }

  // ---- Validation runs fully before any DOM write, so a rejected render() changes nothing.
  function optIndex(v, name) {
    if (v === null || v === undefined || v === -1) return -1;
    if (!Number.isInteger(v) || v < 0 || v >= COUNT) fail(RangeError, `${name} must be 0..5, or null/-1 for none`);
    return v;
  }

  // Unknown/invalid progress stays unknown (null): never clamped, never shown as success.
  function knownProgress(p) {
    return typeof p === 'number' && Number.isFinite(p) && p >= 0 && p <= 1 ? p : null;
  }

  function validate(model) {
    if (!model || typeof model !== 'object') fail(TypeError, 'render() expects a model object');
    const lang = model.language;
    if (lang !== 'zh' && lang !== 'en') fail(RangeError, "language must be 'zh' or 'en'");
    const src = model.items;
    if (!Array.isArray(src) || src.length !== COUNT) fail(RangeError, 'items must be an array of exactly 6');
    const items = [];
    for (let i = 0; i < COUNT; i++) {
      const it = src[i];
      if (!it || typeof it !== 'object') fail(TypeError, `items[${i}] must be an object`);
      const { title, instruction, state, progress } = it;
      if (typeof title !== 'string' || !title.trim()) fail(TypeError, `items[${i}].title must be a non-empty string`);
      if (typeof instruction !== 'string') fail(TypeError, `items[${i}].instruction must be a string`);
      if (STATES.indexOf(state) < 0) fail(RangeError, `items[${i}].state must be one of ${STATES.join('/')}`);
      items.push({ title, instruction, state, progress: knownProgress(progress) });
    }
    return {
      lang,
      items,
      selected: optIndex(model.selectedIndex, 'selectedIndex'),
      current: optIndex(model.currentIndex, 'currentIndex')
    };
  }

  // ---- DOM helpers: write only on change, so repeated renders keep nodes and focus.
  function setText(node, value) { if (node.textContent !== value) node.textContent = value; }
  function setAttr(node, name, value) {
    if (value === null) node.removeAttribute(name);
    else if (node.getAttribute(name) !== value) node.setAttribute(name, value);
  }
  function show(node, on) { if (node.hidden !== !on) node.hidden = !on; }
  function span(cls, parent) {
    const n = document.createElement('span');
    n.className = cls;
    parent.appendChild(n);
    return n;
  }
  function svgEl(tag, attrs, parent) {
    const n = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs).forEach((k) => n.setAttribute(k, String(attrs[k])));
    if (parent) parent.appendChild(n);
    return n;
  }

  // Abstract fingertip markers: a schematic reference drawing, never a measurement.
  // kind 0-3: thumb pinches index/middle/ring/little; 4: four fingers bent; 5: four fingers flat.
  function diagram(kind) {
    const s = svgEl('svg', { class: 'pg-svg', viewBox: '0 0 120 80', 'aria-hidden': 'true' });
    svgEl('rect', { class: 'pg-frame', x: 1, y: 1, width: 118, height: 78, rx: 6 }, s);
    svgEl('rect', { class: 'pg-palm', x: 36, y: 52, width: 64, height: 22, rx: 8 }, s);
    const ray = (d, hi) => svgEl('path', { class: hi ? 'pg-ray pg-hi' : 'pg-ray', d }, s);
    const tip = (x, y, hi) => svgEl('circle', { class: hi ? 'pg-tip pg-hi' : 'pg-tip', cx: x, cy: y, r: 3.6 }, s);
    for (let f = 0; f < 4; f++) {
      const x = BX[f];
      if (kind === 4) { ray(`M${x} 52V38Q${x} 30 ${x + 5} 35`, true); tip(x + 5, 35, true); }
      else if (kind === f) { ray(`M${x} 52Q${x} 40 ${x - 6} 36`, true); tip(x - 6, 36, true); }
      else { ray(`M${x} 52V${TY[f]}`, kind === 5); tip(x, TY[f], kind === 5); }
    }
    if (kind < 4) { // thumb tip meets the target fingertip; dashed ring marks the contact
      const x = BX[kind] - 6;
      ray(`M36 64Q${x} 64 ${x} 44`, true);
      tip(x, 44, true);
      svgEl('circle', { class: 'pg-contact', cx: x, cy: 40, r: 7 }, s);
    } else {
      ray('M36 64L20 46', false);
      tip(20, 46, false);
    }
    return s;
  }

  function buildCell(k, uid) {
    const btn = document.createElement('button');
    btn.type = 'button'; // never submits a surrounding form
    btn.className = 'pg-cell';
    btn.tabIndex = k === 0 ? 0 : -1; // roving tab stop: one Tab stop for the whole grid
    const head = span('pg-head', btn);
    const num = span('pg-num', head);
    num.textContent = String(k + 1);
    num.setAttribute('aria-hidden', 'true');
    const badge = span('pg-state', head);
    const icon = span('pg-icon', badge);
    icon.setAttribute('aria-hidden', 'true');
    const state = span('pg-state-text', badge);
    const sel = span('pg-sel', head);
    sel.setAttribute('aria-hidden', 'true'); // aria-pressed already announces selection
    sel.hidden = true;
    const title = span('pg-title', btn);
    const fig = span('pg-fig', btn);
    fig.appendChild(diagram(k));
    const cap = span('pg-cap', fig);
    const inst = span('pg-inst', btn);
    const prog = span('pg-prog', btn);
    prog.hidden = true;
    const bar = span('pg-bar', prog);
    bar.setAttribute('aria-hidden', 'true');
    const fill = span('pg-fill', bar);
    const pct = span('pg-pct', prog);
    [[title, 't'], [state, 's'], [inst, 'i'], [pct, 'p'], [cap, 'c']].forEach(([n, p]) => { n.id = `${uid}-${k}-${p}`; });
    btn.setAttribute('aria-labelledby', `${title.id} ${state.id}`);
    return { btn, icon, state, sel, title, cap, inst, prog, fill, pct };
  }

  const mounted = new WeakSet();
  let seq = 0;

  function mount(host, options) {
    if (!host || host.nodeType !== 1 || host.namespaceURI !== HTML_NS) fail(TypeError, 'mount() host must be an HTML element');
    if (mounted.has(host)) fail(Error, 'mount() host already has a grid; destroy() it first');
    if (options != null && typeof options !== 'object') fail(TypeError, 'mount() options must be an object');
    const onSelect = options ? options.onSelect : undefined;
    if (onSelect !== undefined && typeof onSelect !== 'function') fail(TypeError, 'onSelect must be a function');

    const uid = `posegrid-${++seq}`;
    const root = document.createElement('div');
    root.className = 'pg-grid';
    root.setAttribute('role', 'group');
    root.hidden = true; // revealed by the first valid render()
    const cells = [];
    for (let k = 0; k < COUNT; k++) {
      cells.push(buildCell(k, uid));
      root.appendChild(cells[k].btn);
    }
    let tabStop = 0;
    let destroyed = false;

    const indexOf = (node) => {
      const btn = node && node.closest ? node.closest('.pg-cell') : null;
      return cells.findIndex((c) => c.btn === btn);
    };
    const setTabStop = (k) => {
      tabStop = k;
      cells.forEach((c, j) => { c.btn.tabIndex = j === k ? 0 : -1; });
    };
    const columns = () => { // 3, 2 or 1 - read from the live layout
      const top = cells[0].btn.offsetTop;
      let n = 0;
      while (n < COUNT && cells[n].btn.offsetTop === top) n++;
      return Math.max(1, n);
    };
    const focusInside = () => {
      const active = root.getRootNode().activeElement; // documents and shadow roots
      return !!active && root.contains(active);
    };

    function onClick(e) {
      const k = indexOf(e.target);
      if (k < 0) return;
      setTabStop(k);
      if (onSelect) onSelect(k); // report only: the caller decides and re-renders
    }
    function onFocusIn(e) {
      const k = indexOf(e.target);
      if (k >= 0 && k !== tabStop) setTabStop(k);
    }
    function onKeyDown(e) {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const k = indexOf(e.target);
      if (k < 0) return;
      let n;
      switch (e.key) {
        case 'ArrowRight': n = k + 1; break;
        case 'ArrowLeft': n = k - 1; break;
        case 'ArrowDown': n = k + columns(); break;
        case 'ArrowUp': n = k - columns(); break;
        case 'Home': n = 0; break;
        case 'End': n = COUNT - 1; break;
        default: return;
      }
      e.preventDefault();
      if (n >= 0 && n < COUNT && n !== k) { setTabStop(n); cells[n].btn.focus(); }
    }
    root.addEventListener('click', onClick);
    root.addEventListener('focusin', onFocusIn);
    root.addEventListener('keydown', onKeyDown);

    function apply(v) {
      const t = TEXT[v.lang];
      setAttr(root, 'lang', v.lang === 'zh' ? 'zh-CN' : 'en');
      setAttr(root, 'aria-label', t.group);
      v.items.forEach((it, k) => {
        const c = cells[k];
        // Caller's state verbatim. Bar only for state 'current' with a known progress value.
        const hasBar = it.state === 'current' && it.progress !== null;
        setAttr(c.btn, 'data-state', it.state);
        setAttr(c.btn, 'aria-pressed', k === v.selected ? 'true' : 'false');
        setAttr(c.btn, 'aria-current', k === v.current ? 'step' : null);
        setText(c.icon, ICON[it.state]);
        setText(c.state, t.state[it.state]);
        setText(c.sel, t.selected);
        setText(c.title, it.title);
        setText(c.cap, `${t.caption} · ${t.poses[k]}`);
        setText(c.inst, it.instruction);
        show(c.sel, k === v.selected);
        show(c.prog, hasBar);
        // Floor (plus float epsilon): 0.996 reads 99%, never a rounded-up 100%.
        setText(c.pct, hasBar ? `${Math.floor(it.progress * 100 + 1e-9)}%` : '');
        c.fill.style.width = hasBar ? `${it.progress * 100}%` : '0%';
        setAttr(c.btn, 'aria-describedby',
          (hasBar ? [c.inst.id, c.pct.id, c.cap.id] : [c.inst.id, c.cap.id]).join(' '));
      });
      if (!focusInside()) { // re-point the Tab stop only when it cannot disturb focus
        const pref = v.selected >= 0 ? v.selected : v.current;
        if (pref >= 0) setTabStop(pref);
      }
      show(root, true);
    }

    host.appendChild(root);
    mounted.add(host);

    return Object.freeze({
      render(model) {
        if (destroyed) fail(Error, 'render() called after destroy()');
        apply(validate(model));
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        root.removeEventListener('click', onClick);
        root.removeEventListener('focusin', onFocusIn);
        root.removeEventListener('keydown', onKeyDown);
        root.remove();
        mounted.delete(host);
      }
    });
  }

  window.PoseGrid = Object.freeze({ mount });
})();
