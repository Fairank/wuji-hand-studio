/* Displayed diagnostic identity and the existing touch preview's async ownership. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const web=path.join(__dirname,'web');
function exportsFixture(report,id){
 const source=fs.readFileSync(path.join(web,'doctor.js'),'utf8'),start=source.indexOf(' const report=result?.report,container='),end=source.indexOf('  const date=',start);
 const nodes=Object.fromEntries(['doctor-rows','doctor-download','doctor-json','doctor-bundle'].map(name=>[name,{hidden:false,href:'latest',replaceChildren(){},removeAttribute(key){delete this[key]}}]));
 const begin=source.indexOf(' function bindReportExports('),finish=source.indexOf(' function render(',begin);
 const env={$:name=>nodes[name],result:{report,report_id:id}};vm.createContext(env);
 vm.runInContext((begin>=0?source.slice(begin,finish):'')+source.slice(start,end),env);return nodes;
}
test('all three ordinary export URLs bind the displayed report identity',()=>{const token='a'.repeat(64),n=exportsFixture({operation:'diagnose'},token);for(const name of ['doctor-download','doctor-json','doctor-bundle']){assert.equal(n[name].hidden,false);assert.equal(new URL(n[name].href,'http://isolated').searchParams.get('report_id'),token);}});
test('an unverified or absent report cannot leave a latest-report export link available',()=>{for(const token of [null,'bad','A'.repeat(64)]){const n=exportsFixture({operation:'diagnose'},token);for(const name of ['doctor-download','doctor-json','doctor-bundle']){assert.equal(n[name].hidden,true);assert.equal(n[name].href,undefined);}}});
function fixture(){
 const source=fs.readFileSync(path.join(web,'studio.js'),'utf8'),begin=source.indexOf('  let touchPending='),fallback=source.indexOf("  $('touch-flow').addEventListener('click',"),end=source.indexOf("  const pose=$('pose-console')",fallback),listeners={},button={disabled:false,addEventListener(name,fn){this[name]=fn}},location={hash:'#interaction'},calls=[],pending=[],errors=[];
 const env={location,$:id=>{assert.equal(id,'touch-flow');return button},window:{addEventListener(name,fn){(listeners[name]??=[]).push(fn)}},text:(zh,en)=>en,notice:e=>errors.push(e.message),post:body=>{calls.push(body);return new Promise((resolve,reject)=>pending.push({resolve,reject}))}};
 vm.createContext(env);vm.runInContext(`let state={csrf:'A'},intentRevision=0;${source.slice(begin>=0?begin:fallback,end)};globalThis.stateTo=value=>state=value;globalThis.bumpIntent=()=>intentRevision++;`,env);
 const publish=token=>{for(const fn of listeners['console-state']||[])fn({detail:{csrf:token}});env.stateTo({csrf:token})};
 const navigate=hash=>{location.hash=hash;for(const fn of listeners.hashchange||[])fn()};
 const offline=()=>{for(const fn of listeners['console-offline']||[])fn();env.stateTo(null)};
 publish('A');return {env,button,calls,pending,errors,location,publish,navigate,offline,start:()=>button.click()};
}
test('touch preview suppresses duplicate pending clicks and preserves normal completion',async()=>{const f=fixture(),first=f.start(),second=f.start();assert.equal(f.calls.length,1);assert.equal(f.button.disabled,true);f.pending[0].resolve({ok:true});await Promise.all([first,second]);assert.equal(f.location.hash,'feedback');assert.equal(f.button.disabled,false);});
test('navigation away and back retires automatic navigation from an old preview',async()=>{const f=fixture(),first=f.start();f.navigate('#home');f.navigate('#interaction');f.pending[0].resolve({ok:true});await first;assert.equal(f.location.hash,'#interaction');assert.deepEqual(f.errors,[]);});
for(const success of [true,false])test('old-service '+(success?'success':'failure')+' cannot clear a new request or change its page/error',async()=>{const f=fixture(),old=f.start();f.offline();f.publish('B');const next=f.start();assert.equal(f.calls.length,2);if(success)f.pending[0].resolve({ok:true});else f.pending[0].reject(Error('OLD A failure'));await old;assert.equal(f.button.disabled,true);assert.deepEqual(f.errors,[]);assert.equal(f.location.hash,'#interaction');f.pending[1].resolve({ok:true});await next;assert.equal(f.button.disabled,false);assert.equal(f.location.hash,'feedback');});
test('same-service explicit failure is retryable and does not navigate',async()=>{const f=fixture(),first=f.start();f.pending[0].reject(Error('Current failure'));await first;assert.equal(f.location.hash,'#interaction');assert.deepEqual(f.errors,['Current failure']);assert.equal(f.button.disabled,false);const retry=f.start();f.pending[1].resolve({ok:true});await retry;assert.equal(f.calls.length,2);assert.equal(f.location.hash,'feedback');});
test('a changed demonstration intent cannot be overwritten by touch preview completion',async()=>{const f=fixture(),first=f.start();f.env.bumpIntent();f.pending[0].resolve({ok:true});await first;assert.equal(f.location.hash,'#interaction');assert.deepEqual(f.errors,[]);});
