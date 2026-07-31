# A.7.5.2 — Real-model HITL Canary: PREFLIGHT FAILED (0 paid calls)

**Result: canary not started. Real paid model calls = 0. No production code modified. `human_review = true`.**

Per A.7.5.2 §7 ("任一项失败：立即停止；不修改代码；不发起付费调用；保存脱敏 preflight 报告；等待人工决定")
and §12, this phase stopped at preflight. Machine-readable detail:
`pilot/round2_results/A752_canary_preflight/preflight.manifest.json`.

---

## 1. Baseline (§3) — PASS

| Check | Expected | Actual | Verdict |
|---|---|---|---|
| dev HEAD | A.7.5.1.1 final | `e54efb3` | ✅ |
| public HEAD | A.7.5.1.1 final | `988b016` | ✅ |
| CI | 11/11 success | run **#72**, 11/11 success | ✅ |
| Worktree | clean | clean (only pre-existing untracked `round2_results/*_local.json`) | ✅ |
| Python suite | baseline | 936 pass / 0 fail / 0 error | ✅ |
| UI | baseline | 44 pass, typecheck+build OK, **0 lint errors** (7 pre-existing warnings) | ✅ |
| Frozen protocol + 3 appendices | unchanged | all SHA-256 match | ✅ |
| A.7.4.7 ledger / manifest / events | unchanged | all SHA-256 match | ✅ |
| A.7.5 + A.7.5.1 screenshots | unchanged | all SHA-256 match | ✅ |
| Historical ledger | open = 0 | reserved 3 / reconciled 3 / **open 0** | ✅ |

## 2. Budget preflight (§6) — PASS (not the blocker)

| Role | Model | max_tokens | Worst-case |
|---|---|---:|---:|
| Synthesizer | `claude-opus-4-8` | 1500 | $0.04350 |
| Verifier | `claude-opus-4-8` | 1200 | $0.03600 |
| Claim extractor | `deepseek-v4-flash` | 1200 | $0.00042 |
| **Total** | | | **$0.07992** ≤ **$0.15** ✅ |

Role quotas 1/1/1, total cap 3, retries 0. Recorded for completeness — cost was never the obstacle.

---

## 3. Blockers

### B1 (decisive) — No HITL → real-model execution path exists
*Refs: §8 (must run through the HITL state chain; must not bypass HITL), §14 (no production code changes this round).*

The HITL runtime and the paid-model stack are **completely disjoint subsystems**:

- `pilot/hitl.py` contains **zero** references to `canary` / `GatedModel` / `hard_gate` /
  `OpenTaskRuntime` / `real_runtime` / `synthesizer` / `claim_extract` / `paid` / `anthropic` / `deepseek`.
- `pilot/canary_a747.py`, `pilot/open_task_runtime.py`, `pilot/real_runtime.py` contain **zero**
  references to `hitl` / `clarification` / `approval`.
- `HitlRun(run_id, event_store, *, clock, exec_delay_ms, exec_gate)` — no injection point for the
  question, the clarification options, the approval-card content, or an executor.
- The HITL question is hardcoded (IL-6 wet-lab experiment design); the clarification is hardcoded
  (tissue source: skin/lung/both); the post-approval action is `_execute_fake_action`, a hardcoded
  fake `simulate_wetlab_package` tool that performs **0 model calls**.
- The only creation path is `runtime_api.start_hitl(exec_delay_ms)` — no `real`/model flag.

A.7.5.2 requires a *different* question, a *different* 4-option clarification, an approval card
showing evidence count/budget/limits, and post-approval execution of Synthesizer → Verifier →
Claim extractor. **None of that exists.** Building it is new production code, which §14 forbids;
and calling the three models directly is explicitly forbidden by §8. Both routes are closed.

### B2 (decisive) — No valid frozen cGAS–STING EvidenceCard input
*Refs: §4, §7.25, §7.26.*

- The frozen "real" fixture (`pilot/demo_real_fixture.json`, used by A.7.4.7) holds **one** article:
  *Plasma Hsp90 in systemic sclerosis*, PMID 33414495 (2021) — **no cGAS/STING/cGAMP content**.
- The only cGAS–STING record in the repo is a **synthetic unit-test row**
  (`tests/fixtures/mini_corpus.jsonl`) with `pmid = 1002` — not a real PMID (real ones are 7–8 digits).
  It carries only `abstract, journal, pmid, title, year`, and is missing **every** §4-required field:
  `evidence_id, doi, study_design, species, content_level, supporting_excerpt_hash,
  provenance_source, content_hash, hash_algorithm, schema_version`.

§4 forbids network supplementation and forbids the model inventing PMIDs/DOIs/years/designs.
Running a *causal-inference* question over a fabricated PMID would make any verdict scientifically
meaningless and would present a test fixture as real evidence. §4 therefore mandates: **preflight
fails and we stop.**

### B3 (operational) — No provider credentials in the environment
*Ref: §7.8.* `ANTHROPIC_API_KEY` and `DEEPSEEK_API_KEY` are **absent** from the environment.
A repo `.env` exists but was **not read** (existence check only; no key material was read, printed,
or logged). A previously reported leaked DeepSeek key rotation was not verified. Resolvable by
configuration — unlike B1/B2.

---

## 4. What was deliberately NOT done

- No provider/model call of any kind — **paid calls = 0**.
- No production code modified (no HITL↔model bridge written).
- No network retrieval of evidence.
- No use of the synthetic PMID as if it were real evidence.
- No HITL bypass by invoking the three models directly.
- No rerun / second canary; no A1/B1; no Planner/ReAct/Executor; no device; no Commit B; no Biomni migration.

## 5. What unblocking would require (report only — not done, needs approval)

1. **A HITL research-run type** (new production code): parameterized question, the four evidence-standard
   clarification options, an approval card binding evidence count + budget + limits + expected artifacts,
   and post-approval execution wired to the existing gated 3-role chain and the deterministic
   EvidenceAccumulator / Step Controller / Verifier / Claim graph / Shadow components.
2. **A lawful frozen cGAS–STING evidence set**: obtained through the normal (approved) retrieval +
   desensitization + structural-validation path in its own phase, hash-frozen before any canary — not
   assembled inside the canary, and not invented.
3. **Credentials configured** for the two providers.

Items 1 and 2 are each their own phase and require explicit approval. The canary should run only after
both exist and are frozen.
