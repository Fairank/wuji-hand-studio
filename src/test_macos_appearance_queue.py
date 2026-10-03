"""Regression tests for serialized native appearance/material changes."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is required for the appearance queue test')
class AppearanceQueueTests(unittest.TestCase):
    def run_node(self, body):
        source = (Path(__file__).parent / 'web/desktop_shell.js').read_text(encoding='utf-8')
        start = source.index(' let materialQueue=Promise.resolve();')
        end = source.index(' function appearance(', start)
        queue = source[start:end]
        script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const dataset={reduceTransparency:'false',theme:'light'};
const document={documentElement:{dataset}};
const context={document,matchMedia:()=>({matches:false}),Promise,
  native:null,info:{platform:'macos'},console};
vm.runInNewContext(QUEUE+'\nglobalThis.applyMaterial=applyMaterial;',context);
BODY
'''.replace('QUEUE', repr(queue)).replace('BODY', body)
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_latest_preferences_and_only_one_native_pair_in_flight(self):
        self.run_node(r'''
let releaseMaterial,releaseAppearance;
const materialGate=new Promise(resolve=>releaseMaterial=resolve);
const appearanceGate=new Promise(resolve=>releaseAppearance=resolve);
const calls=[];let active=0,maxActive=0;
context.native={
  set_window_material(enabled){
    active++;maxActive=Math.max(maxActive,active);calls.push(['material',enabled]);
    if(calls.filter(call=>call[0]==='material').length===1)return materialGate;
    return Promise.resolve({external_backdrop:enabled,mode:enabled?'glass':'solid'});
  },
  set_window_appearance(theme){
    calls.push(['theme',theme]);
    if(calls.filter(call=>call[0]==='theme').length===1)
      return appearanceGate.then(value=>{active--;return value;});
    active--;return Promise.resolve({external_backdrop:true,mode:'glass'});
  }
};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const first=context.applyMaterial();
  dataset.reduceTransparency='true';dataset.theme='light';
  const second=context.applyMaterial();
  dataset.reduceTransparency='false';dataset.theme='dark';
  await tick();
  assert.deepEqual(calls,[['material',true]]);
  releaseMaterial({external_backdrop:true,mode:'glass'});
  await tick();
  assert.deepEqual(calls,[['material',true],['theme','dark']]);
  assert.equal(maxActive,1);
  releaseAppearance({external_backdrop:true,mode:'glass'});
  await Promise.all([first,second]);
  assert.deepEqual(calls,[['material',true],['theme','dark'],['material',true],['theme','dark']]);
  assert.equal(maxActive,1);
  assert.equal(dataset.nativeMaterial,'glass');
  assert.equal(dataset.externalBackdrop,'true');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_rejected_native_call_does_not_poison_later_queue_work(self):
        self.run_node(r'''
let count=0;const calls=[];
context.native={
  set_window_material(enabled){
    calls.push(['material',enabled]);
    count++;
    return count===1?Promise.reject(new Error('native unavailable')):
      Promise.resolve({external_backdrop:enabled,mode:enabled?'glass':'solid'});
  },
  set_window_appearance(theme){calls.push(['theme',theme]);return Promise.resolve({mode:'glass',external_backdrop:true});}
};
(async()=>{
  await assert.rejects(context.applyMaterial(),/native unavailable/);
  dataset.reduceTransparency='false';dataset.theme='dark';
  await context.applyMaterial();
  assert.deepEqual(calls,[['material',true],['material',true],['theme','dark']]);
  assert.equal(dataset.nativeMaterial,'glass');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


if __name__ == '__main__':
    unittest.main()
