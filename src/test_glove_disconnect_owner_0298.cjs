'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'web/glove.js'), 'utf8');
const start = source.indexOf(' async function act(');
const end = source.indexOf(' async function switchConnection(', start);
assert.ok(start >= 0 && end > start);
const receipt = {ok: true, glove: {session_id: 8, action_revision: 3, connection: 'disconnected'}};
const disconnect = {name: 'glove_disconnect', expected_session: 8, expected_revision: 2};
const nextTurn = () => new Promise(resolve => setImmediate(resolve));

function fixture(post = async () => receipt) {
  let finishRead;
  const readGate = new Promise(resolve => { finishRead = resolve; });
  const clear = {checked: true}, events = [];
  const scope = {
    state: {csrf: 'isolated-service', glove: {session_id: 8, action_revision: 2, connection: 'receiving'},
      connection_switch: {expected_owner: 'old-owner'}},
    online: true, pending: false, pendingName: '', requestEpoch: 0, operationVersion: 0,
    selectionVersion: 0, lease: 'initial-lease', pendingEndSession: null, pendingEndRevision: null,
    validSession: Number.isSafeInteger, validRevision: Number.isSafeInteger, virtualMode: () => false,
    sourceIdentity: () => 'isolated-source', attempt: () => null, post,
    actionError: error => error.message, lastMessage: '',
    releaseLease: () => { scope.lease = null; },
    $: () => clear,
    render: () => events.push([scope.state.connection_switch.expected_owner, scope.state.glove.connection]),
    window: {WujiConnection: {async refresh() {
      events.push('read');
      await readGate;
      // Equivalent to publishing a new authoritative console-state event.
      scope.state = {...scope.state, glove: receipt.glove, connection_switch: {expected_owner: 'new-owner'}};
      return scope.state;
    }}},
  };
  vm.runInNewContext(source.slice(start, end) + '\nthis.run = act;', scope);
  return {scope, events, finishRead, clear};
}

test('accepted glove disconnect waits for shared connection ownership before presenting the released state', async () => {
  const f = fixture();
  const acting = f.scope.run(disconnect, true);
  try {
    await nextTurn();
    assert.ok(f.events.includes('read'), 'Read shared state after the accepted disconnect');
    assert.equal(f.scope.state.glove.connection, 'receiving');
    assert.equal(f.scope.pending, true);
    assert.equal(f.scope.lease, null);
    assert.equal(f.clear.checked, false);
  } finally {
    f.finishRead();
    await acting;
  }
  assert.deepEqual(f.events.at(-1), ['new-owner', 'disconnected']);
  assert.equal(f.scope.pending, false);
});

test('a rejected or superseded disconnect response cannot publish shared ownership', async () => {
  const refused = fixture(async () => { throw Error('fixture refusal'); });
  await refused.scope.run(disconnect, true);
  assert.equal(refused.events.includes('read'), false);
  assert.equal(refused.scope.lastMessage, 'fixture refusal');
  assert.equal(refused.scope.state.glove.connection, 'receiving');

  let acknowledge;
  const oldResponse = new Promise(resolve => { acknowledge = resolve; });
  const stale = fixture(() => oldResponse);
  const acting = stale.scope.run(disconnect, true);
  stale.scope.requestEpoch++;
  stale.scope.state.glove = {...stale.scope.state.glove, session_id: 9};
  acknowledge(receipt);
  await acting;
  assert.equal(stale.events.includes('read'), false);
  assert.equal(stale.scope.state.glove.connection, 'receiving');
  assert.equal(stale.scope.state.glove.session_id, 9);
});
