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
from pilot import paid_transport as PT                        # noqa: E402
from pilot.round2_tasks import TASKS                          # noqa: E402

OUT = BASE / "pilot" / "round2_results"

# 协议 v2 冻结上限（分角色，不是单一总数；模型钉死到 flash）
ANTHROPIC_MODEL = "claude-opus-4-8"
DEEPSEEK_MODEL = PT.PINNED_DEEPSEEK          # deepseek-v4-flash，非思考模式
LIMITS = dict(max_usd_global=25.00, max_usd_stage=3.00, max_usd_task=1.50,
              max_calls_global=200, max_calls_task=21,
              max_calls_per_model={ANTHROPIC_MODEL: 4, DEEPSEEK_MODEL: 17},
              max_calls_per_role={"planner": 2, "verifier": 2,
                                  "claim_extractor": 1, "executor": 16},
              task_timeout_s=600, max_retries=0, default_max_tokens=2000)
STAGE1_TOTAL_TIMEOUT_S = 1500


def _wrap_paid_entrypoints(gate):
    """构造 Pilot 专用加固模型（钉死模型 + 非思考 + max_retries=0 + 有限 timeout），
    再就地替换生产模块的付费入口。任何一项不过 → 拒绝启动，不降级为软闸门。"""
    PT.assert_import_order_clean()            # 必须在包装前：ssc_a1/ssc_skill_agent 不得已导入
    roles, runconf = PT.build_pilot_roles(gate, anthropic_model=ANTHROPIC_MODEL,
                                          deepseek_model=DEEPSEEK_MODEL)
    import ssc_pi_agent as P
    P.judge_llm = roles["planner"]            # Planner 与 Verifier 共用 judge 入口
    P.deepseek_llm_pro = roles["executor"]
    runconf["neutralized"] = PT.neutralize_unused_paid_clients(gate)
    assert_all_paid_entrypoints_wrapped([(P, "judge_llm"), (P, "deepseek_llm_pro")])
    runconf["bindings_verified"] = PT.assert_bindings_after_import(roles, gate)
    print(f"运行配置：{runconf}")
    return P, runconf


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
    _, runconf = _wrap_paid_entrypoints(gate)   # 包不住就抛 GateConfigError，Pilot 不启动
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

    out = {"stage": stage, "limits": LIMITS, "run_config": runconf,
           "budget": gate.summary(), "halted": halted, "results": results}
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
