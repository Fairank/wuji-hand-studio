'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const polish = require('../src/web/polish.js');

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.className = '';
    this.tabIndex = 0;
    this.style = {
      values: {},
      getPropertyValue(name) { return this.values[name] || ''; },
      setProperty(name, value) { this.values[name] = String(value); },
      removeProperty(name) { delete this.values[name]; }
    };
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  hasAttribute(name) { return Object.hasOwn(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { this.focused = true; }
  closest() { return null; }
  querySelectorAll() { return this.descendants || []; }
}

test('font scale accepts only the supported persisted values', () => {
  assert.equal(polish.normalizeFontScale('90'), '90');
  assert.equal(polish.normalizeFontScale('100'), '100');
  assert.equal(polish.normalizeFontScale('110'), '110');
  for (const invalid of [null, '', '80', '120', '1.1', 'NaN']) assert.equal(polish.normalizeFontScale(invalid), '100');
});

test('language switch stays synchronized, accessible, and keyboard operable', () => {
  const doc = { createElement: tag => new Element(tag) };
  const events = {};
  const locale = { lang: 'zh', setLanguage(next) { this.lang = next; this.last = next; } };
  const switcher = polish.mountLanguageSwitch(doc, locale);
  assert.equal(switcher.element.getAttribute('role'), 'group');
  assert.equal(switcher.element.getAttribute('aria-label'), '语言');
  assert.deepEqual(switcher.buttons.map(button => button.textContent), ['中文', 'EN']);
  assert.deepEqual(switcher.buttons.map(button => button.type), ['button', 'button']);
  assert.equal(switcher.buttons[0].getAttribute('aria-pressed'), 'true');
  assert.equal(switcher.buttons[0].tabIndex, 0);
  assert.equal(switcher.buttons[1].tabIndex, -1);

  switcher.buttons[1].listeners.click();
  switcher.sync(locale.lang);
  assert.equal(locale.last, 'en');
  assert.equal(switcher.buttons[1].getAttribute('aria-pressed'), 'true');
  assert.equal(switcher.element.getAttribute('aria-label'), 'Language');

  let prevented = false;
  switcher.element.listeners.keydown({
    target: switcher.buttons[1], key: 'ArrowLeft',
    preventDefault() { prevented = true; }
  });
  assert.equal(prevented, true);
  assert.equal(switcher.buttons[0].focused, true);
  assert.equal(locale.last, 'zh');
  switcher.element.setAttribute('aria-labelledby', 'settings-language-label');
  switcher.sync('en');
  assert.equal(switcher.element.getAttribute('aria-label'), null);
});

test('text size preference sets a CSS scale without scanning dynamic DOM', () => {
  const root = new Element('html'); root.dataset = {};
  const control = { value: '100' }, saved = {};
  const doc = {
    documentElement: root,
    getElementById: id => id === 'settings-font-size' ? control : null
  };
  const win = {
    document: doc,
    localStorage: { setItem: (key, value) => { saved[key] = value; } }
  };
  const scaler = polish.createTextScaler(win);
  scaler.set('110');
  assert.equal(root.style.getPropertyValue('--wuji-font-scale'), '1.1');
  assert.equal(control.value, '110');
  scaler.set('90');
  assert.equal(root.style.getPropertyValue('--wuji-font-scale'), '0.9');
  scaler.set('100');
  assert.equal(root.style.getPropertyValue('--wuji-font-scale'), '1');
  assert.equal(saved['wuji-font-scale'], '100');
  assert.equal(root.dataset.uiFontScale, '100');
  assert.equal('observe' in scaler, false);
});

test('entrypoint loads polish last and preserves the settings language ID', () => {
  const root = path.resolve(__dirname, '../src/web');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const settings = fs.readFileSync(path.join(root, 'settings.js'), 'utf8');
  const css = fs.readFileSync(path.join(root, 'polish.css'), 'utf8');
  const picker = fs.readFileSync(path.join(root, 'action_picker.js'), 'utf8');
  const playback = fs.readFileSync(path.join(root, 'playback.js'), 'utf8');
  assert.ok(html.indexOf('/spring_motion.js') < html.indexOf('/polish.css'));
  assert.ok(html.indexOf('/polish.css') < html.indexOf('/polish.js'));
  assert.match(settings, /language\.id='settings-language'/);
  assert.match(settings, /settings-font-size/);
  assert.match(css, /html\[data-theme=dark\]\[data-external-backdrop=true\] \.app\{background:transparent\}/);
  assert.match(css, /@media\(forced-colors:active\)/);
  assert.match(css, /prefers-reduced-transparency:reduce/);
  assert.match(css, /--wuji-font-scale/);
  assert.match(css, /html body \.wuji-language-switch button\[aria-pressed=true\]/);
  assert.match(css, /#desktop-menu-button[\s\S]*width:36px;height:36px;min-height:36px/);
  assert.match(css, /wuji-language-switch--toolbar\{[\s\S]*height:36px/);
  assert.match(css, /wuji-language-switch--toolbar button\[aria-pressed=true\][\s\S]*background:var\(--wuji-polish-accent-soft\)/);
  assert.match(css, /html\[data-theme=dark\] body #desktop-menu-button/);
  assert.match(css, /html\[data-theme=dark\] body \.wuji-language-slot>\.wuji-language-switch--toolbar button\[aria-pressed=true\]/);
  assert.match(css, /html body #desktop-menu\{[\s\S]*background:var\(--wuji-polish-control\)/);
  assert.match(css, /html body #desktop-menu \.desktop-menu-section\{[\s\S]*background:transparent/);
  assert.match(css, /html body #desktop-menu button\.desktop-menu-row:hover:not\(:disabled\)/);
  assert.match(css, /html body \.connection-popover\{[\s\S]*background:var\(--wuji-polish-control\)/);
  assert.match(css, /html body \.connection-popover \.wa-row:hover:not\(:disabled\)/);
  assert.match(css, /html\[data-theme=dark\] body \.settings-tabs\[role=tablist\] button\[aria-selected=true\]/);
  assert.match(css, /html body select:not\(\[multiple\],\[size\]\)/);
  assert.match(css, /border-radius:12px/);
  const polishJs = fs.readFileSync(path.join(root, 'polish.js'), 'utf8');
  assert.match(polishJs, /\.top-actions \.studio-tools select/);
  assert.match(picker, /isPreviewOnly\(id\)/);
  assert.match(picker, /const exportable=item\?\.hardware!==false/);
  assert.match(picker, /真实手模式会禁用实机启动/);
  assert.match(playback, /selectedHardwareAllowed/);
  assert.match(playback, /Hardware start is disabled/i);
  assert.match(playback, /aria-describedby','motion-blocker'/);
});

test('standalone viewer shares presentation preferences without main-page startup', () => {
  const root = path.resolve(__dirname, '../src/web');
  const html = fs.readFileSync(path.join(root, 'viewer.html'), 'utf8');
  const js = fs.readFileSync(path.join(root, 'viewer.js'), 'utf8');
  const css = fs.readFileSync(path.join(root, 'viewer.css'), 'utf8');
  assert.match(html, /href="\/polish\.css"/);
  assert.doesNotMatch(html, /src="\/polish\.js"/);
  assert.match(html, /href="\/viewer\.css"/);
  assert.doesNotMatch(html, /<style[\s>]/);
  assert.match(css, /border-radius:12px/);
  assert.match(css, /forced-colors:active/);
  assert.match(js, /wuji-font-scale/);
  assert.match(js, /wuji-workbench-theme/);
  assert.doesNotMatch(js, /setInterval|hardware_start|hardware_trial|glove_follow/);
});
