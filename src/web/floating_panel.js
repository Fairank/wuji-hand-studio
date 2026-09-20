/* floating_panel.js — classic script. Exposes window.FloatingPanel = { create, clampRect }.
   UI-only helper: moves one existing element between its home slot and a fixed overlay. */
(function (global) {
  'use strict';

  var LANG_KEY = 'wuji-language';
  var STEP = 10;
  var SAVE_DELAY = 300;
  var STYLE_PROPS = ['left', 'top', 'width', 'height', 'minWidth', 'minHeight', 'maxWidth', 'maxHeight'];
  var ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  var CAPTIONS = {
    zh: { panel: '面板', popout: '弹出', dock: '停靠', hint: '拖动标题栏移动；Alt+方向键每次移动 10 像素；Esc 停靠' },
    en: { panel: 'Panel', popout: 'Pop out', dock: 'Dock', hint: 'Drag the title bar to move; Alt+arrow keys move 10px; Esc docks' }
  };

  function num(value, fallback) {
    return typeof value === 'number' && isFinite(value) ? value : fallback;
  }

  /* Pure geometry. Returns an integer rect inside the viewport. Minimum sizes shrink
     when the viewport is smaller than they are; non-finite inputs fall back safely. */
  function clampRect(rect, viewport, opts) {
    rect = rect || {};
    viewport = viewport || {};
    opts = opts || {};
    var vw = Math.max(0, num(viewport.width, 0));
    var vh = Math.max(0, num(viewport.height, 0));
    var margin = Math.max(0, num(opts.margin, 0));
    var mx = Math.min(margin, vw / 2);
    var my = Math.min(margin, vh / 2);
    var availW = vw - 2 * mx;
    var availH = vh - 2 * my;
    var minW = Math.min(Math.max(0, num(opts.minWidth, 240)), availW);
    var minH = Math.min(Math.max(0, num(opts.minHeight, 120)), availH);
    var width = Math.min(Math.max(num(rect.width, minW), minW), availW);
    var height = Math.min(Math.max(num(rect.height, minH), minH), availH);
    var x = Math.min(Math.max(num(rect.x, mx), mx), mx + availW - width);
    var y = Math.min(Math.max(num(rect.y, my), my), my + availH - height);
    return { x: Math.floor(x), y: Math.floor(y), width: Math.floor(width), height: Math.floor(height) };
  }

  function create(options) {
    options = options || {};
    var panel = options.panel;
    var home = options.home || (panel && panel.parentNode);
    if (!panel || !home) throw new TypeError('FloatingPanel.create needs { panel, home }');

    var doc = panel.ownerDocument || global.document;
    var win = (doc && doc.defaultView) || global;
    var title = options.title;
    var storageKey = options.storageKey ? String(options.storageKey) : '';
    var onChange = typeof options.onChange === 'function' ? options.onChange : null;
    var limits = {
      minWidth: num(options.minWidth, 240),
      minHeight: num(options.minHeight, 120),
      margin: Math.max(0, num(options.margin, 0))
    };
    var floating = false, destroyed = false, drag = null, saveTimer = 0;
    var rect = null;    // rect currently applied (always clamped)
    var intent = null;  // last rect the user asked for; re-clamped when the viewport changes
    var savedStyle = null;
    var lang = readLang();
    var addedClass = !panel.classList.contains('float-panel');
    var placeholder = doc.createComment('float-panel-home');
    var observer = typeof win.ResizeObserver === 'function' ? new win.ResizeObserver(syncSize) : null;

    var bar = doc.createElement('div');
    var label = doc.createElement('span');
    var button = doc.createElement('button');
    bar.className = 'float-panel-bar';
    bar.setAttribute('role', 'group');
    label.className = 'float-panel-title';
    button.type = 'button';
    button.className = 'float-panel-toggle';
    bar.appendChild(label);
    bar.appendChild(button);
    panel.insertBefore(bar, panel.firstChild);
    panel.classList.add('float-panel');

    var api = {
      float: floatPanel,
      dock: dockPanel,
      toggle: function () { return floating ? dockPanel() : floatPanel(); },
      destroy: destroy,
      isFloating: function () { return floating; }
    };

    var wiring = [
      [bar, 'pointerdown', onDragStart], [bar, 'pointermove', onDragMove], [bar, 'pointerup', endDrag],
      [bar, 'pointercancel', endDrag], [bar, 'lostpointercapture', endDrag], [bar, 'keydown', onKeyDown],
      [button, 'click', api.toggle], [win, 'resize', onViewport], [win, 'wuji-language', onLanguage]
    ];
    if (!observer) wiring.push([panel, 'pointerup', syncSize]); // size sync fallback
    wiring.forEach(function (w) { w[0].addEventListener(w[1], w[2]); });

    function readLang() {
      try { return win.localStorage.getItem(LANG_KEY) === 'en' ? 'en' : 'zh'; } catch (err) { return 'zh'; }
    }

    function readStored() {
      if (!storageKey) return {};
      try {
        var value = JSON.parse(win.localStorage.getItem(storageKey));
        return value && typeof value === 'object' ? value : {};
      } catch (err) { return {}; }
    }

    function saveNow() {
      if (saveTimer) { win.clearTimeout(saveTimer); saveTimer = 0; }
      if (!storageKey || !intent) return;
      try {
        win.localStorage.setItem(storageKey, JSON.stringify({ x: intent.x, y: intent.y, width: intent.width, height: intent.height }));
      } catch (err) { /* storage blocked or full: keep working without persistence */ }
    }

    function scheduleSave() {
      if (saveTimer) win.clearTimeout(saveTimer);
      saveTimer = win.setTimeout(saveNow, SAVE_DELAY);
    }

    function emit(type) {
      if (!onChange) return;
      try {
        onChange({ type: type, floating: floating, rect: rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null });
      } catch (err) {
        if (global.console && typeof global.console.error === 'function') global.console.error(err);
      }
    }

    function render() {
      var c = CAPTIONS[lang];
      var name = title && typeof title === 'object' ? (title[lang] || title.zh || title.en || c.panel) : (title ? String(title) : c.panel);
      label.textContent = name;
      button.textContent = floating ? c.dock : c.popout;
      bar.setAttribute('aria-label', name);
      bar.tabIndex = floating ? 0 : -1;
      if (floating) bar.setAttribute('title', c.hint); else bar.removeAttribute('title');
      panel.setAttribute('data-floating', floating ? 'true' : 'false');
    }

    function viewport() {
      var el = doc.documentElement;
      return { width: (el && el.clientWidth) || win.innerWidth || 0, height: (el && el.clientHeight) || win.innerHeight || 0 };
    }

    function place(next, keepIntent) {
      var vp = viewport();
      var smallest = clampRect({ x: 0, y: 0, width: 0, height: 0 }, vp, limits); // effective minimums
      var style = panel.style;
      rect = clampRect(next, vp, limits);
      if (!keepIntent) intent = rect;
      style.left = rect.x + 'px';
      style.top = rect.y + 'px';
      style.width = rect.width + 'px';
      style.height = rect.height + 'px';
      style.minWidth = smallest.width + 'px';
      style.minHeight = smallest.height + 'px';
      // keeps the native CSS resize handle inside the viewport
      style.maxWidth = Math.max(rect.width, vp.width - limits.margin - rect.x) + 'px';
      style.maxHeight = Math.max(rect.height, vp.height - limits.margin - rect.y) + 'px';
    }

    function isConnected(node) {
      if (!node || !node.parentNode) return false;
      return typeof node.isConnected === 'boolean' ? node.isConnected : !!(doc.contains && doc.contains(node));
    }

    /* Moves the same node (listeners intact) and restores focus lost by the move. */
    function moveTo(parent, before) {
      var active = doc.activeElement;
      var hadFocus = active && active !== doc.body && panel.contains(active) ? active : null;
      parent.insertBefore(panel, before || null);
      if (!hadFocus) return;
      var target = hadFocus === bar && !floating ? button : hadFocus;
      try { target.focus({ preventScroll: true }); } catch (err) { /* not focusable any more */ }
    }

    function floatPanel() {
      if (destroyed || floating) return api;
      var box = panel.getBoundingClientRect();
      var stored = readStored();
      var initial = options.initialRect || {};
      if (panel.parentNode) panel.parentNode.insertBefore(placeholder, panel);
      savedStyle = {};
      STYLE_PROPS.forEach(function (prop) { savedStyle[prop] = panel.style[prop] || ''; });
      floating = true;
      render();
      moveTo(doc.body, null);
      intent = {
        x: num(stored.x, num(initial.x, box.left)), y: num(stored.y, num(initial.y, box.top)),
        width: num(stored.width, num(initial.width, box.width)), height: num(stored.height, num(initial.height, box.height))
      };
      place(intent, true);
      if (observer) observer.observe(panel);
      emit('float');
      return api;
    }

    function dockPanel() {
      if (destroyed || !floating) return api;
      endDrag();
      if (saveTimer) saveNow();
      if (observer) observer.unobserve(panel);
      floating = false;
      STYLE_PROPS.forEach(function (prop) { panel.style[prop] = savedStyle[prop]; });
      savedStyle = null;
      render();
      var slot = isConnected(placeholder) ? placeholder.parentNode : null;
      if (slot) moveTo(slot, placeholder); else moveTo(home, null);
      if (placeholder.parentNode) placeholder.parentNode.removeChild(placeholder);
      rect = null;
      emit('dock');
      return api;
    }

    function onDragStart(e) {
      if (!floating || drag || e.isPrimary === false || e.button > 0) return;
      if (e.target && e.target.closest && e.target.closest('button')) return;
      var box = panel.getBoundingClientRect();
      drag = { id: e.pointerId, dx: e.clientX - box.left, dy: e.clientY - box.top, moved: false };
      try { bar.setPointerCapture(e.pointerId); } catch (err) { /* synthetic or stale pointer */ }
      panel.setAttribute('data-dragging', 'true');
    }

    function onDragMove(e) {
      if (!drag || e.pointerId !== drag.id) return;
      drag.moved = true;
      place({ x: e.clientX - drag.dx, y: e.clientY - drag.dy, width: rect.width, height: rect.height });
    }

    function endDrag(e) {
      if (!drag || (e && e.pointerId !== drag.id)) return;
      var id = drag.id, moved = drag.moved;
      drag = null;
      panel.removeAttribute('data-dragging');
      try { if (bar.hasPointerCapture && bar.hasPointerCapture(id)) bar.releasePointerCapture(id); } catch (err) { /* already released */ }
      if (moved) { saveNow(); emit('move'); }
    }

    function syncSize() {
      if (!floating || drag || !rect) return;
      var box = panel.getBoundingClientRect();
      if (Math.abs(box.width - rect.width) < 1 && Math.abs(box.height - rect.height) < 1) return;
      place({ x: rect.x, y: rect.y, width: box.width, height: box.height });
      scheduleSave();
      emit('resize');
    }

    function onKeyDown(e) {
      if (!floating || e.target !== bar) return;
      if (e.key === 'Escape') { e.preventDefault(); dockPanel(); return; }
      var dir = e.altKey && Object.prototype.hasOwnProperty.call(ARROWS, e.key) ? ARROWS[e.key] : null;
      if (!dir) return;
      e.preventDefault();
      place({ x: rect.x + dir[0] * STEP, y: rect.y + dir[1] * STEP, width: rect.width, height: rect.height });
      scheduleSave();
      emit('move');
    }

    function onViewport() {
      if (!floating) return;
      place(intent, true); // re-clamp without overwriting what the user chose
      emit('viewport');
    }

    function onLanguage(e) {
      var next = e && e.detail && e.detail.lang;
      if (next !== 'zh' && next !== 'en') return;
      lang = next;
      render();
    }

    function destroy() {
      if (destroyed) return;
      dockPanel();
      destroyed = true;
      if (saveTimer) { win.clearTimeout(saveTimer); saveTimer = 0; }
      if (observer) observer.disconnect();
      wiring
        .forEach(function (w) { w[0].removeEventListener(w[1], w[2]); });
      if (bar.parentNode) bar.parentNode.removeChild(bar);
      if (addedClass) panel.classList.remove('float-panel');
      panel.removeAttribute('data-floating');
    }

    render();
    return api;
  }

  global.FloatingPanel = { create: create, clampRect: clampRect };
})(typeof window !== 'undefined' ? window : this);
