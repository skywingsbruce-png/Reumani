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

from pilot.budget_gate import BudgetExceeded, BudgetGate      # noqa: E402
from pilot.round2_tasks import TASKS                          # noqa: E402

OUT = BASE / "pilot" / "round2_results"

# §3.2 冻结硬上限
LIMITS = dict(max_usd=25.00, max_calls_global=200, max_calls_per_task=12, task_timeout_s=600)


def _attach(gate):
    """运行时把闸门挂到已有 LLM 对象上——不修改 ssc_pi_agent 源码。"""
    import ssc_pi_agent as P
    for name in ("judge_llm", "deepseek_llm_pro", "deepseek_llm_con"):
        llm = getattr(P, name, None)
        if llm is not None:
            try:
                llm.callbacks = [gate]
            except Exception as e:
                print(f"  [warn] 无法给 {name} 挂 callback: {e}")
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
    gate = BudgetGate(**LIMITS)
    _attach(gate)
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
            gate.check_timeout()
        except BudgetExceeded as e:
            halted = f"{tid}: BudgetExceeded: {e}"
            print(f"  !! 硬上限触发，立即停止：{e}")
            break
        except Exception as e:
            m = {"task_id": tid, "error": f"{type(e).__name__}: {e}",
                 "traceback": traceback.format_exc()[-1500:],
                 "seconds": round(time.monotonic() - t0, 2)}
            print(f"  !! 运行异常（记录不修代码）：{type(e).__name__}: {e}")
        finally:
            gate.end_task()
        m["cost"] = gate.per_task.get(tid)
        results.append(m)
        print(f"  ✓ 累计 ${gate.usd:.4f} / {gate.calls_global} calls")

    out = {"stage": stage, "limits": LIMITS, "budget": gate.summary(),
           "halted": halted, "results": results}
    p = OUT / f"{stage}_metrics.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n写入 {p}\n预算：${gate.usd:.4f} / ${LIMITS['max_usd']}  调用：{gate.calls_global}")
    return out


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "stage1"
    ids = {"stage1": ["A1", "B1"], "stage2": list(TASKS), "stage3": list(TASKS)}[stage]
    run_stage(stage, ids)
