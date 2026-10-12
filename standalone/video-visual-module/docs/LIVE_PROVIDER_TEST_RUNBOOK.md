# Live OpenAI Provider Test — Runbook

**Status: prepared, not executed.** No real generation has been run on this module. This runbook takes an operator through **one** paid image generation with `openai-images`, plus the free checks before and after it.

**Charges.** Only **Step 5** can bill. Every other step is free:
- Steps 1–3, 6, 7 and 9 make no OpenAI request.
- Step 4 calls `GET /v1/models/{model}`, which never generates anything and is not billed.
- Step 8 uses a deliberately invalid key, and OpenAI does not bill requests that fail authentication.

**Tool.** Everything runs through `scripts\live_provider_check.py`. It:
- runs the module in-process against a **throwaway storage root** at `%TEMP%\vvm_live_check\<run-id>`, so the module's real `storage\` and user data are never touched;
- never prints, logs or stores the key; it shows only a boolean and the length;
- defaults to a dry run;
- **is not collected by pytest**, because pytest only collects `tests\`.

For `generate`, three gates must all pass:
1. `VVM_ENABLE_PAID_PROVIDER_TESTS=1` is set in the session.
2. The `--confirm-paid` flag is given.
3. The documented pre-call estimate is no more than `--max-usd` (default 0.01).

Two further safeguards apply during the run:
- **Retries are forced to 0** (`VIDEO_MODULE_GENERATION_MAX_RETRIES=0` and `VIDEO_MODULE_BILLABLE_GENERATION_MAX_RETRIES=0`). Since 2026-10-12 the module never retries billable requests by default anyway; this is belt and braces.
- **A guard transport refuses any second `POST /images/generations`.** A timed-out request may still have been processed and billed, so it must never be resent automatically.

All commands are Windows PowerShell 5.1, run from the module root:

```powershell
cd "D:\Jana Soft\defenceStudio\standalone\video-visual-module"
$py = ".\.venv\Scripts\python.exe"
```

## Cost of the single test

| Setting used | Value |
|---|---|
| Model | `gpt-image-2` (module default; snapshot `gpt-image-2-2026-04-21`) |
| Module quality | `draft`, sent to OpenAI as `quality: "low"` |
| Module resolution | `1536x1024` (sent as the preset `size: "1536x1024"`) |
| `n` / format | 1 / `png` |
| **Documented price** | **USD 0.005 per image** (gpt-image-2, low, 1536×1024). Per-image table in the official image-generation guide, retrieved 2026-10-12. |
| Plus prompt text tokens | about 20 tokens × $5.00 / 1M ≈ USD 0.0001 |
| **Expected total** | **≈ USD 0.005–0.006** |
| **Worst case if settings were ignored** | USD 0.211, the gpt-image-2 *high* 1024×1024 price. The module always sends `quality: "low"` explicitly, so this is a theoretical ceiling, not an expected cost. |
| Hard cap in the script | `--max-usd 0.01`. The project budget is also set to this value, so the module's own budget guard enforces it too. |

1536×1024 low ($0.005) is the **cheapest preset size and quality** this module can request. 1024×1024 low costs $0.006.

**Re-check the price on the day you run the test:**
- <https://developers.openai.com/api/docs/pricing>
- <https://developers.openai.com/api/docs/guides/image-generation> (see "cost" / calculator)

If the price has changed, update `app/generation/openai_pricing.py` first.

**Deprecation note.** OpenAI's deprecations page lists `gpt-image-1` for shutdown on 2026-10-23, and `gpt-image-1.5`, `gpt-image-1-mini` and `chatgpt-image-latest` for shutdown on 2026-12-01. `gpt-image-2` is current. Do not switch the model for this test.

---

## Step 1 — Set the key without echoing it (free)

**Option A (recommended): session environment variable.** It disappears when the window closes.

```powershell
$sec = Read-Host -AsSecureString "Paste OpenAI API key (input hidden)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); Remove-Variable sec, bstr
```

**Option B: the module `.env` file.** It persists on disk and is git-ignored. Only use it if you must; delete the line in Step 9.

```powershell
$sec = Read-Host -AsSecureString "Paste OpenAI API key (input hidden)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
Add-Content -Path .env -Value ("OPENAI_API_KEY=" + [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)) -Encoding ascii
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); Remove-Variable sec, bstr
```

With the audit fix, pytest, the self-test and the browser check now neutralise a `.env` key. However, `scripts\serve.py` and any normally started server **will** use it.

Do not paste the key on a command line, into a prompt, or into a file other than `.env`.

## Step 2 — Confirm the key is present without displaying it (free)

```powershell
"key set: $([bool]$env:OPENAI_API_KEY)  length: $("$env:OPENAI_API_KEY".Length)"
& $py scripts\live_provider_check.py            # dry run: key present True/False + length + source, estimate
```

Expected output: `key present: True (length NN, source env:OPENAI_API_KEY)` or `source module .env`. The value is never shown.

## Step 3 — Explicitly enable paid testing (free)

```powershell
$env:VVM_ENABLE_PAID_PROVIDER_TESTS = "1"
& $py scripts\live_provider_check.py            # must now show VVM_ENABLE_PAID_PROVIDER_TESTS=1: True and estimate USD 0.005
```

Do **not** set `VVM_LIVE_GENERATE`. That flag belongs to the pytest opt-in file and is not needed here.

## Step 4 — Configuration and connectivity check (free, not billed)

```powershell
& $py scripts\live_provider_check.py status     # config only, no network: expect "state": "AVAILABLE"
& $py scripts\live_provider_check.py probe      # ONE GET /v1/models/gpt-image-2: expect PASS, "AVAILABLE", POST_generation 0
```

Why this does not bill:
- The probe calls only the models endpoint.
- The script's guard transport counts requests. `"POST_generation": 0` confirms that no generation request was sent.
- OpenAI bills image generation per token generated, and listing or retrieving models generates nothing.

If the probe reports `FAILED (HTTP 401/403)`, the key is wrong or lacks access. If it reports `FAILED (model not available)`, the organisation may need verification for GPT Image models. In either case, **stop**.

The pytest equivalent is `& $py -m pytest tests\test_provider_live_optin.py`. With only `VVM_ENABLE_PAID_PROVIDER_TESTS=1` set, it runs just `test_live_connectivity` (also free). `test_live_generation` stays skipped.

## Step 5 — ONE real generation (**BILLABLE, about USD 0.005**)

> **Run this only after the project owner has explicitly authorised one paid request of about USD 0.005, with a cap of USD 0.01.**

```powershell
& $py scripts\live_provider_check.py generate --confirm-paid --max-usd 0.01
```

The script does the following:
1. Creates one project in the throwaway storage, with a budget of USD 0.01.
2. Checks the documented estimate against the cap.
3. Sends **exactly one** `POST /v1/images/generations` with `{"model":"gpt-image-2","prompt":"Abstract illustration of a calm harbour at dawn…","n":1,"size":"1536x1024","quality":"low","output_format":"png"}`.
4. Validates the result (Steps 6–7).

**Note the printed `run_id`; you need it for cleanup.**

If the run reports `GENERATION FAILED`, **do not re-run it automatically**. Note the `error_code` and `provider_request_id`, check the OpenAI usage dashboard, and decide before trying again:
- `PROVIDER_TIMEOUT`: the request may still have been billed.
- `PROVIDER_CONTENT_REJECTED`, `PROVIDER_QUOTA` or `PROVIDER_AUTH_FAILED`: these are not billed.

## Step 6 — Validate the returned image (free; automatic in Step 5)

The `generate` output lists `checks`. Every check must be `"pass": true`:
- **Exactly one generation POST sent.** The guard counted 1, with 0 refused.
- **Format.** The decoded format matches `mime_type` (PNG / `image/png`).
- **Dimensions.**
  - The decoded size equals the recorded `width` and `height`.
  - It also equals the requested preset, 1536×1024. A different size is reported, never hidden.
- **Metadata.**
  - `provider` is `openai-images`, and `model` is set.
  - `provider_request_id` comes from the `x-request-id` header.
  - `provider_created` comes from the response's `created`.
  - The raw `usage` block and `c2pa_manifest_present` are recorded. C2PA is detection only.
- **Cost status.**
  - `KNOWN` with an amount means the documented token rates were multiplied by the returned `usage`.
  - Otherwise it must be `UNKNOWN` with a `null` amount.
  - A KNOWN amount must be no more than `--max-usd`.
  - The cost report has `ai_image_count = 1`.

Compare the KNOWN amount with the OpenAI usage dashboard a few minutes later. That comparison is the real check of the pricing table.

## Step 7 — Confirm storage and references (free; automatic in Step 5)

The script checks these, and you can confirm them in the printed `asset` block:
- **Asset record:**
  - `generation_kind: "AI_GENERATED"`, `provenance_class: "AI_GENERATED"`, `asset_type: "AI_IMAGE"`, `source_type: "AI_GENERATED"`
  - `is_real_ai: true`, `is_placeholder: false`, `is_fallback: false`, `evidence_eligible: false`
- **File on disk:** `local_path` is under `%TEMP%\vvm_live_check\<run-id>\storage\generated\<project>\` and exists.
- **Checksum:** SHA-256 of the file on disk = `checksum_sha256` = SHA-256 of the bytes served by `GET /assets/{id}/file`.
- **Generation record:** `provider_state: "SUCCEEDED"` and `attempts: 1`.

The run manifest is at `%TEMP%\vvm_live_check\<run-id>\live_check_manifest.json`. It stores IDs and results, never the key.

## Step 8 — Provider state and error behaviour with an invalid key (free, not billed)

This step uses a built-in, deliberately invalid key. **Your real key is not sent.**

```powershell
& $py scripts\live_provider_check.py invalid-key --models-only   # GET /models only: expect PASS, FAILED (HTTP 401)
& $py scripts\live_provider_check.py invalid-key                 # also one generation request with the invalid key
```

Expected results:
- probe state `FAILED`
- generation job `FAILED`, with `error_code: "PROVIDER_AUTH_FAILED"`
- a generation record with `provider_state: "FAILED"`
- `assets_created: 0`, so nothing was turned into a placeholder

OpenAI rejects the request at authentication, so it is not billed.

**Audit evidence.** `invalid-key --models-only` was run during the independent audit on 2026-10-12. It returned HTTP 401 with an `x-request-id`, mapped to `FAILED`, and sent 0 generation POSTs.

## Step 9 — Clean up (free)

```powershell
& $py scripts\live_provider_check.py cleanup --run-id <run-id-from-step-5>
& $py scripts\live_provider_check.py cleanup --run-id <run-id-from-step-8>   # if a run dir was created
Remove-Item Env:\OPENAI_API_KEY, Env:\VVM_ENABLE_PAID_PROVIDER_TESTS -ErrorAction SilentlyContinue
# Option B only: remove the key line from .env (keeps every other line)
if (Test-Path .env) { (Get-Content .env) | Where-Object { $_ -notmatch '^\s*(VIDEO_MODULE_)?OPENAI_API_KEY\s*=' } | Set-Content .env -Encoding ascii }
"key still set: $([bool]$env:OPENAI_API_KEY)"
```

`cleanup` behaves as follows:
- It deletes only `%TEMP%\vvm_live_check\<run-id>`, and only after checking that the path is inside that folder and that the manifest's `run_id` matches.
- It never touches the module's `storage\` or any user project.
- The module has no delete endpoint, so isolating the run storage is what makes this cleanup safe.

## Record the outcome

Add these to `docs\PHASE_2_INDEPENDENT_AUDIT.md` under "Live test":
- the date
- the `run_id` and `provider_request_id`
- the decoded size and format
- `cost_status` and the amount
- the dashboard-reported cost
- any failed check

Until that is done, the real generation path remains **unverified**.
