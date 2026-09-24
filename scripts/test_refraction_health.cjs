const assert=require('node:assert/strict');
const {RefractionHealth,MAX_GAP_MS}=require('../src/web/refraction_health.js');

assert.equal(MAX_GAP_MS,31);
const monitor=new RefractionHealth();
for(let i=0;i<120;i++)assert.equal(monitor.display(i*1000/60,true),null);
assert.equal(monitor.stats().display_hz,60);
assert.equal(monitor.display(120*1000/60+34,true).kind,'display');

monitor.reset();
for(let i=0;i<120;i++)assert.equal(monitor.display(i*1000/120,true),null);
assert.equal(monitor.stats().display_hz,120);
monitor.display(5000,false); // A hidden/inactive surface must not count as a stall.
assert.equal(monitor.display(6000,true),null);

monitor.reset();
assert.equal(monitor.processing(16,true),null);
assert.equal(monitor.processing(35,true).kind,'processing');
assert.equal(monitor.stats().last_fault.gap_ms,35);
assert.equal(monitor.stats().mean_processing_ms,25.5);
monitor.reset();
for(let i=0;i<30;i++)assert.equal(monitor.display(i*20,true),null);
assert.equal(monitor.display(30*20,true).kind,'display_rate');
monitor.reset();
assert.equal(monitor.capture(30,11),null);
assert.equal(monitor.capture(60,12),null);
assert.equal(monitor.capture(30,12).kind,'capture_rate');
monitor.reset();
assert.equal(monitor.present(100,11),null);
for(let i=0;i<30;i++)assert.equal(monitor.present(200+i*1000/60,12),null);
assert.equal(monitor.stats().presented_hz,60);
assert.equal(monitor.present(200+30*1000/60+40,12).kind,'presentation');
monitor.reset();
for(let i=0;i<30;i++)assert.equal(monitor.present(i*20,12),null);
assert.equal(monitor.present(600,12).kind,'presentation_rate');
monitor.present(1000,0);
assert.equal(monitor.stats().presented_hz,null);
console.log('Refraction 60/120 Hz and stall diagnostic checks passed');
