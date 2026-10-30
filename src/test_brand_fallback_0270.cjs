/* Execute the actual brand entry with no stylesheets, network, or device I/O. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/brand.js'),'utf8');
function render(hasHeading=true){const inserted=[],removed=[];const document={querySelector:()=>hasHeading?{before:e=>inserted.push(e)}:null,createElement:tag=>({tagName:tag.toUpperCase()}),querySelectorAll:()=>[{remove:()=>removed.push(true)}]};vm.runInNewContext(source,{document});return {inserted,removed};}
test('the brand has a small intrinsic fallback before any stylesheet loads',()=>{const {inserted}=render();assert.equal(inserted.length,1);const logo=inserted[0];assert.equal(logo.tagName,'IMG');assert.equal(logo.src,'/wuji-logo.png');assert.equal(logo.alt,'WUJI');assert.equal(logo.width,48);assert.equal(logo.height,14);assert.equal(logo.className,'studio-brand');});
test('the fallback leaves responsive CSS in control without inline dimensions',()=>{const {inserted}=render();assert.equal(inserted[0].style,undefined);assert.equal(inserted[0].className,'studio-brand');});
test('a page without the header does not create an orphan image',()=>{const {inserted,removed}=render(false);assert.equal(inserted.length,0);assert.equal(removed.length,1);});
