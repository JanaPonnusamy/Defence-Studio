# Security and read-only boundary

## Credentials

* The employer types username, password and CAPTCHA/OTP into the real ESIC page in a visible Chrome window. The module never fills, reads or submits that form. A test (`test_module_never_types_into_or_submits_forms`) checks that the module source contains no `fill`, `type`, `press`, `select_option` or `set_input_files` calls.
* No API endpoint accepts a password, OTP, CAPTCHA, payment or challan instruction. This is tested.
* The browser context is new and in-memory for every audit: no `storage_state` and no persistent profile. It is closed, and the authenticated session destroyed, when the inspection finishes, fails or is cancelled. The stage `BROWSER_SESSION CLOSED` is recorded.
* No screenshots are taken before login is confirmed. HTML snapshots have password-input values and `__VIEWSTATE` stripped.
* All events, stage details and errors pass through `masking.redact`. It masks 17-digit employer codes and any `password|otp|captcha=…` value. Only the masked employer code is stored on the audit run.
* The end-to-end test types a fake password and then checks that it appears in no file under `storage/`.

## Read-only guards (active from the moment login is confirmed)

| Layer | What it blocks |
|---|---|
| Inspector allow-list | The module only follows menu labels listed in `portal_map.json`, minus `never_open` (File Monthly Contributions, Update Employer Details, Generate Challan, List of Employees), plus pager links. Every label is checked against the deny vocabulary first. |
| In-page click guard (every frame) | Cancels any click or submit, including the employer's own, on a control whose text, value, id, name, title, href or onclick matches: generate, submit, pay / pay now / make payment / proceed to pay, confirm, modify, update, delete, remove, save, edit, add, insert, declare, file, apply, register, approve, upload, reset, change password, cancel challan, initiate, proceed, e-sign, OTP. `window.confirm` always returns false. |
| Network guard | Aborts any POST whose ASP.NET button field or `__EVENTTARGET` names such a control, any non-GET to a host other than esic.gov.in/esic.in, any document navigation off the portal, and any request to bank or payment-gateway hosts (SBI, BillDesk, Razorpay, PayU, NPCI, …), including during login. |
| Dialogs | Every portal dialog is dismissed and never accepted. |

Each blocked attempt is written to the audit log (`View logs`). The synthetic-portal test
clicks Generate Challan, fires a raw `btnPayNow` POST, clicks a "Pay online" link and
navigates to an SBI gateway. All four are blocked, and the fixture server records zero
state-changing requests.

## Data at rest and access

* Local API only (`127.0.0.1`). Every `/api` route requires the access token (constant-time compare). Report and evidence downloads go through the same check.
* Report and evidence paths are resolved and must stay inside `storage/reports` or `storage/evidence`. HTML evidence is served as `text/plain`, so it is never rendered.
* Audit snapshots are immutable at the database level (triggers). History cannot be deleted through the module.
* `storage/` is git-ignored. It holds employer data, so treat it as confidential.

## Not done (by design)

* No recurring or scheduled monitoring.
* No CAPTCHA/OTP automation, and no use of undocumented portal endpoints.
* No LLM in any calculation. All arithmetic is Decimal, deterministic and unit-tested.
