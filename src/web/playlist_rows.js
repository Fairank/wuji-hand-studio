/*
 * playlist_rows.js - editable playlist rows (dependency-free vanilla JS).
 *
 *   var view = window.PlaylistRows.mount(hostDiv, {
 *     speeds:   [0.5, 0.75, 1, 1.25, 1.5],
 *     onEdit:   function (index, field, numericValue) {}, // field: 'speed' | 'cycles'
 *     onMove:   function (index, delta) {},               // delta: -1 | 1
 *     onRemove: function (index) {}
 *   });
 *   view.render(entries, namesMap, 'zh' | 'en');
 *   view.setDisabled(true | false);
 *   view.destroy();
 *
 * Controlled component: the caller owns the entries and calls render()
 * synchronously from inside the callbacks. render() only reads its arguments:
 * it never mutates entries, never invokes callbacks and never moves focus.
 * Focus moves only after a move/remove that the caller actually applied.
 * Contains Chinese UI strings: save and serve as UTF-8.
 */
(function (global) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var TIMES = '\u00D7'; // multiplication sign, as in "1×"

  var CYCLE_VALUES = [];
  for (var c = 1; c <= 20; c++) CYCLE_VALUES.push(c);

  var ICON_PATHS = {
    up: 'M12 19V5M5 12l7-7 7 7',
    down: 'M12 5v14M19 12l-7 7-7-7',
    remove: 'M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6'
  };

  var ICON_ATTRS = [
    ['class', 'pr-icon'], ['viewBox', '0 0 24 24'], ['width', '16'], ['height', '16'],
    ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '2'],
    ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'],
    ['aria-hidden', 'true'], ['focusable', 'false']
  ];

  var TEXT = {
    en: {
      speed: 'Speed',
      repeats: 'Repeats',
      cycles: function (n) { return n === 1 ? '1 time' : n + ' times'; },
      moveUp: function (pos, name) { return withName('Move up, item ' + pos, ': ', name); },
      moveDown: function (pos, name) { return withName('Move down, item ' + pos, ': ', name); },
      remove: function (pos, name) { return withName('Remove, item ' + pos, ': ', name); },
      emptyTitle: 'No routines yet',
      emptyNote: 'Choose a routine above to build your playlist.'
    },
    zh: {
      speed: '速度',
      repeats: '重复',
      cycles: function (n) { return n + ' 次'; },
      moveUp: function (pos, name) { return withName('上移第 ' + pos + ' 项', '：', name); },
      moveDown: function (pos, name) { return withName('下移第 ' + pos + ' 项', '：', name); },
      remove: function (pos, name) { return withName('移除第 ' + pos + ' 项', '：', name); },
      emptyTitle: '还没有动作',
      emptyNote: '从上方选择动作加入节目单'
    }
  };

  var idCounter = 0; // module-wide so generated ids stay unique across instances

  /* ---------- pure helpers ---------- */

  function withName(prefix, separator, name) {
    return name ? prefix + separator + name : prefix;
  }

  function toNumber(value) {
    if (typeof value === 'number') return value;
    if (typeof value === 'string' && value.trim() !== '') return Number(value);
    return NaN;
  }

  function isFiniteNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  function sameNumber(a, b) {
    return a === b || (a !== a && b !== b); // NaN counts as equal to NaN
  }

  function indexOfNumber(list, value) {
    for (var i = 0; i < list.length; i++) if (list[i] === value) return i;
    return -1;
  }

  // Off-list value goes before the first larger option (keeps ascending lists sorted).
  function withValue(base, value) {
    var at = base.length;
    for (var i = 0; i < base.length; i++) {
      if (base[i] > value) { at = i; break; }
    }
    return base.slice(0, at).concat([value], base.slice(at));
  }

  function normalizeSpeeds(list) {
    var out = [];
    if (!Array.isArray(list)) return out;
    for (var i = 0; i < list.length; i++) {
      var value = toNumber(list[i]);
      if (isFiniteNumber(value) && value > 0 && indexOfNumber(out, value) < 0) out.push(value);
    }
    return out;
  }

  function formatSpeed(value) {
    return String(Number(value.toFixed(3))) + TIMES;
  }

  function pickLanguage(value, fallback) {
    var code = typeof value === 'string' ? value.toLowerCase() : '';
    if (code.indexOf('zh') === 0) return 'zh';
    if (code.indexOf('en') === 0) return 'en';
    return fallback;
  }

  function displayName(names, action) {
    var name = names && typeof names.get === 'function' ? names.get(action) : null;
    name = name == null ? '' : String(name);
    return name || action;
  }

  // Copies what we need; caller objects are only read, never stored or changed.
  function snapshot(entries, names) {
    var out = [];
    if (!Array.isArray(entries)) return out;
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var ok = entry !== null && typeof entry === 'object';
      var action = ok && entry.action != null ? String(entry.action) : '';
      out.push({
        action: action,
        speed: ok ? toNumber(entry.speed) : NaN,
        cycles: ok ? toNumber(entry.cycles) : NaN,
        name: displayName(names, action)
      });
    }
    return out;
  }

  function sameEntry(a, b) {
    return !!a && !!b && a.action === b.action &&
      sameNumber(a.speed, b.speed) && sameNumber(a.cycles, b.cycles);
  }

  function sameList(a, b) {
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (!sameEntry(a[i], b[i])) return false;
    return true;
  }

  function isSwap(before, after, i, j) {
    if (before.length !== after.length) return false;
    for (var k = 0; k < before.length; k++) {
      var expected = k === i ? before[j] : (k === j ? before[i] : before[k]);
      if (!sameEntry(after[k], expected)) return false;
    }
    return true;
  }

  function isRemoval(before, after, index) {
    if (after.length !== before.length - 1) return false;
    for (var k = 0; k < after.length; k++) {
      if (!sameEntry(after[k], before[k < index ? k : k + 1])) return false;
    }
    return true;
  }

  /* ---------- DOM helpers ---------- */

  function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
  }

  function setAttr(node, name, value) {
    if (node.getAttribute(name) !== value) node.setAttribute(name, value);
  }

  function setProp(node, name, value) {
    if (node[name] !== value) node[name] = value;
  }

  function detach(node) {
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function createIcon(doc, pathData) {
    var svg = doc.createElementNS(SVG_NS, 'svg');
    for (var i = 0; i < ICON_ATTRS.length; i++) svg.setAttribute(ICON_ATTRS[i][0], ICON_ATTRS[i][1]);
    var path = doc.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', pathData);
    svg.appendChild(path);
    return svg;
  }

  // Keeps a <select> in step with a numeric value. An off-list value gets its own
  // option so the control shows the real state; a non-numeric value selects nothing.
  function syncSelect(select, base, value, format) {
    var valid = isFiniteNumber(value);
    var values = valid && indexOfNumber(base, value) < 0 ? withValue(base, value) : base;
    var options = select.options;
    var same = options.length === values.length;
    var i;
    for (i = 0; same && i < values.length; i++) {
      if (options[i].value !== String(values[i])) same = false;
    }
    if (same) {
      for (i = 0; i < values.length; i++) setText(options[i], format(values[i]));
    } else {
      while (select.firstChild) select.removeChild(select.firstChild);
      for (i = 0; i < values.length; i++) {
        var option = select.ownerDocument.createElement('option');
        option.value = String(values[i]);
        option.textContent = format(values[i]);
        select.appendChild(option);
      }
    }
    if (!valid) {
      if (select.selectedIndex !== -1) select.selectedIndex = -1;
    } else if (select.value !== String(value)) {
      select.value = String(value);
    }
  }

  /* ---------- component ---------- */

  function mount(host, options) {
    if (!host || host.nodeType !== 1) {
      throw new TypeError('PlaylistRows.mount: host must be an element');
    }
    var opts = options || {};
    var doc = host.ownerDocument;
    var speeds = normalizeSpeeds(opts.speeds);
    var onEdit = typeof opts.onEdit === 'function' ? opts.onEdit : null;
    var onMove = typeof opts.onMove === 'function' ? opts.onMove : null;
    var onRemove = typeof opts.onRemove === 'function' ? opts.onRemove : null;

    var language = 'en';
    var disabled = false;
    var destroyed = false;
    var rows = [];      // row records in display order; rows[i].index === i
    var current = [];   // snapshots of the last rendered entries
    var renderCount = 0;
    var pending = null; // { type, index, renderCount } while a callback runs

    var list = make('ol', 'pr-list');
    list.setAttribute('role', 'list'); // keeps list semantics if CSS hides markers (WebKit)
    list.hidden = true;                // nothing is shown before the first render()
    var empty = make('div', 'pr-empty');
    var emptyTitle = make('p', 'pr-empty-title');
    var emptyNote = make('p', 'pr-empty-note');
    empty.appendChild(emptyTitle);
    empty.appendChild(emptyNote);
    empty.hidden = true;

    // The host receives focus after the last row is removed.
    var addedTabIndex = !host.hasAttribute('tabindex');
    if (addedTabIndex) host.setAttribute('tabindex', '-1');

    host.appendChild(list);
    host.appendChild(empty);
    list.addEventListener('click', handleClick);
    list.addEventListener('change', handleChange);

    /* ----- construction ----- */

    function make(tag, className) {
      var node = doc.createElement(tag);
      node.className = className;
      return node;
    }

    function uniqueId(kind) {
      var id;
      do { id = 'pr-' + kind + '-' + (++idCounter); } while (doc.getElementById(id));
      return id;
    }

    function makeField(kind, titleId) {
      var wrap = make('div', 'pr-field');
      var label = make('label', 'pr-label');
      var select = make('select', 'pr-select');
      select.id = uniqueId(kind);
      select.setAttribute('aria-describedby', titleId);
      label.htmlFor = select.id;
      wrap.appendChild(label);
      wrap.appendChild(select);
      return { wrap: wrap, label: label, select: select };
    }

    function makeButton(className, pathData) {
      var button = make('button', 'pr-button ' + className);
      button.type = 'button';
      button.appendChild(createIcon(doc, pathData));
      return button;
    }

    function createRow() {
      var li = make('li', 'pr-row');
      li.tabIndex = -1;
      var number = make('span', 'pr-number');
      var title = make('span', 'pr-title');
      title.id = uniqueId('title');
      var fields = make('div', 'pr-fields');
      var speed = makeField('speed', title.id);
      var cycles = makeField('cycles', title.id);
      fields.appendChild(speed.wrap);
      fields.appendChild(cycles.wrap);
      var actions = make('div', 'pr-actions');
      var up = makeButton('pr-move-up', ICON_PATHS.up);
      var down = makeButton('pr-move-down', ICON_PATHS.down);
      var remove = makeButton('pr-remove', ICON_PATHS.remove);
      actions.appendChild(up);
      actions.appendChild(down);
      actions.appendChild(remove);
      li.appendChild(number);
      li.appendChild(title);
      li.appendChild(fields);
      li.appendChild(actions);
      return {
        index: -1, li: li, number: number, title: title,
        speedLabel: speed.label, speedSelect: speed.select,
        cyclesLabel: cycles.label, cyclesSelect: cycles.select,
        up: up, down: down, remove: remove
      };
    }

    function dropRow(row) {
      row.index = -1;
      detach(row.li);
    }

    /* ----- rendering (reads data, never calls back, never focuses) ----- */

    function render(entries, names, lang) {
      if (destroyed) return;
      var next = snapshot(entries, names);
      if (destroyed) return;
      // During onRemove, drop the removed row's own node (not the last one) so
      // focus afterwards lands on a different element and is announced.
      var removeAt = pending && pending.type === 'remove' &&
        pending.renderCount === renderCount && isRemoval(current, next, pending.index)
        ? pending.index : -1;
      renderCount++;
      current = next;
      language = pickLanguage(lang, language);

      if (removeAt >= 0) dropRow(rows.splice(removeAt, 1)[0]);
      while (rows.length > current.length) dropRow(rows.pop());
      while (rows.length < current.length) {
        var row = createRow();
        rows.push(row);
        list.appendChild(row.li);
      }
      for (var i = 0; i < rows.length; i++) updateRow(rows[i], i);

      setProp(list, 'hidden', current.length === 0);
      setProp(empty, 'hidden', current.length !== 0);
      setText(emptyTitle, TEXT[language].emptyTitle);
      setText(emptyNote, TEXT[language].emptyNote);
    }

    function updateRow(row, index) {
      var entry = current[index];
      var text = TEXT[language];
      var pos = index + 1;
      row.index = index;
      setText(row.number, String(pos));
      setText(row.title, entry.name);
      setText(row.speedLabel, text.speed);
      setText(row.cyclesLabel, text.repeats);
      syncField(row, 'speed');
      syncField(row, 'cycles');
      setAttr(row.up, 'aria-label', text.moveUp(pos, entry.name));
      setAttr(row.down, 'aria-label', text.moveDown(pos, entry.name));
      setAttr(row.remove, 'aria-label', text.remove(pos, entry.name));
      applyDisabled(row);
    }

    function syncField(row, field) {
      var entry = current[row.index];
      if (field === 'speed') syncSelect(row.speedSelect, speeds, entry.speed, formatSpeed);
      else syncSelect(row.cyclesSelect, CYCLE_VALUES, entry.cycles, TEXT[language].cycles);
    }

    function applyDisabled(row) {
      setProp(row.speedSelect, 'disabled', disabled);
      setProp(row.cyclesSelect, 'disabled', disabled);
      setProp(row.up, 'disabled', disabled || row.index <= 0);
      setProp(row.down, 'disabled', disabled || row.index >= rows.length - 1);
      setProp(row.remove, 'disabled', disabled);
    }

    function setDisabled(value) {
      if (destroyed) return;
      disabled = !!value;
      for (var i = 0; i < rows.length; i++) applyDisabled(rows[i]);
    }

    /* ----- user actions ----- */

    function handleClick(event) {
      if (destroyed || pending || disabled) return;
      var target = event.target;
      var button = target && typeof target.closest === 'function' ? target.closest('button') : null;
      if (!button || button.disabled) return;
      for (var i = 0; i < rows.length; i++) {
        if (button === rows[i].up) return requestMove(rows[i], -1);
        if (button === rows[i].down) return requestMove(rows[i], 1);
        if (button === rows[i].remove) return requestRemove(rows[i]);
      }
    }

    function handleChange(event) {
      if (destroyed) return;
      for (var i = 0; i < rows.length; i++) {
        if (event.target === rows[i].speedSelect) return requestEdit(rows[i], 'speed');
        if (event.target === rows[i].cyclesSelect) return requestEdit(rows[i], 'cycles');
      }
    }

    function invoke(type, index, callback, args) {
      pending = { type: type, index: index, renderCount: renderCount };
      try {
        callback.apply(undefined, args);
      } finally {
        pending = null;
      }
    }

    function requestEdit(row, field) {
      var select = field === 'speed' ? row.speedSelect : row.cyclesSelect;
      var index = row.index;
      var entry = current[index];
      var value = select.value === '' ? NaN : Number(select.value);
      try {
        if (onEdit && !pending && !disabled && entry && isFiniteNumber(value) &&
            !sameNumber(value, entry[field])) {
          var hadFocus = ownsFocus();
          invoke('edit', index, onEdit, [index, field, value]);
          if (hadFocus && !destroyed) recoverLostFocus(index);
        }
      } finally {
        // Controlled select: always show the caller's state (rejected edits snap back).
        if (!destroyed && rows[row.index] === row) syncField(row, field);
      }
    }

    function requestMove(row, delta) {
      var from = row.index;
      var to = from + delta;
      if (!onMove || from < 0 || to < 0 || to >= current.length) return;
      var before = current;
      var count = renderCount;
      var hadFocus = ownsFocus();
      invoke('move', from, onMove, [from, delta]);
      if (destroyed || renderCount === count || sameList(before, current)) return; // rejected
      if (isSwap(before, current, from, to)) {
        var moved = rows[to];
        var button = delta < 0 ? moved.up : moved.down;
        focusNode(button.disabled ? moved.li : button);
      } else if (hadFocus) {
        recoverLostFocus(from);
      }
    }

    function requestRemove(row) {
      var index = row.index;
      if (!onRemove || index < 0 || index >= current.length) return;
      var before = current;
      var count = renderCount;
      var hadFocus = ownsFocus();
      invoke('remove', index, onRemove, [index]);
      if (destroyed || renderCount === count || sameList(before, current)) return; // rejected
      if (isRemoval(before, current, index)) {
        if (!rows.length) {
          focusNode(host);
        } else {
          var next = rows[Math.min(index, rows.length - 1)];
          focusNode(next.remove.disabled ? next.li : next.remove);
        }
      } else if (hadFocus) {
        recoverLostFocus(index);
      }
    }

    /* ----- focus helpers (used only after our own callbacks) ----- */

    function ownsFocus() {
      var active = doc.activeElement;
      return !!active && (active === host || list.contains(active) || empty.contains(active));
    }

    // If the caller's render removed the focused node, land nearby instead of <body>.
    function recoverLostFocus(index) {
      var active = doc.activeElement;
      if (active && active !== doc.body) return;
      if (!rows.length) focusNode(host);
      else focusNode(rows[Math.max(0, Math.min(index, rows.length - 1))].li);
    }

    function focusNode(node) {
      if (node && typeof node.focus === 'function') node.focus();
    }

    /* ----- teardown ----- */

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      pending = null;
      list.removeEventListener('click', handleClick);
      list.removeEventListener('change', handleChange);
      detach(list);
      detach(empty);
      if (addedTabIndex && host.getAttribute('tabindex') === '-1') host.removeAttribute('tabindex');
      rows = [];
      current = [];
    }

    return Object.freeze({ render: render, setDisabled: setDisabled, destroy: destroy });
  }

  global.PlaylistRows = Object.freeze({ mount: mount });
})(typeof window !== 'undefined' ? window : this);
