"""User-requested independent review, restricted to read tools."""
import json,shutil,subprocess,time
from pathlib import Path
repo=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
diff=subprocess.check_output(['git','diff','--','backend'],cwd=repo,text=True)
prompt='''Perform an independent read-only review of this Defence Studio change. Never edit, stage, commit, run shell commands, access credentials/.env files, or contact providers. Read/Grep/Glob only. Review correctness and security of current source and tests, status/progress semantics, rights revocation/expiry/use restrictions at render/export boundaries, and evidence claims. Do not infer human/legal acceptance. Identify concrete defects with file/line and severity, or say none found. Distinguish execution evidence from your static review. The production protected script must remain AWAITING_REVIEW. Full regression is still running: do not claim it passed. Core workflow queue remains PAUSED although media-chain fixture passed. Original baseline was 1933a62; concurrent independent evidence-only commit cbf380d arrived and is preserved.\n\nDiff:\n'''+diff
for name in ['fixture-e2e.json','restart-check.json','artifact-and-protection-validation.json','live-e2e.json','real-rights-revocation.json']:
    prompt+='\nEvidence '+name+':\n'+(out/name).read_text(encoding='utf-8')
prompt+='\nFresh focused tests: final-rights-regression.log (4 passed), affected-tests.log (42 passed,1 skipped). Long renderer full JSON at '+str(out/'long-render.json')+'. Full1080p acceptance NOT_RUN; reduced 640x360 12fps 600s fixture only. Engine component ARE remains not imported by core; patch hardens existing core rights checks. Tamil remains blocked with no new provider. Please verify claims against files and return findings and limitations.'
prompt+='\nFollow-up: our own audit found a new check-order race after your initial review: checking rights before transitioning QUEUED to RUNNING left a revoked job QUEUED because failure fencing accepts only RUNNING. queued-rights-before.log reproduces QUEUED vs expected FAILED. The check is now immediately after the conditional RUNNING transition. queued-rights-after.log is 1 passed, exit 0, 12.62s. Please specifically inspect cancellation/failure safety of this corrected order and the new regression. Reconstructing a service in real-rights-revocation.json is SAME PROCESS; actual fresh-process persistence is restart-check.json. Final broad tests still running; do not claim them passed.'
(out/'claude-review-request.txt').write_text(prompt,encoding='utf-8')
start=time.monotonic()
p=subprocess.run([shutil.which('claude'),'--print','--no-session-persistence','--permission-mode','plan','--tools','Read,Glob,Grep','--allowedTools','Read,Glob,Grep'],input=prompt,text=True,encoding='utf-8',capture_output=True,cwd=repo,timeout=600)
(out/'claude-review.txt').write_text(p.stdout,encoding='utf-8')
(out/'claude-review.stderr').write_text(p.stderr,encoding='utf-8')
(out/'claude-review-result.json').write_text(json.dumps({'exit':p.returncode,'seconds':time.monotonic()-start,'tools':'Read,Glob,Grep','mode':'plan'},indent=2))
print('Claude review exit:',p.returncode)
