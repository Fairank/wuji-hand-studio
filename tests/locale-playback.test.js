'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function makeLocale() {
  const listeners = {};
  const win = {
    localStorage: { getItem() { return null; }, setItem() {} },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    dispatchEvent(event) { (listeners[event.type] || []).forEach(fn => fn(event)); },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    document: { querySelectorAll() { return []; } }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/web/locale.js'), 'utf8'), { window: win });
  return win.WujiLocale;
}

test('backend preview labels localize known phases and registered action names', () => {
  const locale = makeLocale();
  locale.registerActionLabels([{ id: 'count_digits', zh: '依次报数 0–9', en: 'Count 0–9' }]);
  assert.equal(locale.playbackLabel('依次报数 0–9 · 9', 'count_digits'), '依次报数 0–9 · 9');
  locale.setLanguage('en');
  assert.equal(locale.playbackLabel('依次报数 0–9 · 9', 'count_digits'), 'Count 0–9 · 9');
  assert.equal(locale.playbackLabel('准备 / Ready'), 'Ready');
  assert.equal(locale.text('simulationPreviewNoMotion'), 'MuJoCo preview · no hardware movement');
  assert.equal(locale.text('motionPausedOrComplete'), 'Paused or complete');
  locale.setLanguage('zh');
  assert.equal(locale.playbackLabel('依次报数 0–9 · 9', 'count_digits'), '依次报数 0–9 · 9');
});

test('preview surfaces use backend-aware labels and relocalize on language changes', () => {
  const root = path.resolve(__dirname, '../src/web');
  const playback = fs.readFileSync(path.join(root, 'playback.js'), 'utf8');
  const viewer = fs.readFileSync(path.join(root, 'viewer.js'), 'utf8');
  const picker = fs.readFileSync(path.join(root, 'action_picker.js'), 'utf8');
  const visual = fs.readFileSync(path.join(root, 'visual.js'), 'utf8');
  assert.match(playback, /playbackLabel\?\.\(p\.label,p\.action\)/);
  assert.match(playback, /data-i18n="simulationPreviewNoMotion"/);
  assert.match(playback, /wuji-locale-catalog/);
  assert.match(viewer, /playbackLabel\?\.\(playback\.label,playback\.action\)/);
  assert.match(viewer, /仿真动作预览','Motion preview'/);
  assert.match(viewer, /wuji-language/);
  assert.match(picker, /registerActionLabels\?\.\(data\)/);
  assert.match(picker, /playbackLabel\?\.\(p\.label,p\.action\)/);
  assert.match(picker, /wuji-language/);
  assert.match(visual, /Scripted motion · no hardware movement/);
  assert.match(visual, /playbackLabel\?\.\(playback\.label,playback\.action\)/);
  assert.doesNotMatch(visual, /const locale\s*=\s*window\.WujiLocale/);
  assert.match(visual, /window\.WujiLocale\?\.lang/);
  assert.match(visual, /renderUnavailableLabels\(\)/);
  assert.match(visual, /wuji-language/);
  assert.doesNotMatch(playback, /fetch\('\/api\/catalog'/);
});
