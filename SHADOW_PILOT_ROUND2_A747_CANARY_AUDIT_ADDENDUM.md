# A.7.4.7.1 — Canary Audit Addendum (append-only erratum)

This file **appends** to `SHADOW_PILOT_ROUND2_A747_CANARY_RESULT.md`. It does **not** modify,
delete, or rewrite the original report, and it does **not** change the scientific or safety
conclusions of A.7.4.7. It fills the role-level token/cost detail that the original report
summarized only at the aggregate level, and records two clarifications.

Source: the A.7.4.7 **append-only** budget ledger and desensitized manifest, read **read-only**
(zero paid calls, no new run). Raw ledger / events / manifest remain local and git-ignored;
only the desensitized figures below are committed.

## 1. Role-level token & cost detail (from the reconciled ledger)

| role | model | input_tokens | output_tokens | cache-token breakdown | calls | reconciled_cost (USD) |
|---|---|---:|---:|---|---:|---:|
| synthesizer | claude-opus-4-8 | 566 | 599 | `cache_creation_input_tokens`, `cache_read_input_tokens` = **unknown** | 1 | 0.020635 |
| verifier | claude-opus-4-8 | 384 | 167 | `cache_creation_input_tokens`, `cache_read_input_tokens` = **unknown** | 1 | 0.008015 |
| claim_extractor | deepseek-v4-flash | 457 (prompt_tokens) | 111 (completion_tokens) | `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens` = **unknown** | 1 | 0.000095 |

- **Total tokens:** input 1407, output 877.
- **Reconciled cost sum by role:** 0.020635 + 0.008015 + 0.000095 = **$0.028745**.
- **Reconciliation vs sealed total ($0.028745): exact match, difference = 0.0** (bit-for-bit; no
  rounding gap). The DeepSeek row uses the provider's `prompt_tokens`/`completion_tokens` convention.

### Why the cache-token fields are `unknown`
The append-only ledger's `reconciled` events recorded only `input_tokens` / `output_tokens` and the
final `actual_usd`; they did **not** persist the per-tier cache-token breakdown
(`cache_creation_input_tokens` / `cache_read_input_tokens` for Anthropic;
`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` for DeepSeek). Those fields are therefore
reported as **unknown**. They are **not** inferred or back-derived from the total cost.

## 2. Reservation / reconciliation / guards (audited)
- reserved = 3, reconciled = 3, **open = 0**.
- usage_unknown = 0, provider_may_have_billed = 0, retries = 0, rejected_before_invoke = 0, cancelled = 0.
- `cache_breakdown_available = false` (ledger records input/output only).

## 3. Clarifications (do not change any A.7.4.7 conclusion)
- **The original A.7.4.7 UI screenshot is a completed-run, read-only replay** of the frozen event
  stream — it is **not** a live, real-time view of the paid execution as it happened. The paid run
  itself completed once, headless, in ~19.4 s; the UI later replayed its stored events. A.7.4.7.1
  adds an automated test that the live SSE architecture delivers events incrementally (with a fake,
  zero-paid provider), but that only demonstrates the architecture — it does **not** claim the
  historical paid run was watched live.
- **Encoding erratum:** when the desensitized manifest note (which contains an em-dash `—`) was
  echoed through a Windows PowerShell console during A.7.4.7, the console's default code page
  rendered it as mojibake (e.g. `â`). This was a **console display artifact only**; the committed
  Markdown reports are written as UTF-8 and are not corrupted. A.7.4.7.1 fixes future
  report/doc writing to always use explicit UTF-8 (never PowerShell's default ANSI code page).
- The scientific verdict (**causal_strength = insufficient**, verifier `insufficient_for_causal`,
  no `verifier_fact_conflict`, claim graph all `insufficient_evidence`, shadow created no new cards)
  and the safety conclusions (3 real calls, $0.028745, retries=0, open reservations=0, sensitive
  scan clean) are **unchanged**.

## 4. Integrity — original artifacts preserved, hashed
- The original report `SHADOW_PILOT_ROUND2_A747_CANARY_RESULT.md` is **unchanged** (not edited,
  not deleted). Its SHA-256 at the time of this addendum:
  `0aaa86ce883f372da1a7c98910b290b4dab5f48b715b11b21e2204606d0894d9`
- The A.7.4.7 budget ledger is unchanged (read-only audit). SHA-256:
  `63a812c2e0bc1a54ff1909b3727231d32bf70a7207219de911b0d200cdfe8647` (1756 bytes).
- The SHA-256 of **this** addendum file (as committed) is reported separately in the A.7.4.7.1
  acceptance report (a file cannot contain its own hash).

## 5. Scope
This addendum is audit-only. A.7.4.7 was **not** re-run; no new real model was called; no re-network
retrieval; Planner/Executor not opened; Clarification/Approval/Pause/Resume not implemented;
Commit B not started; final adjudication authority unchanged.
