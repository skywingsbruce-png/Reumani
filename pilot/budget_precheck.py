"""A1 预算预检（dry-run，**不调用任何模型**）。

按 A.6.3.1 §5：用 A1 的**实际冻结问题 + 完整工具 schema + 实际系统提示**构建请求，
估算逐角色最坏费用。规则：
- 不能只用问题文本估算；必须含系统提示、工具定义、消息历史与工具结果上界；
- 未知长度**不能按 0**（用显式的保守上界，并在输出中标注）；
- 单题最坏费用超过冻结上限 → 停止并报告；
- **不得自行提高预算，也不得缩短真实输入来"通过"预检**。
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from pilot import prices as PR                                    # noqa: E402
from pilot import paid_transport as PT                            # noqa: E402
from pilot.hard_gate import estimate_input_tokens                 # noqa: E402
from pilot.round2_tasks import TASKS                              # noqa: E402

# v2 冻结值（不修改）
CAPS = {"planner": 2, "verifier": 2, "claim_extractor": 1, "executor": 16}
MAX_USD_TASK = 1.50
MAX_USD_STAGE1 = 3.00
ANTHROPIC_MODEL = "claude-opus-4-8"
DEEPSEEK_MODEL = PT.PINNED_DEEPSEEK

# 未知长度的**显式保守上界**（绝不按 0）
BOUNDS = {
    "tool_result_tokens_per_call": 4000,     # 单次工具结果上界（工具输出被截到 4000 字符）
    "history_growth_per_step": 1200,         # ReAct 每轮追加的历史 token
    "resource_bundle_tokens": 3000,          # 阶段0 资源检索注入的文本上界
}


def _tool_schema_text(allowed=None):
    """真实工具 schema（名称 + 描述 + 参数），不是占位符。"""
    import ssc_skill_agent as SK
    tools = SK.SKILL_AGENT_TOOLS
    if allowed:
        tools = [t for t in tools if t.name in allowed]
    out = []
    for t in tools:
        try:
            schema = t.args_schema.model_json_schema() if t.args_schema else {}
        except Exception:
            schema = {}
        out.append(json.dumps({"name": t.name, "description": t.description or "",
                               "input_schema": schema}, ensure_ascii=False))
    return "\n".join(out), len(tools)


def _planner_prompt(task):
    """真实 Planner 系统提示（从 planner 模块取，取不到则用显式保守上界，绝不按 0）。"""
    try:
        import planner
        for attr in ("PLAN_SYSTEM_PROMPT", "SYSTEM_PROMPT", "PLANNER_PROMPT", "PROMPT"):
            v = getattr(planner, attr, None)
            if isinstance(v, str) and v.strip():
                return v
        src = Path(planner.__file__).read_text(encoding="utf-8")
        lits = [m for m in src.split('"""') if len(m) > 200]      # 取最长的提示模板
        if lits:
            return max(lits, key=len)
    except Exception:
        pass
    return "x" * 6000          # 显式保守上界（~2000 token），不是 0


def precheck(task_id="A1"):
    task = TASKS[task_id]
    tool_text, n_tools = _tool_schema_text()
    sys_planner = _planner_prompt(task)

    rows = []

    def add(role, model, label, est_in, max_out, n_calls):
        worst = PR.worst_case_usd(model, est_in, max_out)
        rows.append({"role": role, "model": model, "call": label,
                     "est_input_tokens": est_in, "max_tokens": max_out,
                     "worst_usd_per_call": round(worst, 6), "n_calls": n_calls,
                     "worst_usd_total": round(worst * n_calls, 6)})
        return worst * n_calls

    q = task["question"]
    base_ctx = estimate_input_tokens(q + sys_planner) + BOUNDS["resource_bundle_tokens"]

    total = 0.0
    # Planner：系统提示 + 问题 + 允许工具清单 + 资源包
    total += add("planner", ANTHROPIC_MODEL, "each",
                 base_ctx + estimate_input_tokens(tool_text) // 4,
                 PT.MAX_TOKENS["planner"], CAPS["planner"])

    # Executor（ReAct）：消息随工具循环增长 → 分首次 / 典型中间 / 最后一次报告
    exec_first = base_ctx + estimate_input_tokens(tool_text)
    exec_mid = exec_first + 7 * (BOUNDS["history_growth_per_step"]
                                 + BOUNDS["tool_result_tokens_per_call"])
    exec_last = exec_first + (CAPS["executor"] - 1) * (BOUNDS["history_growth_per_step"]
                                                       + BOUNDS["tool_result_tokens_per_call"])
    total += add("executor", DEEPSEEK_MODEL, "first", exec_first,
                 PT.MAX_TOKENS["executor"], 1)
    total += add("executor", DEEPSEEK_MODEL, "typical_middle", exec_mid,
                 PT.MAX_TOKENS["executor"], CAPS["executor"] - 2)
    total += add("executor", DEEPSEEK_MODEL, "last_allowed", exec_last,
                 PT.MAX_TOKENS["executor"], 1)

    # Verifier：按 ssc_a1.verify() 的**真实 prompt 构成**建模 ——
    # 固定系统提示 + 研究问题 + 计划 + executor 最终输出（**不含** ReAct 历史与工具结果）。
    # 计划与 executor 输出分别受各自 max_tokens 约束，故上界是确定的。
    verifier_in = (estimate_input_tokens(q)
                   + 400                                  # verify() 里的固定提示文本
                   + PT.MAX_TOKENS["planner"]             # 计划上界
                   + PT.MAX_TOKENS["executor"])           # executor 最终输出上界
    total += add("verifier", ANTHROPIC_MODEL, "each", verifier_in,
                 PT.MAX_TOKENS["verifier"], CAPS["verifier"])

    # Claim extractor：最终答案 + 证据卡
    total += add("claim_extractor", DEEPSEEK_MODEL, "each",
                 base_ctx + BOUNDS["tool_result_tokens_per_call"],
                 PT.MAX_TOKENS["claim_extractor"], CAPS["claim_extractor"])

    per_role = {}
    for r in rows:
        per_role[r["role"]] = round(per_role.get(r["role"], 0.0) + r["worst_usd_total"], 6)

    out = {
        "task_id": task_id,
        "dry_run": True, "real_api_calls": 0,
        "price_config_version": PR.PRICE_TABLE_VERSION,
        "models": {"anthropic": ANTHROPIC_MODEL, "deepseek": DEEPSEEK_MODEL},
        "n_tools_in_schema": n_tools,
        "explicit_bounds_for_unknown_lengths": BOUNDS,
        "rows": rows,
        "per_role_worst_usd": per_role,
        "task_worst_usd": round(total, 6),
        "task_cap_usd": MAX_USD_TASK,
        "headroom_vs_task_cap_usd": round(MAX_USD_TASK - total, 6),
        "stage1_worst_usd": round(total * 2, 6),
        "stage1_cap_usd": MAX_USD_STAGE1,
        "headroom_vs_stage1_cap_usd": round(MAX_USD_STAGE1 - total * 2, 6),
        "within_task_cap": total <= MAX_USD_TASK,
        "within_stage1_cap": total * 2 <= MAX_USD_STAGE1,
    }
    return out


if __name__ == "__main__":
    r = precheck(sys.argv[1] if len(sys.argv) > 1 else "A1")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if not (r["within_task_cap"] and r["within_stage1_cap"]):
        print("\n!! 预检未通过：最坏费用超过冻结上限。停止并报告，"
              "不得自行提高预算或缩短真实输入。")
        sys.exit(2)
