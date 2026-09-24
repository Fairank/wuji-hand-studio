/* numeric_grid.js: dependency-free numeric matrix editor (no framework, network, storage or innerHTML).
 * window.NumericGrid.mount(host, { rows, columns, value, language: 'zh'|'en', onChange })
 *   -> { read(), setValue(matrix), setLabels(rows, columns), setDisabled(bool), destroy() }
 */
(function () {
  'use strict';

  const MAX = 10;
  // Sign, digits, optional fraction and exponent only: '', NaN, Infinity, 0x1F and 'true' never match.
  const NUM_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
  const TEXT = {
    en: {
      blank: '{cell}: a number is required.',
      invalid: '{cell}: not a valid number.',
      rejected: 'Paste rejected. ',
      shape: 'Rows have different numbers of values.',
      fit: '{h} × {w} values do not fit from this cell.',
      done: 'Pasted {h} × {w} values.'
    },
    zh: {
      blank: '{cell}：请输入数值。',
      invalid: '{cell}：不是有效数值。',
      rejected: '已拒绝粘贴。',
      shape: '各行数值个数不一致。',
      fit: '从当前单元格起放不下 {h} × {w} 个数值。',
      done: '已粘贴 {h} × {w} 个数值。'
    }
  };

  function parseNum(text) {
    const s = String(text).trim();
    if (!NUM_RE.test(s)) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null; // '1e999' overflows to Infinity: rejected, never clamped
  }

  const fmt = (n) => (Object.is(n, -0) ? '-0' : String(n));

  function checkList(list, what, min) {
    if (!Array.isArray(list) || list.length < min || list.length > MAX) {
      throw new RangeError(`NumericGrid: ${what} must be an array of ${min}-${MAX} entries`);
    }
    const seen = new Set();
    return Array.from(list, (item, i) => {
      if (!item || typeof item.key !== 'string' || typeof item.label !== 'string' || !item.label.trim()) {
        throw new TypeError(`NumericGrid: ${what}[${i}] needs a string key and a non-empty string label`);
      }
      if (seen.has(item.key)) throw new Error(`NumericGrid: duplicate ${what} key "${item.key}"`);
      seen.add(item.key);
      return { key: item.key, label: item.label };
    });
  }

  function checkMatrix(m, nRows, nCols) {
    if (!Array.isArray(m) || m.length !== nRows) throw new RangeError(`NumericGrid: value needs ${nRows} rows`);
    for (let r = 0; r < nRows; r++) {
      if (!Array.isArray(m[r]) || m[r].length !== nCols) {
        throw new RangeError(`NumericGrid: value[${r}] needs ${nCols} columns`);
      }
      for (let c = 0; c < nCols; c++) { // index loop, so sparse holes are rejected too
        if (typeof m[r][c] !== 'number' || !Number.isFinite(m[r][c])) {
          throw new TypeError(`NumericGrid: value[${r}][${c}] must be a finite number`);
        }
      }
    }
  }

  function el(tag, className, parent) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (parent) parent.appendChild(node);
    return node;
  }

  function mount(host, options) {
    if (!host || host.nodeType !== 1) throw new TypeError('NumericGrid: host must be an Element');
    const opts = options || {};
    let lang = opts.language;
    if (lang !== 'zh' && lang !== 'en') throw new TypeError('NumericGrid: language must be "zh" or "en"');
    if (opts.onChange != null && typeof opts.onChange !== 'function') {
      throw new TypeError('NumericGrid: onChange must be a function');
    }
    let rows = checkList(opts.rows, 'rows', 1);
    let cols = checkList(opts.columns, 'columns', 1);
    checkMatrix(opts.value, rows.length, cols.length); // everything is validated before any DOM exists
    let T = TEXT[lang];
    const onChange = opts.onChange || null;
    const cellOf = new Map(); // input element -> [row, column]
    let destroyed = false;

    const root = el('div', 'ng-root');
    root.lang = lang;
    const table = el('table', 'ng-table', el('div', 'ng-scroll', root));
    const headRow = el('tr', '', el('thead', '', table));
    el('td', '', headRow);
    const colHeads = cols.map(() => Object.assign(el('th', 'ng-col', headRow), { scope: 'col' }));
    const tbody = el('tbody', '', table);
    const rowHeads = [];
    const inputs = rows.map((_, r) => {
      const tr = el('tr', '', tbody);
      rowHeads.push(Object.assign(el('th', 'ng-row', tr), { scope: 'row' }));
      return cols.map((__, c) => {
        const input = el('input', 'ng-input', el('td', '', tr));
        Object.assign(input, { type: 'text', autocomplete: 'off', spellcheck: false });
        input.setAttribute('inputmode', 'decimal'); // text type keeps partial input such as '-' or '1e'
        input.value = fmt(opts.value[r][c]);
        input.dataset.gridRow = String(r);
        input.dataset.gridColumn = String(c);
        cellOf.set(input, [r, c]);
        return input;
      });
    });
    const status = el('div', 'ng-status', root);
    status.setAttribute('role', 'status');

    const cellName = (r, c) => `${rows[r].label} ${cols[c].label}`;
    const msg = (key, vars) => T[key].replace(/\{(\w+)\}/g, (_, name) => String(vars[name]));
    const setText = (node, text) => { if (node.textContent !== text) node.textContent = text; };
    function applyLabels() { // text and attributes only: nodes, typed text, focus and caret stay put
      rows.forEach((row, r) => setText(rowHeads[r], row.label));
      cols.forEach((col, c) => setText(colHeads[c], col.label));
      inputs.forEach((line, r) => line.forEach((input, c) => input.setAttribute('aria-label', cellName(r, c))));
    }
    function say(text, isError) {
      status.textContent = text;
      status.classList.toggle('ng-status-error', Boolean(isError));
    }
    function cellError(r, c, raw) {
      const err = new Error(msg(String(raw).trim() ? 'invalid' : 'blank', { cell: cellName(r, c) }));
      err.row = rows[r].key;
      err.column = cols[c].key;
      return err;
    }
    // Builds fresh arrays on every call, so each caller gets its own deep copy.
    const readAll = () => inputs.map((line, r) => line.map((input, c) => {
      const n = parseNum(input.value);
      if (n === null) throw cellError(r, c, input.value);
      return n;
    }));
    function emit() { // onChange only ever receives a complete, valid matrix
      if (!onChange) return;
      let matrix;
      try { matrix = readAll(); } catch { return; }
      onChange(matrix);
    }

    function onInput(e) { // reward early: clear the invalid mark as soon as the text parses
      if (!cellOf.has(e.target)) return;
      if (parseNum(e.target.value) !== null) e.target.removeAttribute('aria-invalid');
      say('');
      emit();
    }
    function onCommit(e) { // punish late: mark blank/invalid text once the edit is committed
      if (!cellOf.has(e.target) || parseNum(e.target.value) !== null) return;
      e.target.setAttribute('aria-invalid', 'true');
    }
    function onPaste(e) {
      const at = cellOf.get(e.target);
      if (!at || !e.clipboardData) return;
      e.preventDefault(); // every paste is validated and applied here, never by the browser
      const text = e.clipboardData.getData('text/plain');
      if (!text) return;
      const input = e.target;
      const [r0, c0] = at;
      const body = text.replace(/\r\n?/g, '\n').replace(/\n$/, ''); // drop at most one terminal newline
      const cells = body.split('\n').map((line) => line.split('\t'));
      const [h, w] = [cells.length, cells[0].length];
      const reject = (reason) => say(T.rejected + reason, true);
      if (cells.some((line) => line.length !== w)) return reject(T.shape);
      if (r0 + h > rows.length || c0 + w > cols.length) return reject(msg('fit', { h, w }));
      let caret = -1;
      if (h === 1 && w === 1) { // single value: splice into the current selection like a native paste
        const { value, selectionStart: start, selectionEnd: end } = input;
        caret = start + cells[0][0].length;
        cells[0][0] = value.slice(0, start) + cells[0][0] + value.slice(end);
      }
      for (let r = 0; r < h; r++) { // validate the whole rectangle before any cell changes
        for (let c = 0; c < w; c++) {
          if (parseNum(cells[r][c]) === null) return reject(cellError(r0 + r, c0 + c, cells[r][c]).message);
        }
      }
      cells.forEach((line, r) => line.forEach((raw, c) => {
        inputs[r0 + r][c0 + c].value = raw;
        inputs[r0 + r][c0 + c].removeAttribute('aria-invalid');
      }));
      if (caret >= 0) input.setSelectionRange(caret, caret);
      say(h * w > 1 ? msg('done', { h, w }) : '');
      emit(); // at most one onChange per paste
    }

    table.addEventListener('input', onInput);
    table.addEventListener('change', onCommit);
    table.addEventListener('paste', onPaste);
    applyLabels();
    host.appendChild(root);

    const alive = () => { if (destroyed) throw new Error('NumericGrid: grid has been destroyed'); };
    return Object.freeze({
      read() {
        alive();
        return readAll();
      },
      setValue(matrix) {
        alive();
        checkMatrix(matrix, rows.length, cols.length); // throws before any cell is touched
        inputs.forEach((line, r) => line.forEach((input, c) => {
          input.value = fmt(matrix[r][c]);
          input.removeAttribute('aria-invalid');
        }));
        say('');
      },
      setLabels(newRows, newCols) {
        alive();
        const rowMap = new Map(checkList(newRows, 'rows', 0).map((it) => [it.key, it.label]));
        const colMap = new Map(checkList(newCols, 'columns', 0).map((it) => [it.key, it.label]));
        const relabel = (list, map) =>
          list.map((it) => (map.has(it.key) ? { key: it.key, label: map.get(it.key) } : it));
        rows = relabel(rows, rowMap); // only existing keys change; unknown keys are ignored
        cols = relabel(cols, colMap);
        applyLabels();
      },
      setLanguage(language) {
        alive();
        if (!TEXT[language]) throw new TypeError('Unknown grid language');
        lang=language; T=TEXT[lang]; root.lang=lang; say('');
      },
      setDisabled(flag) {
        alive();
        if (typeof flag !== 'boolean') throw new TypeError('NumericGrid: setDisabled expects a boolean');
        inputs.forEach((line) => line.forEach((input) => { input.disabled = flag; }));
        root.classList.toggle('ng-disabled', flag);
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        table.removeEventListener('input', onInput);
        table.removeEventListener('change', onCommit);
        table.removeEventListener('paste', onPaste);
        cellOf.clear();
        root.remove();
      }
    });
  }

  window.NumericGrid = Object.freeze({ mount });
})();
