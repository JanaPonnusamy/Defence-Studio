import {launch,sleep} from '../../../frontend/tests/browser/cdp.mjs';
import {readFileSync,writeFileSync} from 'node:fs';
import {createHmac} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const out=fileURLToPath(new URL('.',import.meta.url));
const state=JSON.parse(readFileSync(out+'browser-state.json'));
const enc=x=>Buffer.from(JSON.stringify(x)).toString('base64url');
const body=enc({alg:'HS256',typ:'JWT'})+'.'+enc({sub:state.owner,exp:Math.floor(Date.now()/1000)+3600});
const token=body+'.'+createHmac('sha256','content-browser-isolated-signing-secret-20261016').update(body).digest('base64url');
const b=await launch(9359); const results=[];
function check(id,ok,note){results.push({id,ok,note});console.log(ok?'PASS':'FAIL',id,note);}
const calls=()=>readFileSync(state.calls_file,'utf8').trim().split('\n').filter(Boolean).length;
try{
 await b.goto('http://127.0.0.1:8038/');
 await b.waitFor(`!!document.querySelector('#login-token-field')`,12000);
 await b.setValue('#login-token-field',token);
 await b.eval(`document.querySelector('#login-token-form').requestSubmit()`);
 await b.waitFor(`!!document.querySelector('[data-tab="script"]')`,12000);
 await b.eval(`localStorage.setItem('ds_review_job_id',${JSON.stringify(state.job_id)})`);
 await b.goto('http://127.0.0.1:8038/#/script'); await b.send('Page.reload');
 await b.waitFor(`document.querySelector('#gen-submit')?.disabled===false`,12000);
 await b.setValue('#gen-language','en'); await b.setValue('#gen-words','200');
 await b.eval(`document.querySelector('#script-generate-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));document.querySelector('#script-generate-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))`);
 await b.waitFor(`document.querySelector('#script-gen-status')?.textContent.includes('Draft saved')`,15000);
 await b.waitFor(`document.querySelector('#detail-panel')?.innerText.includes('Varunastra') || [...document.querySelectorAll('.detail-overlay')].some(e=>e.innerText.includes('Varunastra'))`,12000);
 check('draft-opened-automatically',await b.eval(`document.body.innerText.includes('Varunastra')`),'actual saved narration shown');
 check('double-submit-one-provider-call',calls()===1,calls());
 await b.shot(out+'content-saved.png');
 await b.send('Page.reload');
 await b.waitFor(`!!document.querySelector('[data-open-revision="en:1"]')`,12000);
 await b.click('[data-open-revision="en:1"]'); await sleep(700);
 check('refresh-retrieves-draft',await b.eval(`document.body.innerText.includes('Varunastra')`),'saved revision reopened');
 for(const mode of ['invalid','empty','error']){
   writeFileSync(state.mode_file,mode);
   await b.setValue('#gen-language','en'); await b.setValue('#gen-words','200');
   await b.eval(`document.querySelector('#script-generate-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))`);
   await b.waitFor(`(()=>{const t=document.querySelector('#script-gen-status')?.textContent||'';return !!t && !t.startsWith('Generating')})()`,15000,mode+' error display');
   const text=await b.eval(`document.querySelector('#script-gen-status').textContent`);
   check(mode+'-not-success',!text.includes('Draft saved') && /fail|invalid|empty|unavailable|JSON|schema/i.test(text),text);
   await b.shot(out+'content-'+mode+'.png');
 }
 check('no-browser-exceptions',b.log.exceptions.length===0,JSON.stringify(b.log.exceptions));
}catch(e){check('harness',false,e.stack);}
writeFileSync(out+'browser-results.json',JSON.stringify({scope:state.scope,results},null,2));
// User requested no applications be closed. Leave the owned test browser/server running.
process.exit(results.some(r=>!r.ok)?1:0);
