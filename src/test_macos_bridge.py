"""CSP-safe macOS WebView bridge checks (Node, no AppKit or live window)."""
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch, MagicMock


@unittest.skipUnless(shutil.which('node'), 'Node is required for the CSP bridge test')
class MacBridgeTests(unittest.TestCase):
    def run_bridge(self, body):
        script = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
const pending = new Map();
const bridge = {api:{}, _returnValuesCallbacks:{},
  _checkValue(name, resolve, reject, id){pending.set(id, {resolve, reject, name});},
  _jsApiCallback(name, args, id){
    const item = pending.get(id);
    if (!item) throw new Error('missing pending callback ' + id);
    pending.delete(id);
    item.resolve({name, args});
  }};
const context = vm.createContext({window:{pywebview:bridge},crypto:require('node:crypto')},
  {codeGeneration:{strings:false,wasm:false}});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'),context);
BODY
'''.replace('BODY', body)
        bridge_path = Path(__file__).parent / 'web/macos_bridge.js'
        result = subprocess.run([shutil.which('node'), '-e', script, str(bridge_path)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_closure_api_dispatch_without_eval_and_reject_prototype_names(self):
        self.run_bridge(r'''
bridge._createApi([{func:'info'}, {func:'nested.echo'}]);
(async()=>{
  assert.equal((await bridge.api.info()).name,'info');
  const r = await bridge.api.nested.echo('hello', 20);
  assert.equal(r.name,'nested.echo');
  assert.equal(r.args[0],'hello');
  assert.equal(r.args[1],20);
  assert.equal(pending.size,0);
  for (const func of ['__proto__.polluted','constructor.x','nested.prototype',
                      'nested.__proto__.polluted','nested.constructor']) {
    assert.throws(()=>bridge._createApi([{func}]),/Invalid bridge name/);
  }
  assert.equal({}.polluted, undefined);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_remote_rejection_settles_the_corresponding_promise(self):
        self.run_bridge(r'''
bridge._createApi([{func:'danger'}]);
bridge._jsApiCallback = (name, args, id) => {
  const item = pending.get(id);
  pending.delete(id);
  item.reject({message:'host rejected', name});
};
(async()=>{
  await assert.rejects(bridge.api.danger(), error =>
    error.message === 'host rejected' && error.name === 'danger');
  assert.equal(pending.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_host_binding_injects_factory_only_for_api_factory_script(self):
        from macos_desktop import MacDesktop

        class FakeWindow:
            def __init__(self):
                self.scripts = []
                self.run_js = self.scripts.append

        window = FakeWindow()
        desktop = MacDesktop()
        with patch('macos_desktop.RESOURCE', Path(__file__).parent):
            desktop.bind_script_bridge(window)
            wrapped = window.run_js
            factory_call = 'window.pywebview._createApi([{func:"info"}])'
            wrapped(factory_call)
            wrapped('document.title="unchanged"')
            desktop.bind_script_bridge(window)  # idempotent; must not wrap twice
            window.run_js(factory_call)

        self.assertEqual(len(window.scripts), 3)
        self.assertTrue(window.scripts[0].startswith('/* Cocoa bridge'))
        self.assertTrue(window.scripts[0].endswith(factory_call))
        self.assertEqual(window.scripts[1], 'document.title="unchanged"')
        self.assertEqual(window.scripts[2], window.scripts[0])

    def test_preview_awaits_serializable_result_without_javascript_eval(self):
        from macos_desktop import MacDesktop
        desktop = MacDesktop()
        desktop.window = MagicMock()
        expected = dict(ok=True, hardware_motion=False, action='digit_0', speed=.5)
        desktop.window.evaluate_js.return_value = expected
        self.assertEqual(desktop.preview('digit_0', .5), expected)
        desktop.window.evaluate_js.assert_called_once_with(
            'window.WujiWorkbench.preview("digit_0",0.5)', await_promise=True)


if __name__ == '__main__':
    unittest.main()
