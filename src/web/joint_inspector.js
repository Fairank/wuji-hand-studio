/* joint_inspector.js - display-only joint reference inspector (no dependencies).
 * Never fetches, stores, polls, drives hardware or executes code; the only network activity is the
 * browser loading the caller-supplied, validated image URL. Every label is written via textContent.
 * API: window.JointInspector.mount(host, { onSelect(index) }) -> { render(model), destroy() } */
(() => {
  'use strict';

  const FINGERS = 5;
  const AXES = 4;
  const COUNT = FINGERS * AXES; // finger-major layout from the contract: index = finger * AXES + axis
  const FIELDS = ['fingerLabel', 'jointLabel', 'movement', 'modelName', 'channelLabel', 'nodeLabel', 'rangeLabel', 'valueLabel'];
  const ROWS = [['channel', 'channelLabel'], ['node', 'nodeLabel'], ['model', ''], ['range', 'rangeLabel'], ['value', 'valueLabel']];
  const TEXT = {
    en: {
      region: 'Joint reference', fingers: 'Finger', joints: 'Joint', channel: 'Model index', node: 'Node mapping',
      model: 'Model joint', range: 'Model range', value: 'Current feedback', tech: 'Technical name',
      noImg: 'No reference image', badImg: 'Reference image unavailable', invalid: 'Invalid joint data. Nothing is shown.'
    },
    zh: {
      region: '关节图解', fingers: '手指', joints: '关节', channel: '模型索引', node: '节点映射',
      model: '模型关节', range: '模型范围', value: '当前反馈', tech: '技术名称',
      noImg: '无参考图', badImg: '参考图无法显示', invalid: '关节数据无效，未显示内容。'
    }
  };
  const PNG_RE = /^data:image\/png;base64,[A-Za-z0-9+\/]+={0,2}$/;
  const API_RE = /^\/api\/[A-Za-z0-9\-._~%!$&'()*+,;=:@\/?]*$/;
  const DOT_SEGMENT_RE = /(^|\/)(\.|%2e){1,2}(\/|\?|$)/i;

  const isText = (v) => typeof v === 'string';
  const filled = (v) => isText(v) && v.trim() !== '';
  const isLang = (v) => v === 'zh' || v === 'en';

  function imageOk(url) {
    if (url === '' || PNG_RE.test(url)) return true;
    if (!API_RE.test(url) || DOT_SEGMENT_RE.test(url) || url.includes('//')) return false;
    try {
      return new URL(url, document.baseURI).origin === location.origin; // also rejects a foreign <base href>
    } catch (e) {
      return false;
    }
  }

  function problem(m) {
    if (!m || typeof m !== 'object') return 'model must be an object';
    if (!isLang(m.language)) return 'language must be "zh" or "en"';
    if (!isText(m.profileLabel) || !isText(m.imageUrl) || !isText(m.imageAlt)) return 'profileLabel, imageUrl and imageAlt must be strings';
    if (!filled(m.sourceNote)) return 'sourceNote must be a non-empty string';
    if (!imageOk(m.imageUrl)) return 'imageUrl must be empty, a same-origin /api/... path or a data:image/png;base64 URL';
    if (m.imageUrl && !filled(m.imageAlt)) return 'imageAlt must be non-empty when imageUrl is set';
    if (!Number.isInteger(m.selectedIndex) || m.selectedIndex < 0 || m.selectedIndex >= COUNT) return 'selectedIndex must be an integer 0..19';
    if (!Array.isArray(m.items) || m.items.length !== COUNT) return 'items must contain exactly 20 entries';
    for (let i = 0; i < COUNT; i++) {
      const it = m.items[i];
      if (!it || typeof it !== 'object' || it.index !== i) return `items[${i}].index must be ${i}`;
      const bad = FIELDS.find((f) => !filled(it[f]));
      if (bad) return `items[${i}].${bad} must be a non-empty string`;
      const first = i - (i % AXES);
      if (it.fingerLabel !== m.items[first].fingerLabel) return `items[${i}].fingerLabel differs from items[${first}]`;
    }
    return '';
  }

  function el(tag, cls, parent) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (parent) parent.appendChild(node);
    return node;
  }

  function put(node, text) {
    if (node.textContent !== text) node.textContent = text; // skip no-op writes
  }

  function toolbar(parent, cls, count, onPick) {
    const bar = el('div', `ji-bar ${cls}`, parent);
    bar.setAttribute('role', 'toolbar');
    for (let i = 0; i < count; i++) {
      const button = el('button', 'ji-btn', bar);
      button.type = 'button';
      button.addEventListener('click', () => onPick(i));
    }
    bar.addEventListener('keydown', (e) => {
      const buttons = Array.from(bar.children);
      const i = buttons.indexOf(e.target);
      const moves = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: count - 1 };
      const to = moves[e.key];
      if (i < 0 || typeof to !== 'number' || e.altKey || e.ctrlKey || e.metaKey) return;
      e.preventDefault(); // arrows move focus only; Enter/Space (native click) selects
      const next = buttons[(to + count) % count];
      buttons.forEach((b) => { b.tabIndex = b === next ? 0 : -1; });
      next.focus();
    });
    return bar;
  }

  // Updates buttons in place, so a focused button keeps focus and stays the tab stop across renders.
  function sync(bar, pressed, labels) {
    const buttons = Array.from(bar.children);
    const focused = buttons.indexOf(bar.getRootNode().activeElement);
    const stop = focused >= 0 ? focused : pressed;
    buttons.forEach((b, i) => {
      put(b, labels[i]);
      b.setAttribute('aria-pressed', String(i === pressed));
      b.tabIndex = i === stop ? 0 : -1;
    });
  }

  function mount(host, opts) {
    if (!host || host.nodeType !== 1) throw new TypeError('JointInspector.mount: host must be an element');
    const onSelect = opts && opts.onSelect;
    if (typeof onSelect !== 'function') throw new TypeError('JointInspector.mount: opts.onSelect must be a function');

    let sel = -1; // selectedIndex of the last valid render; -1 disables selection
    let lang = 'en';
    let url = '';
    let imgFailed = false;
    let dead = false;
    const pick = (index) => { if (!dead && sel >= 0) onSelect(index); }; // no local state change

    const root = el('div', 'ji', host);
    root.setAttribute('role', 'group');
    const status = el('p', 'ji-bad', root);
    status.setAttribute('role', 'status');
    const body = el('div', 'ji-body', root);
    body.hidden = true;
    const profile = el('p', 'ji-profile', body);
    const selectors = el('div', 'ji-sel', body);
    const fingers = toolbar(selectors, 'ji-fingers', FINGERS, (f) => pick(f * AXES + (sel % AXES)));
    const joints = toolbar(selectors, 'ji-joints', AXES, (a) => pick(sel - (sel % AXES) + a));
    const figure = el('figure', 'ji-fig', body);
    const frame = el('div', 'ji-frame', figure);
    const img = el('img', 'ji-img', frame);
    const empty = el('p', 'ji-empty', frame);
    const note = el('figcaption', 'ji-note', figure);
    const text = el('div', 'ji-text', body);
    const title = el('h3', 'ji-title', text);
    const titleFinger = el('span');
    const titleJoint = el('span');
    const dot = el('span', 'ji-sep');
    dot.textContent = '·';
    dot.setAttribute('aria-hidden', 'true');
    title.append(titleFinger, ' ', dot, ' ', titleJoint);
    const movement = el('p', 'ji-move', text);
    body.insertBefore(text, selectors);
    const dl = el('dl', 'ji-dl', body);
    const rows = ROWS.map(([key, field]) => ({ key, field, dt: el('dt', '', dl), dd: el('dd', '', dl) }));
    const details = el('details', 'ji-tech', rows.find((r) => r.key === 'model').dd); // native, starts collapsed
    const summary = el('summary', '', details);
    const code = el('code', '', details);

    img.hidden = true;
    img.decoding = 'async';
    img.addEventListener('error', () => { if (url) { imgFailed = true; paintImage(); } });
    img.addEventListener('load', () => { imgFailed = false; paintImage(); });

    function paintImage() {
      img.hidden = !url || imgFailed;
      empty.hidden = !img.hidden;
      put(empty, TEXT[lang][url ? 'badImg' : 'noImg']);
    }

    function applyLang(next) {
      lang = next;
      root.lang = lang === 'zh' ? 'zh-Hans' : 'en';
      root.setAttribute('aria-label', TEXT[lang].region);
    }

    function render(model) {
      if (dead) return;
      const error = problem(model);
      if (error) {
        sel = -1; // hide everything rather than leave stale values (e.g. old feedback) on screen
        applyLang(model && isLang(model.language) ? model.language : lang);
        body.hidden = true;
        put(status, TEXT[lang].invalid);
        throw new TypeError(`JointInspector.render: ${error}`);
      }
      const items = model.items;
      const item = items[model.selectedIndex];
      const finger = Math.floor(model.selectedIndex / AXES);
      const t = TEXT[model.language];
      sel = model.selectedIndex;
      applyLang(model.language);
      put(status, '');
      body.hidden = false;
      put(profile, model.profileLabel);
      profile.hidden = !model.profileLabel.trim();
      fingers.setAttribute('aria-label', t.fingers);
      joints.setAttribute('aria-label', t.joints);
      sync(fingers, finger, Array.from({ length: FINGERS }, (_, f) => items[f * AXES].fingerLabel));
      sync(joints, sel % AXES, Array.from({ length: AXES }, (_, a) => items[finger * AXES + a].jointLabel));
      if (model.imageUrl !== url) { // touch src only when the URL actually changes
        url = model.imageUrl;
        imgFailed = false;
        if (url) img.src = url;
        else img.removeAttribute('src');
      }
      if (img.alt !== model.imageAlt) img.alt = model.imageAlt;
      paintImage();
      put(note, model.sourceNote);
      put(titleFinger, item.fingerLabel);
      put(titleJoint, item.jointLabel);
      put(movement, item.movement);
      rows.forEach((r) => {
        put(r.dt, t[r.key]);
        if (r.field) put(r.dd, item[r.field]);
      });
      put(summary, t.tech);
      put(code, item.modelName);
    }

    function destroy() {
      if (dead) return;
      dead = true;
      img.removeAttribute('src');
      root.remove();
    }

    return Object.freeze({ render, destroy });
  }

  window.JointInspector = Object.freeze({ mount });
})();
