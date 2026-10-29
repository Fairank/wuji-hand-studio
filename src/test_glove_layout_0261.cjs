'use strict';
// Production layout helper with isolated DOM operations; no browser/network/SDK.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'web/glove.js'),'utf8');
const helper=source.slice(source.indexOf(' // Keep the same controls in visual and keyboard order;'),source.indexOf(' function render(){'));
assert(helper.includes('function syncOperationOrder()'));
function fixture({width=1366,stopHidden=false,native=true,focusPause=false}={}){
 const document={activeElement:null},moves=[],focusCalls=[],listeners={};
 const layout={dataset:{},children:[],get lastElementChild(){return this.children.at(-1);}};
 function node(id,children=[]){return {id,children,parentElement:layout,get nextElementSibling(){return layout.children[layout.children.indexOf(this)+1]||null;},contains(target){return target===this||this.children.includes(target);},focus(options){document.activeElement=this;focusCalls.push({id,options});}};}
 const pause=node('pause'),operation=node('operations',[pause]),controls=node('source'),display=node('display'),other=node('other');layout.children=[controls,display,operation];
 function move(item,next,method){const wasFocused=item.contains(document.activeElement);layout.children.splice(layout.children.indexOf(item),1);const position=next===null?layout.children.length:layout.children.indexOf(next);assert(position>=0);layout.children.splice(position,0,item);moves.push(method);if(method==='insertBefore'&&wasFocused)document.activeElement=null;}
 layout.insertBefore=(item,next)=>move(item,next,'insertBefore');if(native)layout.moveBefore=(item,next)=>move(item,next,'moveBefore');
 const stop={hidden:stopHidden},page={querySelector:s=>({'.glove-layout':layout,'.glove-controls':controls,'.glove-actionbar':operation}[s])},window={innerWidth:width,addEventListener:(n,fn)=>listeners[n]=fn};
 document.activeElement=focusPause?pause:other;
 const context={window,document,page,$:id=>id==='glove-stop'?stop:null};vm.runInNewContext(helper+'\nthis.sync=syncOperationOrder;',context);
 return {layout,pause,operation,controls,display,other,stop,window,document,moves,focusCalls,sync:context.sync,resize(width){window.innerWidth=width;listeners.resize();},order:()=>layout.children.map(n=>n.id)};
}
test('wide and narrow idle preserve original nodes and never reorder on repeated polls',()=>{for(const width of [1366,700,390]){const e=fixture({width,stopHidden:true});e.sync();e.sync();assert.deepEqual(e.order(),['source','display','operations']);assert.equal(e.moves.length,0);assert.equal(e.layout.dataset.actionFirst,'false');}});
test('an available narrow stop moves the existing operation bar once before source fields',()=>{const e=fixture({width:390,focusPause:true});e.sync();e.sync();assert.deepEqual(e.order(),['operations','source','display']);assert.deepEqual(e.moves,['moveBefore']);assert.equal(e.document.activeElement,e.pause);assert.equal(e.focusCalls.length,0);});
test('resize across the media boundary restores the original wide order without replacing controls',()=>{const e=fixture({width:1366,focusPause:true});e.sync();e.resize(700);assert.deepEqual(e.order(),['operations','source','display']);e.resize(701);assert.deepEqual(e.order(),['source','display','operations']);assert.equal(e.document.activeElement,e.pause);assert.equal(e.layout.children.at(-1),e.operation);assert.deepEqual(e.moves,['moveBefore','moveBefore']);});
test('confirmed pause hides the local stop and restores source-first layout',()=>{const e=fixture({width:390});e.sync();e.stop.hidden=true;e.sync();assert.deepEqual(e.order(),['source','display','operations']);assert.equal(e.layout.dataset.actionFirst,'false');});
test('older-browser fallback restores a focused control after each actual move',()=>{const e=fixture({width:1366,native:false,focusPause:true});e.resize(390);assert.equal(e.document.activeElement,e.pause);e.resize(1366);assert.equal(e.document.activeElement,e.pause);assert.deepEqual(e.moves,['insertBefore','insertBefore']);assert.equal(e.focusCalls.length,2);assert(e.focusCalls.every(c=>c.options.preventScroll===true));});
test('fallback does not steal focus from an unmoved source or mode control',()=>{const e=fixture({width:390,native:false});e.sync();assert.equal(e.document.activeElement,e.other);assert.equal(e.focusCalls.length,0);});
