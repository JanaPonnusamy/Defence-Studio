import {launch} from '../../../frontend/tests/browser/cdp.mjs';
import {writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const out=fileURLToPath(new URL('./',import.meta.url));
const b=await launch(9347);
const results=[];
function check(name,ok,details=''){results.push({name,ok,details});if(!ok)throw Error(name+': '+details);}
try {
 await b.goto('http://127.0.0.1:8047/ui/');
 await b.click('[data-tab="creator"]');
 await b.waitFor("document.querySelector('#deliveryPreset').options.length===5",10000,'profiles');
 check('five original presets',true);
 await b.setValue('#providerSelect','mock-tts');
 check('unsupported mock controls disabled',await b.eval("document.querySelector('#deliveryControls').disabled"));
 await b.setValue('#providerSelect','local-pyttsx3');
 check('real controls enabled',await b.eval("!document.querySelector('#deliveryControls').disabled"));
 check('inventory visible',await b.eval("document.querySelector('#providerCapabilities').textContent.includes('en-us')"));
 await b.setValue('#ttsForm [name=language]','ta');
 check('Tamil unavailable is disabled',await b.eval("document.querySelector('#ttsForm button[type=submit]').disabled"));
 await b.setValue('#ttsForm [name=language]','en');
 await b.setValue('#deliveryPreset','calm');
 check('calm sets requested rate',await b.eval("document.querySelector('#ttsForm [name=rate_wpm]').value==='140'"));
 await b.setValue('#ttsForm [name=text]','This original briefing separates evidence from speculation. Please review the details carefully.');
 await b.click('#ttsForm button[type=submit]');
 await b.waitFor("document.querySelectorAll('#ttsResult audio').length===1",90000,'real synthesis');
 check('real measured result',await b.eval("document.querySelector('#ttsResult').textContent.includes('pitch_hz') && document.querySelector('#ttsResult').textContent.includes('unmetered_local_estimate')"));
 await b.waitFor("document.querySelector('#ttsResult audio').readyState>=1",10000,'audio decode');
 check('browser decoded playable speech artifact',await b.eval("document.querySelector('#ttsResult audio').duration>1"));
 await b.setValue('#deliveryPreset','urgent');
 await b.click('#ttsForm button[type=submit]');
 await b.waitFor("document.querySelectorAll('#ttsResult audio').length===2",90000,'comparison');
 check('comparison preserves both samples',true);
 check('downloadable metadata',await b.eval("document.querySelectorAll('#ttsResult a[download]').length===2"));
 const generated=await b.eval("[...document.querySelectorAll('#ttsResult pre')].map(e=>JSON.parse(e.textContent))");
 for (let i=0;i<generated.length;i++) {
   writeFileSync(out+`preview-${i}.json`,JSON.stringify(generated[i],null,2));
   writeFileSync(out+`preview-${i}.wav`,Buffer.from(await (await fetch('http://127.0.0.1:8047'+generated[i].stream_url)).arrayBuffer()));
 }
 await b.shot(out+'preview-desktop.png');
 await b.viewport(390,844);
 check('mobile has no horizontal overflow',!(await b.metrics()).overflowX);
 await b.shot(out+'preview-mobile.png');
 check('no browser exceptions',b.log.exceptions.length===0,JSON.stringify(b.log.exceptions));
} finally {
 writeFileSync(out+'browser-results.json',JSON.stringify(results,null,2));
 writeFileSync(out+'browser-finished.txt','done');
 b.close();
}

