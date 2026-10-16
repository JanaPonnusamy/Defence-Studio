# Execution commands

Repository root: `E:/Jana Soft/DefenceStudio`. Evidence directory below is abbreviated as `$ev` only in this document; its actual value is `AGENT_WORK/CODEX_EVIDENCE/next-e2e-gap-closure-20261016-0945`.

Baseline: `git status --short`, `git branch -vv`, `git log -5 --oneline`, `git rev-parse HEAD origin/main`. Production files were hashed/read only; scripts never initialize the protected databases.

```powershell
.audit-venv/Scripts/python.exe "$ev/start_services.py"
$env:E2E_DIR='C:/dstmp/next-live'
.audit-venv/Scripts/python.exe backend/scripts/e2e_acceptance.py Ukraine --out "$ev/live-e2e.json"
.audit-venv/Scripts/python.exe "$ev/long_render.py"
.audit-venv/Scripts/python.exe -u "$ev/run_suites.py"
python -m venv C:/dstmp/next-clean-0945
C:/dstmp/next-clean-0945/Scripts/python.exe -m pip install -e './backend[test]'
C:/dstmp/next-clean-0945/Scripts/python.exe -m pip check
C:/dstmp/next-clean-0945/Scripts/python.exe -m pip freeze
```

`run_suites.py` records exact interpreter, cwd, arguments, exit and wall time for the old-environment backend and each standalone/frontend run in `fresh-suites.json`. Every pytest invocation uses `-q -ra` there. Backend needs editable installation or an explicit PYTHONPATH; the old audit environment was not modified to hide its failures.

Clean broad backend (cwd `backend`):

```powershell
C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest -q -ra --basetemp C:/dstmp/nc
```

Core affected suites (cwd repository root; clean interpreter below):

```powershell
C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest -q backend/tests/test_render_rm5.py backend/tests/test_assets_ingest_rm9.py backend/tests/test_qc_rm6.py backend/tests/test_package_rm7.py
C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest -q backend/tests/test_render_rm5.py backend/tests/test_assets_ingest_rm9.py backend/tests/test_qc_rm6.py backend/tests/test_package_rm7.py backend/tests/test_youtube_rm8.py
C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest -q backend/tests/test_render_rm5.py::test_rights_changed_after_visual_plan_blocks_render backend/tests/test_youtube_rm8.py
C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest -q backend/tests/test_youtube_rm8.py::test_revoked_rights_are_terminal_through_publish_worker backend/tests/test_youtube_rm8.py::test_permanent_errors_are_final_and_bounded_retries_end backend/tests/test_youtube_rm8.py::test_cancel_before_upload_and_cross_owner_isolation backend/tests/test_youtube_rm8.py::test_illegal_transition_rejected_server_side backend/tests/test_youtube_rm8.py::test_public_publication_only_after_confirmation_and_after_assets
C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest -q backend/tests/test_youtube_rm8.py::test_revoked_rights_are_terminal_through_publish_worker
```

Before/after isolated regressions used their same test node paths separately: `test_rights_changed_after_visual_plan_blocks_render`, `test_rights_revoked_before_worker_start_marks_render_failed`, and `test_revoked_rights_are_terminal_through_publish_worker`. Their before/after logs/exits preserve observed failures and fixes. `final-rights-regression.log` uses four nodes: changed-rights render, valid full render, expiry and non-web rights. Test-summary durations are pytest wall times; command metadata records outer process wall time where available.

Fixture and independent verification, all cwd repository root:

```powershell
C:/dstmp/next-clean-0945/Scripts/python.exe -u "$ev/fixture_e2e.py"
C:/dstmp/next-clean-0945/Scripts/python.exe "$ev/restart_check.py"
C:/dstmp/next-clean-0945/Scripts/python.exe "$ev/validate_outputs.py"
C:/dstmp/next-clean-0945/Scripts/python.exe "$ev/real_rights_revocation.py"
C:/dstmp/next-clean-0945/Scripts/python.exe "$ev/rights_restart_check.py"
ffmpeg -hide_banner -i 'E:/Jana Soft/DefenceStudio/tmp/next-long-0945/storage/renders/r_764e7fd6b58744/output.mp4' -vn -af volumedetect -f null -
```

`real_rights_revocation.py` intentionally refuses an existing test-copy directory. Preserve that directory; choose a new isolated destination for a new run. `validate_outputs.py` records each exact ffprobe/decode argument array, exit and duration. Fixtures never contact live feeds. The 600-second artifact uses the real production renderer job path at its explicitly reduced profile.

Independent Claude calls are fully defined in `claude_review.py`, `claude_publish_review.py`, and `final_cancel_review.py`. They permit Read/Glob/Grep only or no tools, prohibit mutation/provider access, and retain stdout/exit/duration. No live Google, hosted narration, paid image generation, model download or production approval command was executed.
