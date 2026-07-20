"""Round 2 Pilot runner（只运行冻结题目，不改系统模块、不改 Prompt、不改评分规则）。

用法：python pilot/round2_runner.py stage1     # 仅 A1 + B1 金丝雀
      python pilot/round2_runner.py stage2     # 12 题各 1 次
      python pilot/round2_runner.py stage3     # 稳定性复测

费用闸门在任何模型调用之前挂载；超限抛 BudgetExceeded 并立即停止，不重试、不提高预算。
"""

import json
import sys
import time
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from pilot.hard_gate import (BudgetExceeded, GateConfigError, HardBudgetGate,  # noqa: E402
                             assert_all_paid_entrypoints_wrapped, wrap_all)
from pilot.round2_tasks import TASKS                          # noqa: E402

OUT = BASE / "pilot" / "round2_results"

# §3.2 冻结硬上限（**本次 A.6.2 不修改上限**：每题仍为 12，改动须走协议 v2）
LIMITS = dict(max_usd_global=25.00, max_usd_stage=25.00, max_usd_task=5.00,
              max_calls_global=200, max_calls_task=12,
              max_calls_per_model={"claude-opus-4-8": 6, "deepseek-chat": 12},
              task_timeout_s=600, max_retries=2, default_max_tokens=4096)


def _wrap_paid_entrypoints(gate):
    """把所有付费入口就地换成 GatedModel；任何一个包不住 → 拒绝启动，不降级为软闸门。"""
    import ssc_pi_agent as P
    specs = [
        (P, "judge_llm", "planner_verifier", "claude-opus-4-8", 4096),
        (P, "deepseek_llm_pro", "executor_claim", "deepseek-chat", 4096),
    ]
    if getattr(P, "deepseek_llm_con", None) is not None:
        specs.append((P, "deepseek_llm_con", "executor_con", "deepseek-chat", 4096))
    wrapped = wrap_all(gate, specs)
    assert_all_paid_entrypoints_wrapped([(P, a) for _, a, _, _, _ in specs])
    print(f"已包装付费入口：{wrapped}")
    return P


def _metrics(state, task, seconds):
    """只读地从 state/manifest 抽取第四部分要求的指标。"""
    sh = state.shadow or {}
    ev = sh.get("tool_events") or []
    structured = [e for e in ev if e.get("structured")]
    legacy = [e for e in ev if not e.get("structured")]
    cards = sh.get("evidence_cards") or []
    claims = sh.get("claims") or []
    linked = [c for c in claims if (c.get("evidence_ids") or c.get("supporting_evidence"))]
    cmp_ = sh.get("comparison") or {}
    plan = state.research_plan
    steps = (plan or {}).get("steps") if isinstance(plan, dict) else getattr(plan, "steps", None)
    prov = [(e.get("artifact") or {}).get("provenance") or {} for e in structured]
    return {
        "task_id": task["task_id"],
        "execution": {
            "plan_structured": bool(plan),
            "plan_steps": len(steps or []),
            "tool_calls": len(ev),
            "selected_tools": sh.get("selected_tools"),
            "allowed_tools": sh.get("allowed_tools"),
            "unauthorized_tool_calls": len(sh.get("unauthorized_tool_calls") or []),
            "tool_failures": sum(1 for e in ev if e.get("ok") is False),
            "retries": state.retry_count,
            "seconds": round(seconds, 2),
        },
        "structuredness": {
            "structured_toolresults": len(structured),
            "legacy_unstructured": len(legacy),
            "structured_rate": round(len(structured) / len(ev), 3) if ev else None,
            "schema_incompatible": sum(
                1 for e in structured
                if (e.get("artifact") or {}).get("schema_version") != "toolresult-v1"),
            "evidence_cards": len(cards),
            "claims": len(claims),
            "claim_evidence_link_rate": round(len(linked) / len(claims), 3) if claims else None,
            "claims_without_evidence": len(claims) - len(linked),
        },
        "shadow": {
            "old_passed": cmp_.get("old_passed"),
            "shadow_passed": cmp_.get("shadow_passed"),
            "agree": cmp_.get("agree"),
            "divergence": cmp_.get("divergence"),
            "shadow_status": sh.get("shadow_status"),
            "shadow_error_type": sh.get("shadow_error_type"),
            "shadow_reject_reason": (sh.get("shadow_verifier_result") or {}).get("reason"),
            "old_pass_new_reject": bool(cmp_.get("old_passed") and not cmp_.get("shadow_passed")),
        },
        "audit": {
            "manifest_present": bool(sh),
            "manifest_schema_version": sh.get("manifest_schema_version"),
            "run_id": sh.get("run_id"),
            "hash_algorithms": sorted({p.get("hash_algorithm") for p in prov if p}),
            "content_hash_lens": sorted({len(p.get("content_hash") or "") for p in prov if p}),
            "retrieval_statuses": [
                ((e.get("artifact") or {}).get("data") or {}).get("retrieval_status")
                for e in structured
                if isinstance((e.get("artifact") or {}).get("data"), dict)],
            "multiplicity": [
                {k: ((e.get("artifact") or {}).get("data") or {}).get(k)
                 for k in ("multiplicity_status", "adjustment_method", "adjusted_q", "test_count")}
                for e in structured
                if isinstance((e.get("artifact") or {}).get("data"), dict)
                and ((e.get("artifact") or {}).get("data") or {}).get("multiplicity_status")],
        },
        "final_answer": state.final_answer,
        "expectations": {k: task[k] for k in
                         ("expected_retrieval_status", "expected_multiplicity",
                          "allow_causal", "allow_clinical_extrapolation", "forbidden")},
    }


def run_stage(stage, task_ids):
    OUT.mkdir(parents=True, exist_ok=True)
    gate = HardBudgetGate(stage=stage, ledger_path=OUT / f"{stage}_ledger.jsonl", **LIMITS)
    gate.check_switches()          # 两个显式开关缺一不可，且 CI 中一律拒绝
    _wrap_paid_entrypoints(gate)   # 包不住就抛 GateConfigError，Pilot 不启动
    from ssc_a1 import run_agent

    results, halted = [], None
    for tid in task_ids:
        task = TASKS[tid]
        print(f"\n=== [{stage}] {tid} ===\n{task['question'][:90]}…")
        gate.start_task(tid)
        t0 = time.monotonic()
        try:
            state = run_agent(task["question"], constraints=task.get("constraints", ""),
                              max_iterations=2, shadow=True)
            m = _metrics(state, task, time.monotonic() - t0)
            m["error"] = None
        except (BudgetExceeded, GateConfigError) as e:
            halted = f"{tid}: {type(e).__name__}: {e}"
            print(f"  !! 硬闸门触发（网络请求前拒绝），立即停止：{e}")
            break
        except Exception as e:
            m = {"task_id": tid, "error": f"{type(e).__name__}: {e}",
                 "traceback": traceback.format_exc()[-1500:],
                 "seconds": round(time.monotonic() - t0, 2)}
            print(f"  !! 运行异常（记录不修代码）：{type(e).__name__}: {e}")
        finally:
            gate.end_task()
        m["cost"] = gate.summary()
        results.append(m)
        print(f"  ✓ 已承诺 ${gate.committed_usd:.4f} / {gate.calls_global} calls")

    out = {"stage": stage, "limits": LIMITS, "budget": gate.summary(),
           "halted": halted, "results": results}
    p = OUT / f"{stage}_metrics.json"          # 注意：不覆盖 Stage 1 历史失败记录
    if p.exists():
        p = OUT / f"{stage}_metrics_{int(time.time())}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n写入 {p}\n已承诺：${gate.committed_usd:.4f} / "
          f"${LIMITS['max_usd_global']}  调用：{gate.calls_global}")
    return out


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "stage1"
    ids = {"stage1": ["A1", "B1"], "stage2": list(TASKS), "stage3": list(TASKS)}[stage]
    run_stage(stage, ids)
