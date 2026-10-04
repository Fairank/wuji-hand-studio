"""Cold-start readiness regressions for the macOS desktop bridge."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is required for the desktop startup test')
class MacDesktopStartupTests(unittest.TestCase):
    def run_node(self, body, *, initial_api='null', setup=''):
        source = (Path(__file__).parent / 'web/desktop_shell.js').read_text(encoding='utf-8')
        queue_start = source.index(' let materialQueue=Promise.resolve();')
        queue_end = source.index(' function labels()', queue_start)
        appearance = source[queue_start:queue_end]
        ready_start = source.index(' let readiness=null;')
        ready_end = source.index(" window.addEventListener('wuji-language'", ready_start)
        readiness = source[ready_start:ready_end]
        vm_source = r'''
let native=null,info=null,state=null;
const $=id=>document.getElementById(id);
function labels(){}
function addMacDragStrip(){}
SETUP
window.pywebview={api:INITIAL_API};
APPEARANCE
READINESS
globalThis.startupTest={dispatch:()=>window.dispatchEvent('pywebviewready'),setApi:api=>window.pywebview={api},
  getReadiness:()=>readiness,getDataset:()=>document.documentElement.dataset,getElement:id=>$(id)};
'''.replace('INITIAL_API', initial_api).replace('SETUP', setup)
        vm_source = vm_source.replace('APPEARANCE', appearance).replace('READINESS', readiness) + body
        script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const dataset={theme:'dark'};
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{checked:false,disabled:false,hidden:false,textContent:''});return elements.get(id);}
const document={documentElement:{dataset},getElementById:element,querySelector:()=>null,body:{prepend(){}}};
const handlers={};
const window={pywebview:null,WujiLocale:{lang:'en'},addEventListener(name,handler){(handlers[name]??=[]).push(handler);},
  dispatchEvent(name){for(const handler of handlers[name]||[])handler({type:name});}};
const localStorage={getItem(key){return key==='wuji-reduceTransparency'?'true':null;},setItem(){}};
const context={document,window,localStorage,Promise,console,setImmediate,process,assert,
  matchMedia:()=>({matches:false,addEventListener(){}})};
vm.createContext(context);
vm.runInContext(SOURCE,context);
'''.replace('SOURCE', json.dumps(vm_source))
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_bridge_does_not_initialize_but_ready_event_uses_completed_api(self):
        self.run_node(r'''
const calls=[];
const api={info(){calls.push('info');return Promise.resolve({platform:'macos',version:'test'});},
  set_language(){calls.push('language');return Promise.resolve();},
  set_window_material(enabled){calls.push(['material',enabled]);return Promise.resolve({external_backdrop:enabled,mode:enabled?'glass':'solid'});},
  set_window_appearance(theme){calls.push(['appearance',theme]);return Promise.resolve({external_backdrop:false,mode:'solid'});}};
(async()=>{
  assert.deepEqual(calls,[]);
  startupTest.dispatch();
  await new Promise(setImmediate);
  assert.deepEqual(calls,[]);
  startupTest.setApi(api);
  startupTest.dispatch();
  await startupTest.getReadiness();
  assert.deepEqual(calls,['info','language',['material',false],['appearance','dark']]);
  assert.equal(startupTest.getDataset().reduceTransparency,'true');
})().catch(error=>{console.error(error);process.exitCode=1;});
''', initial_api='{}')

    def test_preexisting_bridge_and_repeated_ready_event_initialize_once_with_saved_preference(self):
        self.run_node(r'''
startupTest.dispatch();
assert.deepEqual(calls,['info']);
const first=startupTest.getReadiness();
startupTest.dispatch();
assert.equal(startupTest.getReadiness(),first);
assert.deepEqual(calls,['info']);
releaseInfo({platform:'macos',version:'test'});
(async()=>{
  await first;
  assert.deepEqual(calls,['info','language',['material',false],['appearance','dark']]);
  assert.equal(startupTest.getDataset().reduceTransparency,'true');
})().catch(error=>{console.error(error);process.exitCode=1;});
''', initial_api='api', setup=r'''
let releaseInfo;const infoGate=new Promise(resolve=>releaseInfo=resolve);const calls=[];
const api={info(){calls.push('info');return infoGate;},
  set_language(){calls.push('language');return Promise.resolve();},
  set_window_material(enabled){calls.push(['material',enabled]);return Promise.resolve({external_backdrop:enabled,mode:enabled?'glass':'solid'});},
  set_window_appearance(theme){calls.push(['appearance',theme]);return Promise.resolve({external_backdrop:false,mode:'solid'});}};
''')

    def test_startup_failure_allows_a_later_ready_event_to_retry(self):
        self.run_node(r'''
(async()=>{
  startupTest.dispatch();
  await new Promise(setImmediate);
  assert.equal(startupTest.getReadiness(),null);
  assert.equal(startupTest.getElement('desktop-menu-status').textContent,'startup bridge failed');
  startupTest.dispatch();
  await startupTest.getReadiness();
  assert.deepEqual(calls,['info','info','language',['material',false],['appearance','dark']]);
})().catch(error=>{console.error(error);process.exitCode=1;});
''', initial_api='api', setup=r'''
let attempts=0;const calls=[];
const api={info(){calls.push('info');return ++attempts===1?Promise.reject(new Error('startup bridge failed')):
    Promise.resolve({platform:'macos',version:'test'});},
  set_language(){calls.push('language');return Promise.resolve();},
  set_window_material(enabled){calls.push(['material',enabled]);return Promise.resolve({external_backdrop:enabled,mode:enabled?'glass':'solid'});},
  set_window_appearance(theme){calls.push(['appearance',theme]);return Promise.resolve({external_backdrop:false,mode:'solid'});}};
''')


if __name__ == '__main__':
    unittest.main()
