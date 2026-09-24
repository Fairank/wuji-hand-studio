/* locale.js — classic script. Exposes window.WujiLocale = { lang, setLanguage, text, apply, dictionary }.
   Static bilingual UI strings; everything is written as plain text. */
(function (global) {
  'use strict';

  var STORAGE_KEY = 'wuji-language';
  var EVENT_NAME = 'wuji-language';
  var hasOwn = Object.prototype.hasOwnProperty;

  var ZH = {
    appTitle: '灵巧手工作台', motion: '动作播放', parameters: '参数调节', feedback: '实时反馈', capture: '触碰采集',
    records: '运行记录', connection: '连接与校准', library: '单手展示', interaction: '触碰互动', glove: '手套遥操作', doctor: '官方诊断', connect: '自动连接',
    disconnect: '断开连接', start: '开始', pause: '暂停', resume: '继续', stop: '停止', preview: '预览',
    hardware: '硬件', source: '来源', official: '官方', project: '项目', unverified: '未验证',
    language: '语言', letters: '字母', numbers: '数字', clock: '时钟',
    clockExplanation: '按启动时的本地时间依次展示小时十位、小时个位、分钟十位、分钟个位',
    floating: '浮动', dock: '停靠', popout: '弹出', ready: '就绪', unavailable: '不可用',
    modelDecision: '模型决策', measuredFeedback: '实测反馈', rawSuggestion: '原始建议',
    appliedCommand: '已应用指令', readOnly: '只读', currentContact: '当前接触', recentTouch: '最近触碰',
    copy: '复制', save: '保存', close: '关闭', search: '搜索', noResults: '未找到与“{query}”匹配的结果'
  };
  var EN = {
    appTitle: 'Hand Workbench', motion: 'Motion', parameters: 'Parameters', feedback: 'Feedback', capture: 'Capture',
    records: 'Records', connection: 'Connection', library: 'Single hand', interaction: 'Interaction', glove: 'Glove teleop', doctor: 'Diagnostics', connect: 'Auto-connect',
    disconnect: 'Disconnect', start: 'Start', pause: 'Pause', resume: 'Resume', stop: 'Stop', preview: 'Preview',
    hardware: 'Hardware', source: 'Source', official: 'Official', project: 'Project', unverified: 'Unverified',
    language: 'Language', letters: 'Letters', numbers: 'Numbers', clock: 'Clock',
    clockExplanation: 'Shows the local time taken at start, one digit at a time: hour tens, hour ones, minute tens, minute ones',
    floating: 'Floating', dock: 'Dock', popout: 'Pop out', ready: 'Ready', unavailable: 'Unavailable',
    modelDecision: 'Model decision', measuredFeedback: 'Measured feedback', rawSuggestion: 'Raw suggestion',
    appliedCommand: 'Applied command', readOnly: 'Read-only', currentContact: 'Current contact', recentTouch: 'Recent touch',
    copy: 'Copy', save: 'Save', close: 'Close', search: 'Search', noResults: 'No results for "{query}"'
  };
  var DICT = Object.freeze({ zh: Object.freeze(ZH), en: Object.freeze(EN) });
  var current = readStored();

  function readStored() {
    try { return global.localStorage.getItem(STORAGE_KEY) === 'en' ? 'en' : 'zh'; } catch (err) { return 'zh'; }
  }

  /* Returns a plain string. {name} markers are filled from own properties of params in a
     single pass; values are never re-expanded and never treated as markup. */
  function text(key, params) {
    var name = String(key);
    var table = DICT[current];
    var template = hasOwn.call(table, name) ? table[name] : name;
    if (params === null || typeof params !== 'object') return template;
    return template.replace(/\{(\w+)\}/g, function (match, field) {
      var value = hasOwn.call(params, field) ? params[field] : undefined;
      return value === undefined || value === null ? match : String(value);
    });
  }

  /* Text is only written to leaf elements whose text is not a form value. */
  function canSetText(el) {
    var tag = String(el.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return false;
    if (tag === 'OPTION' && !(el.hasAttribute && el.hasAttribute('value'))) return false; // its text would become its value
    return !(el.children && el.children.length);
  }

  var TARGETS = [
    ['data-i18n', function (el, value) { if (!canSetText(el)) return false; el.textContent = value; return true; }],
    ['data-i18n-title', function (el, value) { el.setAttribute('title', value); return true; }],
    ['data-i18n-placeholder', function (el, value) { el.setAttribute('placeholder', value); return true; }]
  ];

  function apply(root) {
    root = root || global.document;
    if (!root || typeof root.querySelectorAll !== 'function') return 0;
    var changed = 0;
    TARGETS.forEach(function (target) {
      var attr = target[0], found = root.querySelectorAll('[' + attr + ']'), list = [], i, key;
      if (typeof root.getAttribute === 'function' && root.getAttribute(attr) !== null) list.push(root);
      for (i = 0; i < found.length; i++) list.push(found[i]);
      for (i = 0; i < list.length; i++) {
        key = list[i].getAttribute(attr);
        if (key && hasOwn.call(ZH, key) && target[1](list[i], text(key))) changed++;
      }
    });
    return changed;
  }

  function setLanguage(next) {
    if (next !== 'zh' && next !== 'en') return false;
    current = next;
    try { global.localStorage.setItem(STORAGE_KEY, next); } catch (err) { /* storage blocked: still switch */ }
    if (typeof global.CustomEvent === 'function' && typeof global.dispatchEvent === 'function') {
      global.dispatchEvent(new global.CustomEvent(EVENT_NAME, { detail: { lang: next } }));
    }
    apply();
    return true;
  }

  var api = { setLanguage: setLanguage, text: text, apply: apply, dictionary: DICT };
  Object.defineProperty(api, 'lang', { enumerable: true, get: function () { return current; } });
  global.WujiLocale = api;
})(typeof window !== 'undefined' ? window : this);
