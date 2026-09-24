/* workbench_sheet.js — window.HandWorkbenchSheet: native <dialog> settings sheet. No deps, timers, innerHTML or key handlers. */
(function (win) {
  'use strict';
  var doc = win.document;
  var SVG_NS = 'http://www.w3.org/2000/svg';

  function fail(Ctor, msg, name) { var e = new Ctor('HandWorkbenchSheet: ' + msg); if (name) e.name = name; throw e; }
  function filled(v) { return typeof v === 'string' && v.trim() !== ''; }
  function el(tag, cls, text) {
    var n = doc.createElement(tag);
    n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function create(options) {
    var o = options || {};
    var id = o.id, title = o.title;
    var description = o.description == null ? '' : o.description;
    var triggerLabel = o.triggerLabel == null ? title : o.triggerLabel;
    if (typeof doc.createElement('dialog').showModal !== 'function') {
      fail(Error, 'native <dialog>.showModal() is not supported by this browser; no modal was created.', 'NotSupportedError');
    }
    if (!doc.body) fail(Error, 'document.body is missing; call create() after DOMContentLoaded.', 'InvalidStateError');
    if (typeof id !== 'string' || !/^\S+$/.test(id)) fail(TypeError, 'id must be a non-empty string without spaces.');
    if (!filled(title) || !filled(triggerLabel)) fail(TypeError, 'title and triggerLabel must be non-empty strings.');
    if (typeof description !== 'string') fail(TypeError, 'description must be a string.');
    var titleId = id + '-title', descId = id + '-desc';
    [id, titleId, descId].forEach(function (x) {
      if (doc.getElementById(x)) fail(Error, 'id "' + x + '" is already in use.', 'InvalidStateError');
    });

    var returnTo = null, focusAtOpen = null, downOutside = false, destroyed = false;
    var dialog = el('dialog', 'hws-sheet'), head = el('div', 'hws-head'), body = el('div', 'hws-body');
    var titleEl = el('h2', 'hws-title', title), descEl = el('p', 'hws-desc');
    var closeBtn = el('button', 'hws-close'), trigger = el('button', 'hws-trigger', triggerLabel);
    var svg = doc.createElementNS(SVG_NS, 'svg'), path = doc.createElementNS(SVG_NS, 'path');
    dialog.id = id; titleEl.id = titleId; descEl.id = descId;
    dialog.setAttribute('aria-labelledby', titleId);
    closeBtn.type = trigger.type = 'button'; // no form wrapper; never a submitter
    closeBtn.setAttribute('aria-label', 'Close');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    path.setAttribute('d', 'M4 4l8 8M12 4l-8 8');
    closeBtn.appendChild(svg).appendChild(path);
    head.append(titleEl, descEl, closeBtn);
    dialog.append(head, body);
    setDescription(description);
    var listeners = [ // [target, type, handler, capture]: bound below, unbound by destroy()
      [trigger, 'click', onTrigger], [closeBtn, 'click', close], [dialog, 'click', onClick],
      [dialog, 'pointerdown', onPointerDown, true], [dialog, 'close', onClose]
    ];

    function bind(method) { listeners.forEach(function (l) { l[0][method](l[1], l[2], l[3] === true); }); }
    function setDescription(text) {
      var has = text.trim() !== '';
      descEl.textContent = has ? text : '';
      descEl.hidden = !has;
      dialog[has ? 'setAttribute' : 'removeAttribute']('aria-describedby', descId);
    }
    function outside(e) {
      var r = dialog.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    }
    // Capture phase: every press is seen even if a moved control stops propagation.
    function onPointerDown(e) { downOutside = e.button === 0 && e.target === dialog && outside(e); }
    function onClick(e) { // close only when press AND release both land on the backdrop
      var hit = downOutside && e.target === dialog && outside(e);
      downOutside = false;
      if (hit) dialog.close();
    }
    function restoreFocus() {
      var t = returnTo;
      returnTo = null;
      if (t && t !== doc.body && t.isConnected && typeof t.focus === 'function' && !t.matches(':disabled')) t.focus();
    }
    function onClose() { // native 'close': Escape/cancel, close button, backdrop, element.close()
      if (dialog.open) return; // reopened before this queued event ran
      var a = doc.activeElement; // keep focus that caller code deliberately moved elsewhere
      if (a && a !== doc.body && a !== focusAtOpen && !dialog.contains(a)) returnTo = null;
      else restoreFocus();
    }
    function show(from) {
      if (destroyed || dialog.open) return;
      focusAtOpen = doc.activeElement;
      returnTo = from || focusAtOpen;
      dialog.showModal();
    }
    function onTrigger() { show(trigger); }
    function close() { if (!destroyed && dialog.open) dialog.close(); }
    function setLabels(labels) {
      if (destroyed || !labels) return;
      if (filled(labels.title)) titleEl.textContent = labels.title;
      if (typeof labels.description === 'string') setDescription(labels.description);
      if (filled(labels.triggerLabel)) trigger.textContent = labels.triggerLabel;
      if (filled(labels.closeLabel)) closeBtn.setAttribute('aria-label', labels.closeLabel);
    }
    function destroy() {
      if (destroyed) return;
      var wasOpen = dialog.open;
      destroyed = true;
      bind('removeEventListener');
      if (wasOpen) dialog.close();
      dialog.remove();
      trigger.remove();
      if (wasOpen) restoreFocus();
    }

    bind('addEventListener');
    doc.body.appendChild(dialog);
    return {
      element: dialog, body: body, trigger: trigger,
      open: function () { show(null); }, close: close, setLabels: setLabels, destroy: destroy
    };
  }

  win.HandWorkbenchSheet = Object.freeze({ create: create });
}(window));
