'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, 'web', 'locale.js'), 'utf8');
function locale() {
  const window = {localStorage: {getItem: () => null, setItem: () => {}}};
  vm.runInNewContext(source, {window});
  return window.WujiLocale;
}

test('published bilingual notices use just the current interface language in either order', () => {
  const l = locale();
  const pair = '专用网口准备未完成，可重新查找重试 / The device network setup did not complete. Find devices again to retry.';
  assert.equal(l.notice(pair), '专用网口准备未完成，可重新查找重试');
  assert.equal(l.notice('Device offline / 设备离线'), '设备离线');
  l.setLanguage('en');
  assert.equal(l.notice(pair), 'The device network setup did not complete. Find devices again to retry.');
  assert.equal(l.notice('Device offline / 设备离线'), 'Device offline');
});

test('identifiers, paths, prose with slashes and multi-line diagnostics keep their meaning', () => {
  const l = locale();
  for (const value of [
    'HTTP 503 / Connection unavailable',
    '路径 https://127.0.0.1/api/state',
    '读取 / 写入',
    '读取失败 / Read failed / Retry',
    '读取失败\nRead failed / 503',
    'Profile C:/Models/one.json',
    '<b>操作未完成</b>',
  ]) assert.equal(l.notice(value), value);
  assert.equal(l.notice(null), '');
});
