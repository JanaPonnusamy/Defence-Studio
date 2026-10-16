// Real Chrome/CDP and application HTTP; retained synthetic fixture, no provider mocks in browser.
import { launch, sleep } from '../../../frontend/tests/browser/cdp.mjs';
import { readFileSync, writeFileSync } from 'node:fs';
import { createHmac, createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const out = fileURLToPath(new URL('.', import.meta.url));
const state = JSON.parse(readFileSync(out + 'browser-state.json'));
const BASE = 'http://127.0.0.1:8037';
const encode = x => Buffer.from(JSON.stringify(x)).toString('base64url');
function token(owner) {
  const body = encode({alg:'HS256',typ:'JWT'}) + '.' + encode({sub:owner,exp:Math.floor(Date.now()/1000)+3600});
  return body + '.' + createHmac('sha256','production-browser-isolated-test-secret-20261016').update(body).digest('base64url');
}
const results = [];
function check(id, ok, note) { results.push({id,ok,note}); console.log(ok?'PASS':'FAIL',id,note); }
const b = await launch(9357);
const api = async (path, owner=state.owner, method='GET', body) => b.eval(`fetch(${JSON.stringify('/api'+path)}, {
  method:${JSON.stringify(method)}, headers:{'Content-Type':'application/json', 'Idempotency-Key':'browser-auth-probe',
  ${owner === null ? '' : `'Authorization':${JSON.stringify('Bearer '+token(owner))}`}},
  ${body ? 'body:'+JSON.stringify(JSON.stringify(body)) : ''}}).then(async r=>({status:r.status,body:await r.text()}))`);
try {
  await b.goto(BASE);
  await b.waitFor(`!!document.querySelector('#login-token-field')`,12000,'token login');
  check('auth-bypass-disabled', !(await b.eval(`!!document.querySelector('#dev-enter-btn')`)), 'production bearer login required');
  const anonymous = await api('/jobs',null);
  check('anonymous-jobs-denied',anonymous.status===401,anonymous.status);
  await b.setValue('#login-token-field',token(state.owner));
  await b.eval(`document.querySelector('#login-token-form').requestSubmit()`);
  await b.waitFor(`!!document.querySelector('[data-tab="production"]')`,12000,'authenticated workspace');
  const jobs = await api('/jobs');
  check('candidate-job-visible',jobs.status===200 && jobs.body.includes(state.job_id),'persisted fixture job listed');
  await b.eval(`localStorage.setItem('ds_review_job_id',${JSON.stringify(state.job_id)})`);
  await b.goto(BASE+'/#production');
  await b.send('Page.reload');
  await b.waitFor(`!!document.querySelector('[data-workflow-status="PAUSED"]')`,20000,'paused workflow overview');
  const overview = JSON.parse((await api(`/jobs/${state.job_id}/pipeline/en`)).body);
  const queue = await b.eval(`document.querySelector('[data-workflow-status]').innerText`);
  check('queue-state-matches-api',overview.workflow.status==='PAUSED' && queue.includes('PAUSED') && queue.includes(`${overview.workflow.stages_completed} / ${overview.workflow.stages_total}`),queue);
  check('media-not-queue-completion',queue.includes('separate from queue completion'),'media outputs coexist with paused queue');
  const packageInfo=JSON.parse((await api(`/jobs/${state.job_id}/package/en`)).body).items.at(-1);
  const manifest=JSON.parse((await api(`/jobs/${state.job_id}/package/en/${packageInfo.version}`)).body);
  // Endpoint returns the saved manifest body (verify bytes independently).
  const files=manifest.files || manifest.body?.files;
  if (!files) throw Error('manifest response has no files: '+Object.keys(manifest));
  const ref=files.video_branded;
  const response=await fetch(`${BASE}/api/jobs/${state.job_id}/package/en/${packageInfo.version}/file/video_branded`,{headers:{Authorization:'Bearer '+token(state.owner)}});
  const hash=createHash('sha256').update(Buffer.from(await response.arrayBuffer())).digest('hex');
  check('authorized-output-hash',response.status===200 && hash===ref.sha256,hash);
  for (const [name,path,method,body] of [
    ['approval',`/jobs/${state.job_id}/script/revisions/en/1/approve`,'POST',{revision_sha256:'0'.repeat(64),note:'unauthorized isolated probe'}],
    ['export',`/jobs/${state.job_id}/package/en/${packageInfo.version}/file/video_branded`,'GET'],
    ['publish',`/youtube/jobs/nonexistent/queue`,'POST'],
  ]) {
    for (const owner of [null,'other-owner']) {
      const r=await api(path,owner,method,body);
      check(`${name}-${owner===null?'anonymous':'other-owner'}-denied`,r.status===(owner===null?401:404),r.status);
    }
  }
  for (const width of [1440,768,390]) {
    await b.viewport(width,900); await sleep(300);
    const metrics=await b.metrics();
    check('viewport-'+width,!metrics.overflowX,JSON.stringify(metrics));
    await b.shot(out+`browser-production-${width}.png`);
  }
  await b.send('Page.reload');
  await b.waitFor(`!!document.querySelector('[data-workflow-status="PAUSED"]')`,20000,'refresh persisted state');
  check('refresh-keeps-review-pause',true,'re-read same durable queue state');
  // Inject a backend failure only into this run-owned disposable fixture, then reconnect.
  if (!state.database.startsWith('C:\\dstmp\\production-browser-20261016-1155\\')) throw Error('unsafe fixture path');
  execFileSync('C:/dstmp/next-clean-0945/Scripts/python.exe',['-c',
    `import sqlite3,sys\nwith sqlite3.connect(sys.argv[1]) as c:\n c.execute("UPDATE integration_artifacts SET status='FAILED',detail='Injected browser acceptance failure' WHERE kind='render' AND job_id=?",(sys.argv[2],))`,state.database,state.job_id]);
  await b.send('Page.reload');
  await b.waitFor(`document.body.innerText.includes('Injected browser acceptance failure')`,20000,'persisted failure display');
  const failed=await b.eval(`document.querySelector('[data-section="overview"]').innerText`);
  check('backend-failure-visible',failed.includes('FAILED') && /Needs attention:.*render/.test(failed),failed.slice(0,500));
  await b.shot(out+'browser-failed.png');
  check('no-browser-exceptions',b.log.exceptions.length===0,JSON.stringify(b.log.exceptions));
} catch(e) { check('harness',false,e.stack); }
finally { b.close(); writeFileSync(out+'browser-results.json',JSON.stringify({scope:state.scope,results,exceptions:b.log.exceptions},null,2)); }
process.exitCode=results.some(r=>!r.ok)?1:0;
