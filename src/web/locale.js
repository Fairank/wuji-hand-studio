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
    copy: '复制', save: '保存', close: '关闭', search: '搜索', noResults: '未找到与“{query}”匹配的结果',
    simulationPreviewNoMotion: 'MuJoCo 动作预览 · 不驱动实机', simulatedMotionPreview: '仿真动作预览',
    motionPlaying: '播放中', motionPausedOrComplete: '已暂停或完成',
    previewFeedback: '当前显示实机反馈或静态预览', scriptedPreview: '编排预览'
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
    copy: 'Copy', save: 'Save', close: 'Close', search: 'Search', noResults: 'No results for "{query}"',
    simulationPreviewNoMotion: 'MuJoCo preview · no hardware movement', simulatedMotionPreview: 'Motion preview',
    motionPlaying: 'Playing', motionPausedOrComplete: 'Paused or complete',
    previewFeedback: 'Showing measured feedback or static preview', scriptedPreview: 'Scripted preview'
  };
  var DICT = Object.freeze({ zh: Object.freeze(ZH), en: Object.freeze(EN) });
  var current = readStored();
  var actionLabels = [];
  var PLAYBACK_LABELS = Object.freeze({
    '整套展示': 'Combined preview', '张开': 'Open', '握拳': 'Fist', '依次对指': 'Sequential opposition',
    '左右侧摆': 'Side-to-side sway', '逐指屈伸': 'Finger wave', '逐关节活动': 'Per-joint movement',
    '拇指活动': 'Thumb movement', '食指活动': 'Index finger movement', '中指活动': 'Middle finger movement',
    '无名指活动': 'Ring finger movement', '小指活动': 'Little finger movement',
    '官方左手对指录制': 'Official left-hand opposition recording', '轻触反应流程 · 编排预览': 'Touch response · scripted preview',
    '准备': 'Ready', '返回': 'Return', '完成': 'Complete', '实测起点': 'Measured start',
    '返回实测起点': 'Return to measured start', '实际起始姿态': 'Measured start pose', '稳定': 'Hold'
  });

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

  /* Playback labels arrive from the controller in Chinese. Prefer an existing
     bilingual segment when present, then translate known catalog/phase labels. */
  function registerActionLabels(actions) {
    actionLabels = Array.isArray(actions) ? actions.filter(function (item) {
      return item && typeof item.zh === 'string' && typeof item.en === 'string';
    }).map(function (item) { return { id: item.id, zh: item.zh, en: item.en }; }) : [];
    if (typeof global.CustomEvent === 'function' && typeof global.dispatchEvent === 'function') {
      global.dispatchEvent(new global.CustomEvent('wuji-locale-catalog'));
    }
  }

  function playbackLabel(label, actionId) {
    var value = String(label || '');
    if (current !== 'en') return value;
    var action = actionLabels.find(function (item) { return item.id === actionId; });
    if (!action) action = actionLabels.find(function (item) { return value === item.zh || value.indexOf(item.zh + ' · ') === 0; });
    if (action && value.indexOf(action.zh) === 0) value = action.en + value.slice(action.zh.length);
    return value.split(' · ').map(function (part) {
      var bilingual = part.match(/^(.+?)\s*\/\s*([A-Za-z].*)$/);
      if (bilingual) return bilingual[2].trim();
      return hasOwn.call(PLAYBACK_LABELS, part) ? PLAYBACK_LABELS[part] : part;
    }).join(' · ');
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

  var api = { setLanguage: setLanguage, text: text, playbackLabel: playbackLabel, registerActionLabels: registerActionLabels, apply: apply, dictionary: DICT };
  Object.defineProperty(api, 'lang', { enumerable: true, get: function () { return current; } });
  global.WujiLocale = api;
})(typeof window !== 'undefined' ? window : this);
