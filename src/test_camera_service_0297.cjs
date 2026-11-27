/* Run the production camera controls against isolated service events. */
'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'web/camera.js'), 'utf8');
const script = source.slice(source.indexOf('\n') + 1);

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.events = {};
    this.disabled = false;
  }
  append(child) {
    if (child.parent) child.parent.children = child.parent.children.filter(item => item !== child);
    this.children.push(child);
    child.parent = this;
  }
  before() {}
  setAttribute() {}
  set innerHTML(value) {
    assert.match(value, /class="camera-presets"/);
    const group = new Element('div');
    group.className = 'camera-presets';
    for (const view of ['front', 'back', 'side', 'reset']) {
      const button = new Element('button');
      button.dataset.view = view;
      group.append(button);
    }
    this.append(group);
    for (const source of ['preview', 'feedback']) {
      const button = new Element('button');
      button.dataset.source = source;
      this.append(button);
    }
  }
  querySelectorAll(selector) {
    const matches = node => selector === 'button' ? node.tag === 'button' : selector === '.camera-presets' ?
      node.className === 'camera-presets' : node.dataset[selector === '[data-view]' ? 'view' : 'source'] !== undefined;
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  addEventListener(type, callback) { this.events[type] = callback; }
  dispatch(type, properties = {}) { return this.events[type]({preventDefault() {}, ...properties}); }
}

function testCamera(respond) {
  const screen = new Element('div'), notice = new Element('p'), image = new Element('img');
  const listeners = {}, commands = [], deadlines = new Map();
  let nextDeadline = 0;
  const context = {
    window: {addEventListener: (name, callback) => { listeners[name] = callback; }},
    document: {querySelector: () => screen, createElement: tag => new Element(tag),
      getElementById: id => id === 'camera-notice' ? notice : image},
    AbortController, AbortSignal, Math, performance: {now: () => 1000},
    setTimeout: callback => { deadlines.set(++nextDeadline, callback); return nextDeadline; },
    clearTimeout: id => deadlines.delete(id),
    fetch: async (url, request) => {
      assert.equal(url, '/api/action');
      assert.equal(request.method, 'POST');
      const command = {token: request.headers['X-Console-Token'], body: JSON.parse(request.body)};
      commands.push(command);
      const override = respond?.(command, request.signal);
      if (override) return override;
      return {ok: true, json: async () => ({ok: true, camera_revision: 2})};
    },
  };
  vm.runInNewContext(script, context);
  return {screen, notice, commands, presets: screen.querySelectorAll('[data-view]'),
    ready(token) {
      listeners['console-state']({detail: {csrf: token, camera: {azimuth: 90, elevation: -5, distance: .38,
        lookat: [0, 0, .115]}, camera_revision: 1, hardware: {active: false}}});
    },
    offline: () => listeners['console-offline'](),
    async elapse() { for (const [id, callback] of deadlines) { deadlines.delete(id); await callback(); } },
  };
}

test('embedded camera presets disable on outage and the explicitly selected view recovers', async () => {
  const camera = testCamera();
  camera.ready('service-A');
  assert.equal(camera.presets.filter(button => !button.disabled).length, 4);
  camera.offline();
  assert.equal(camera.presets.filter(button => !button.disabled).length, 0);
  camera.ready('service-B');
  assert.equal(camera.presets.filter(button => !button.disabled).length, 4);
  camera.presets.find(button => button.dataset.view === 'side').dispatch('click');
  await camera.elapse();
  assert.equal(camera.commands.length, 1);
  assert.equal(camera.commands[0].token, 'service-B');
  assert.equal(camera.commands[0].body.camera.azimuth, 0);
});

test('a keyboard preset pressed while offline is never replayed when the service returns', async () => {
  const camera = testCamera();
  camera.ready('service-A');
  camera.offline();
  camera.screen.dispatch('keydown', {key: 'r'});
  camera.ready('service-B');
  await camera.elapse();
  assert.deepEqual(camera.commands, []);
});

test('a late camera reply cannot overwrite the recovered service controls', async () => {
  let oldSignal, release;
  const camera = testCamera((command, signal) => {
    if (command.token !== 'service-A') return;
    oldSignal = signal;
    return new Promise(resolve => { release = resolve; });
  });
  camera.ready('service-A');
  camera.presets.find(button => button.dataset.view === 'side').dispatch('click');
  const pending = camera.elapse();
  assert.equal(typeof release, 'function');
  camera.offline();
  assert.equal(oldSignal.aborted, true);
  camera.ready('service-B');
  camera.presets.find(button => button.dataset.view === 'front').dispatch('click');
  await camera.elapse();
  assert.deepEqual(camera.commands.map(command => command.token), ['service-A', 'service-B']);
  release({ok: false, json: async () => ({ok: false, error: 'Previous service reply'})});
  await pending;
  assert.equal(camera.notice.textContent, '');
  assert.ok(camera.presets.every(button => !button.disabled));
});
