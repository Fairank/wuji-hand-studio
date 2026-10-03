/* Accessible language pills and a persisted text-only size preference. */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) api.boot(root);
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  var SCALE_VALUES = Object.freeze({ '90': 0.9, '100': 1, '110': 1.1 });

  function normalizeFontScale(value) {
    var key = String(value);
    return Object.prototype.hasOwnProperty.call(SCALE_VALUES, key) ? key : '100';
  }

  function mountLanguageSwitch(doc, locale, options) {
    options = options || {};
    var group = doc.createElement('div');
    group.className = 'wuji-language-switch' + (options.className ? ' ' + options.className : '');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', options.label || '语言 / Language');
    var labels = [['zh', '中文'], ['en', 'EN']], buttons = [];
    labels.forEach(function (entry) {
      var button = doc.createElement('button');
      button.type = 'button';
      button.textContent = entry[1];
      button.dataset.language = entry[0];
      button.setAttribute('aria-pressed', 'false');
      button.tabIndex = -1;
      button.addEventListener('click', function () { locale.setLanguage(entry[0]); });
      group.appendChild(button);
      buttons.push(button);
    });
    function sync(language) {
      var active = language === 'en' ? 'en' : 'zh';
      if (group.hasAttribute('aria-labelledby')) group.removeAttribute('aria-label');
      else group.setAttribute('aria-label', active === 'en' ? 'Language' : '语言');
      buttons.forEach(function (button) {
        var selected = button.dataset.language === active;
        button.setAttribute('aria-pressed', String(selected));
        button.tabIndex = selected ? 0 : -1;
      });
    }
    group.addEventListener('keydown', function (event) {
      var index = buttons.indexOf(event.target), next;
      if (index < 0) return;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % buttons.length;
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + buttons.length - 1) % buttons.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = buttons.length - 1;
      else return;
      event.preventDefault();
      buttons[next].focus();
      locale.setLanguage(buttons[next].dataset.language);
    });
    sync(locale.lang);
    return { element: group, sync: sync, buttons: buttons };
  }

  function createTextScaler(win) {
    var doc = win.document;
    function set(value) {
      var key = normalizeFontScale(value), factor = SCALE_VALUES[key];
      doc.documentElement.dataset.uiFontScale = key;
      doc.documentElement.style.setProperty('--wuji-font-scale', String(factor));
      try { win.localStorage.setItem('wuji-font-scale', key); } catch (err) { /* Preference remains active for this session. */ }
      var control = doc.getElementById('settings-font-size');
      if (control && control.value !== key) control.value = key;
      return key;
    }
    return { set: set };
  }

  function boot(win) {
    var doc = win.document, locale = win.WujiLocale;
    if (!doc || !locale || doc.documentElement.dataset.polishReady === 'true') return;
    doc.documentElement.dataset.polishReady = 'true';
    var switches = [];
    function addSwitch(parent, options) {
      if (!parent || parent.querySelector('.wuji-language-switch')) return;
      var control = mountLanguageSwitch(doc, locale, options);
      parent.appendChild(control.element);
      switches.push(control);
    }
    var actions = doc.querySelector('.top-actions');
    if (actions) {
      var toolbar = doc.createElement('div');
      toolbar.className = 'wuji-language-slot';
      addSwitch(toolbar, { className: 'wuji-language-switch--toolbar' });
      var tools = actions.querySelector('.desktop-tools');
      if (tools) actions.insertBefore(toolbar, tools); else actions.appendChild(toolbar);
    }
    var language = doc.getElementById('settings-language');
    if (language) {
      var row = language.parentElement, label = doc.getElementById('settings-language-label');
      language.classList.add('wuji-language-source');
      language.hidden = true;
      language.tabIndex = -1;
      language.setAttribute('aria-hidden', 'true');
      addSwitch(row, { label: '语言' });
      var settingsSwitch = row.querySelector('.wuji-language-switch');
      if (label && settingsSwitch) {
        label.id = 'settings-language-label';
        label.removeAttribute('for');
        settingsSwitch.setAttribute('aria-labelledby', label.id);
        settingsSwitch.removeAttribute('aria-label');
      }
    }
    var nativeLanguage = doc.getElementById('studio-language') || doc.querySelector('.top-actions .studio-tools select');
    if (nativeLanguage) {
      nativeLanguage.hidden = true;
      nativeLanguage.tabIndex = -1;
      nativeLanguage.setAttribute('aria-hidden', 'true');
      if (nativeLanguage.parentElement) nativeLanguage.parentElement.hidden = true;
    }
    var scaler = createTextScaler(win), saved = '100';
    try { saved = normalizeFontScale(win.localStorage.getItem('wuji-font-scale')); } catch (err) { /* Default to 100%. */ }
    scaler.set(saved);
    function sync(event) {
      var lang = event && event.detail && event.detail.lang || locale.lang;
      doc.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
      switches.forEach(function (control) { control.sync(lang); });
      if (language) language.value = lang;
    }
    win.addEventListener('wuji-language', sync);
    sync();
    win.WujiPolish = { setFontScale: scaler.set, normalizeFontScale: normalizeFontScale };
  }

  return { normalizeFontScale: normalizeFontScale, mountLanguageSwitch: mountLanguageSwitch, createTextScaler: createTextScaler, boot: boot };
});
