/* Actual picker source; deterministic presentation events, no service/devices. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/action_picker.js'),'utf8');
function fixture(){
 class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.value='';this.textContent='';this.open=false;}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(){}addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}querySelectorAll(){return [];}closest(){return null;}
  showModal(){this.open=true;}close(){this.open=false;}focus(){}select(){}
  emit(type){for(const fn of this.listeners[type]||[])fn({preventDefault(){}});}
  get options(){return this.children.flatMap(n=>n.tag==='optgroup'?n.children:[n]);}
 }
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
 const window={},changes=[],document={getElementById:get,createElement:tag=>new Element(tag),body:new Element()};
 const ctx={window,document,Option:function(text,value){const n=new Element('option');n.textContent=text;n.value=value;return n;},URL,encodeURIComponent};vm.createContext(ctx);vm.runInContext(source,ctx);
 const picker=window.WujiActionPicker.create({root:new Element(),text:(zh,en)=>en,onSelect:id=>changes.push(id)});
 const choose=id=>{get('studio-action').value=id;get('studio-action').emit('change');};
 return {picker,changes,get,choose};
}
const second=[{id:'official_opposition',zh:'录制',en:'Replay',hardware:true,group:'official'},{id:'open',zh:'张开',en:'Open',hardware:true,group:'basic'},{id:'fist',zh:'握拳',en:'Fist',hardware:true,group:'basic'}];
const first=second.map(a=>({...a,hardware:a.id==='open'||a.id==='fist'})).concat([{id:'dance_piano',zh:'候选',en:'Candidate',hardware:false,group:'dance'},{id:'digit_1',zh:'1',en:'1',hardware:false,group:'numbers'},{id:'text_sequence',zh:'文字',en:'Text',hardware:false,group:'letters'}]);

test('Hand2 official choice becomes matching supported open when Hand1 catalog arrives',()=>{
 const f=fixture();f.picker.setCatalog(second);f.choose('official_opposition');f.picker.setCatalog(first);
 assert.equal(f.picker.selected,'open');assert.equal(f.changes.at(-1),'open');f.picker.setPreview(false);assert.equal(f.picker.selected,'open');
});
test('initial Hand1 real mode exposes only eligible actions and rejects injected preview choices',()=>{
 const f=fixture();f.picker.setCatalog(first);f.picker.setBusy(false);
 assert.equal(f.picker.selected,'open');assert.deepEqual(f.get('studio-action').options.map(o=>o.value),['open','fist']);
 assert.equal(f.get('choose-letter').disabled,true);assert.equal(f.get('choose-digit').disabled,true);
 for(const id of ['official_opposition','dance_piano','digit_1','text_sequence'])f.choose(id);
 assert.equal(f.picker.selected,'open');assert.equal(f.changes.at(-1),'open');
});
test('candidate remains selectable in preview then returns to valid Hand1 hardware selection',()=>{
 const f=fixture();f.picker.setCatalog(second);f.picker.setPreview(true);f.choose('official_opposition');f.picker.setCatalog(first);
 assert.equal(f.picker.selected,'official_opposition');f.choose('dance_piano');assert.equal(f.picker.selected,'dance_piano');
 f.picker.setPreview(false);assert.equal(f.picker.selected,'open');assert.equal(f.changes.at(-1),'open');
});
test('supported remembered choice survives catalogs while preview availability follows mode',()=>{
 const f=fixture();f.picker.setCatalog(second);f.choose('fist');f.picker.setCatalog(first);assert.equal(f.picker.selected,'fist');
 f.picker.setPreview(true);f.picker.setBusy(false);assert.equal(f.get('choose-letter').disabled,false);assert.equal(f.get('choose-digit').disabled,false);
 f.choose('official_opposition');f.picker.setPreview(false);assert.equal(f.picker.selected,'fist');assert.equal(f.changes.at(-1),'fist');
});
