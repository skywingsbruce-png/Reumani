# A.7.4.7 — Real-Model Canary Result (desensitized)

**One** strictly-bounded real-model canary through the Reumani Runtime + UI.
Single run, no rerun. This is an **observation/run** result — **not** a claim that B1 passed.
No full prompts, API keys, auth headers, cookies, model bodies, or local absolute paths are
included (raw ledger/events/manifest stay local and git-ignored).

## Run identity
- `run_id`: `canary-real-01`  · stage `A747_canary` · price table `2026-07-20.1`
- Question (fixed): 现有证据是否支持 IL-6 升高会导致系统性硬化症纤维化？（区分相关性 / 机制支持 / 因果证据 + 证据缺口）
- Literature input: **frozen, hash-verified** real Europe PMC record from A.7.4.6 — **no new network retrieval**.
- Wall-clock: ~19.4 s.

## Real vs deterministic roles
- **Deterministic (0 paid calls):** Planner, Step Controller, structured `search_literature`
  fixture replay, Exact-ID Resolver, EvidenceAccumulator, EvidenceCard builder, Claim Graph,
  Shadow orchestration, lifecycle / loop / budget / provenance / fail-closed guards.
- **Real gated model (≤1 each, independent GatedModel identity + independent role quota):**
  Synthesizer (`claude-opus-4-8`), Verifier (`claude-opus-4-8`), Claim extractor (`deepseek-v4-flash`).
- **Planner and Executor paid calls: 0** (unused paid clients neutralized before the run).

## Calls / tokens / cost (per role) — hard limits honored
| role | model | calls | quota |
|---|---|---|---|
| synthesizer | claude-opus-4-8 | **1** | 1 |
| verifier | claude-opus-4-8 | **1** | 1 |
| claim_extractor | deepseek-v4-flash | **1** | 1 |
| **total** | — | **3** | ≤3 |

- **Total real model cost: $0.028745** (single-task cap **$1.50**).
- calls_by_model: `claude-opus-4-8` = 2, `deepseek-v4-flash` = 1.
- SDK retries = **0**; fallbacks = none; auto-reruns = none.
- **rejected_before_invoke = 0**, **retries = 0**.

## Reservation / reconciliation
- Every call: budget reserved **before** the network request, reconciled with real usage after.
- **open reservations at end = 0** (reserved → reconciled for all 3 calls).

## Evidence (frozen, hashed)
- evidence_ids: `33414495::4b156fdf4a3d` (1 EvidenceCard)
- content hash (sha256): `4b156fdf…89585ef4`
- evidence axes established: **none** (single abstract-level record; study design not machine-reported)
- Exact-ID Resolver resolution: **verified** (not overridden downstream)

## Scientific support verdict (real synthesizer, verified)
- **causal_strength: `insufficient`** — the real model did **not** claim causation.
  (Correct per §5: cross-sectional / single-biomarker / no temporal / no intervention evidence
  must not be read as causal; the harness also caps causal tier by confirmed axes.)
- association / mechanistic / temporal / intervention evidence: **none established** from the frozen record.
- unsupported claims (flagged): IL-6→fibrosis causation, dose-response, mechanistic sufficiency — all unsupported.
- missing evidence: longitudinal/temporal, interventional (e.g. IL-6-pathway RCTs), dose-response,
  genetic/instrumental (MR), in-vivo mechanism linked to outcomes, confounding control, reverse-causation check.

## Verifier
- verifier_status: **`insufficient_for_causal`**
- **verifier_fact_conflict: false** — Verifier did **not** override the Exact-ID Resolver's `verified` terminal state.

## Claim ↔ Evidence
- Claim Graph verdicts: `insufficient_evidence` × 3 (no over-attribution to the single record).
- Claim extractor: references only existing evidence_ids (fabricated ids dropped).

## Shadow / old comparison
- Shadow structured entry created **no new EvidenceCard** (`shadow_created_new_cards = false`);
  it recorded the existing real cards only.

## Lifecycle (five-stage)
- requested = executed = tool_returned = observed = **1** (consistent; observed only from a real ToolMessage).

## UI acceptance (Reumani Lab API mode, read-only served real run)
- Banner clearly marks **“Real model canary · Frozen real literature evidence”** with real call
  count (3), cost ($0.0287) and causal tier (insufficient); **not** shown as a normal DEMO.
- Real run status, plan steps (real search_literature / real resolver, both Satisfied), EvidenceCard
  count + source, synthesis/verification/claim/shadow stage events, causal tier + evidence gaps,
  four result artifacts, expandable trace, and a Stop control all render. Mock download buttons are
  labeled “下载 (mock)” (not described as real product downloads).
- UI event count: 27. Refresh does not duplicate events.
- Screenshot: `reumani_lab_ui/docs/screenshots/canary-real-1920x1080.png`.

## Guards triggered
- None fired unexpectedly. Switches were OFF by default (any accidental call would be rejected);
  they were set ON only for this single process.

## Sensitive-info scan
- **clean** — no `F:\…` absolute path, `sk-…`, key names, `Authorization`, `Cookie`, or `Bearer`
  in the desensitized manifest or the event stream (events use a safe_payload whitelist).

## Boundaries honored
Second run NOT performed; A1/B1 NOT run; real Planner NOT opened; ReAct Executor NOT entered;
no new network retrieval; protocol / question / scoring / budget unchanged;
Clarification/Approval/Pause/Resume NOT implemented; Commit B NOT started; code NOT modified to
re-run on result; final adjudication authority unchanged.
