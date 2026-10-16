"""Final narrow static review; no tools are available to the reviewer."""
import json,shutil,subprocess,time
from pathlib import Path
repo=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
source=(repo/'backend/defence_studio/services/youtube.py').read_text()
test=(repo/'backend/tests/test_youtube_rm8.py').read_text()
prompt='''Final narrow independent static review. Previous broader Claude review found no actionable defects after the permanent rights-failure fix. The last small change explicitly handles CANCEL_REQUESTED in the permanent-precondition handler: finish cancellation rather than attempting an illegal FAILED_FINAL transition. This source and test are copied verbatim from final working files. No tools, writes, providers or commands are permitted/available. Check the permanent IntegrationStale exception handling and state transitions for cancellation correctness. Final focused regression publisher-cancel-final.log: 1 passed,29.71s; it exercises eight permanent-failure states, prevents provider access/retry entry, then requests cancellation inside the rights gate. Final lifecycle subset before that tiny guard:5 passed116.88s. Return any concrete actionable defect, or no actionable defect; state review is static only.\n'''
prompt+=source[source.index('TRANSITIONS ='):source.index('TERMINAL =')]
prompt+='\n'+source[source.index('    def _advance('):source.index('    def _load(')]
prompt+='\n'+test[test.index('def test_revoked_rights_are_terminal_through_publish_worker'):test.index('def test_private_upload_thumbnail_captions_verified_and_audited')]
start=time.monotonic()
p=subprocess.run([shutil.which('claude'),'--print','--no-session-persistence','--tools',''],input=prompt,text=True,encoding='utf-8',capture_output=True,cwd=repo,timeout=300)
(out/'claude-final-cancel-review.txt').write_text(p.stdout,encoding='utf-8')
(out/'claude-final-cancel-review-result.json').write_text(json.dumps({'exit':p.returncode,'seconds':time.monotonic()-start,'tools':'none'},indent=2))
print('Final narrow review exit:',p.returncode)
