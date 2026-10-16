"""Independent static review requested by the user; read tools only."""
import json
import shutil
import subprocess
import time
from pathlib import Path
repo = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
diff = subprocess.check_output(['git','diff','--','backend','frontend'],cwd=repo,text=True)
prompt = '''Review this Defence Studio patch independently, read-only. Never edit files, run shell commands, access secrets/.env, contact providers, stage or commit. Read/Grep/Glob only. Find concrete correctness/security/status defects with severity and file/line. Focus on pipeline overview distinguishing durable PAUSED workflow from successful media artifacts, publishing failure attention, and tests. Review evidence scripts for honest claims and protection of production data. No full ARE integration or complete queue orchestration is claimed. Tamil/human/legal and external providers remain blocked; no paid calls/uploads. Ten-minute real renderer 1080p25 benchmark is running; do not infer success. Browser uses real Chrome, real app endpoints, copied synthetic fixture, simulated prior approvals; auth bypass disabled; failure injected only into run-owned copy. Your review is STATIC, do not claim you ran tests. Diff follows:\n''' + diff
prompt += '\nEvidence directory: ' + str(out) + '\nRead render_acceptance.py and browser_acceptance.mjs if useful. Fresh backend reports suite 8 passed, frontend production suite 19 passed. Initial frontend duplicate import was corrected; failed log retained. Initial browser harness used wrong video file name; corrected to video_branded, failed logs retained. Please also flag omitted real publishing error states.'
(out/'claude-request.txt').write_text(prompt,encoding='utf-8')
started=time.monotonic()
p=subprocess.run([shutil.which('claude'),'--print','--no-session-persistence','--permission-mode','plan',
                  '--tools','Read,Glob,Grep','--allowedTools','Read,Glob,Grep'],input=prompt,text=True,
                 encoding='utf-8',capture_output=True,cwd=repo,timeout=600)
(out/'claude-review.txt').write_text(p.stdout,encoding='utf-8')
(out/'claude-review.stderr').write_text(p.stderr,encoding='utf-8')
(out/'claude-result.json').write_text(json.dumps({'exit':p.returncode,'seconds':time.monotonic()-started,
                                               'scope':'read-only static review'},indent=2))
print('Claude exit',p.returncode)
