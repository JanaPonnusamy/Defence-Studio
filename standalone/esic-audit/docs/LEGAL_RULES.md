# Statutory rules used (rules version 2026.11.03-1, researched 2026-11-03)

Machine-readable source of truth: `app/rules/statutory_rules.json`. The engine picks a
rule by **effective date**: due-date rules by wage month, interest and damages rules by
each day of delay. A delay spanning two regimes is split into day segments.

| Rule | Provision | Applies | Method | Verification |
|---|---|---|---|---|
| REG31-21D | ESI (General) Regs 1950, Reg. 31 (pre-2017 text) | wage months ≤ 2017-05 | due = month end + 21 days | SECONDARY → UNVERIFIED |
| REG31-15D | Reg. 31 | wage months 2017-06 … 2025-10 | due = month end + 15 days | **OFFICIAL TEXT** (ESIC consolidated regulations as on 17.05.2023) |
| COSS-DUE-15D-ASSUMED | Code on Social Security, 2020 | wage months ≥ 2025-11 | 15 days assumed | UNKNOWN → UNVERIFIED |
| REG31A-15 | Reg. 31A (historical) | delay days 01.09.1994–30.09.2005 | 15% p.a. simple | SECONDARY (court judgment) |
| REG31A-12 | Reg. 31A | delay days 01.10.2005–20.11.2025 | 12% p.a. simple, per day | **OFFICIAL TEXT** (rate); start date from a court judgment |
| COSS-S127-12 | CoSS s.127, S.O. 2698(E) 29.05.2026 | delay days ≥ 21.11.2025 | 12% p.a. simple | SECONDARY → UNVERIFIED |
| REG31C-SLAB | Reg. 31C with ESI Act s.85B | delay days ≤ 20.11.2025 | **maximum** 5% (<2 m), 10% (2–<4 m), 15% (4–<6 m), 25% (≥6 m) p.a., capped at the arrears | **OFFICIAL TEXT** (slab); s.85B cap SECONDARY |
| COSS-S128-UNVERIFIED | CoSS s.128 | delay days ≥ 21.11.2025 | same slab assumed | UNKNOWN → UNVERIFIED |

## Sources

* ESIC, *ESI (General) Regulations, 1950*, consolidated as on 17.05.2023 (official PDF; Regs 31, 31A, 31B, 31C read verbatim): https://sronandnagri.esic.gov.in/attachments/publicationfile/b5492c0755e7b0f96197f413a363bc6d.pdf
* ESIC HQ circular N-11011/2/2025-BFT-II dt 28.11.2025: the Code on Social Security, 2020 is in force from 21.11.2025 (Gazette No. 5143), and the ESI Act 1948 is "since repealed": https://esic.gov.in/attachments/circularfile/Implementation_the_Code_on_Social_Security_2020_English_1764326385.pdf
* Reg. 31 amendment (21 → 15 days, from contributions for June 2017), notification N-12/13/1/2016-P&D, reproduced at: https://corporatelawreporter.com/esi-contribution-payment-last-date-changed-to-15th-of-every-month-w-e-f-1st-july-17/
* Interest history (15% to 30.09.2005, 12% from 01.10.2005), Bombay HC 2022: https://www.latestlaws.com/judgements/bombay-high-court/2022/february/2022-latest-caselaw-1297-bom
* CoSS s.127 interest notification S.O. 2698(E) (secondary summary; the Gazette PDF was not read): https://www.sgcms.com/regulatory-updates/?p=3380
* ESI Act 1948 (India Code): https://www.indiacode.nic.in/handle/123456789/1441

## How the rules are used

* **Due date and delay.** Delay days run from the day after the due date to the payment date, or to the audit date when unpaid. A challan with no confirmed payment is **not** treated as paid.
* **Interest.** Amount × rate × days / 365, simple, rounded half-up to the paisa. Shown as *indicative*. ESIC's own interest figure always prevails, and a difference of more than Rs 1 is listed as a discrepancy.
* **Damages.** These are **maximum** rates. ESIC levies damages only after assessment and a hearing, and may levy less or waive them. The slab is chosen from the total delay in completed months. Potential damages are computed only for late or unpaid periods with no damages record and no covering RC, and are shown as `ESTIMATED` upper bounds.
* Basis `VERIFIED` requires every rule touched to be OFFICIAL_TEXT. Anything else is labelled `UNVERIFIED`.

## Open legal points (left UNVERIFIED rather than guessed)

1. Whether Section 128 of the CoSS (damages) is in force for ESI dues, and at what rates. Secondary commentary says the Reg. 31C scale continues as a saved provision.
2. The due date under the CoSS framework.
3. The official text of S.O. 2698(E).
4. Month-specific due-date extensions granted by ESIC, which are not modelled.

Re-check these before relying on any figure for periods after 20.11.2025, then update the
JSON (new `rules_version`). Old audits keep the rules version they were calculated with.
