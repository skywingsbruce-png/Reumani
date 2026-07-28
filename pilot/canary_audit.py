"""A.7.4.7.1 —— 已完成金丝雀运行的**只读**审计（零付费）。

只读取 A.7.4.7 现有的 append-only 预算账本 + 脱敏 Manifest，聚合出**角色级** token 与费用。
绝不修改/删除/重写原始账本、事件、Manifest、科学结论、EvidenceCard、Claim 或费用记录。
不猜测缺失字段：账本未记录的（如缓存分档 token）一律标记为 "unknown"，不得由总费用反推。
输出脱敏：不含完整账本正文、Prompt、模型正文、API key、认证头或绝对路径。
"""

from __future__ import annotations

import hashlib
import json
import os

UNKNOWN = "unknown (not recorded in append-only ledger)"
# 账本未登记的缓存分档 token 字段（供审计如实标记 unknown，不反推）
_ANTHROPIC_CACHE = ("cache_creation_input_tokens", "cache_read_input_tokens")
_DEEPSEEK_CACHE = ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens")


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _read_ledger(path: str) -> list:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def audit_ledger(ledger_path: str) -> dict:
    """从只读账本聚合角色级 token / 费用。返回脱敏审计字典。"""
    events = _read_ledger(ledger_path)
    reserved, reconciled = {}, {}
    usage_unknown = failed_maybe_billed = rejected = cancelled = retries = 0
    for e in events:
        ev = e.get("event")
        if ev == "reserved":
            reserved[e["call_uid"]] = e
            if e.get("is_retry"):
                retries += 1
        elif ev == "reconciled":
            reconciled[e["call_uid"]] = e
        elif ev == "usage_unknown":
            usage_unknown += 1
        elif ev == "failed_maybe_billed":
            failed_maybe_billed += 1
        elif ev == "rejected_before_invoke":
            rejected += 1
        elif ev == "cancelled":
            cancelled += 1

    # open reservation = reserved 但无对应结算/释放
    done = set(reconciled) | {e["call_uid"] for e in events
                              if e.get("event") in ("released", "usage_unknown", "failed_maybe_billed")}
    open_uids = [u for u in reserved if u not in done]

    roles: dict[str, dict] = {}
    total_cost = 0.0
    total_in = total_out = 0
    for uid, r in reserved.items():
        role, model = r.get("role"), r.get("model")
        rec = reconciled.get(uid)
        row = roles.setdefault(role, {"model": model, "calls": 0, "input_tokens": 0,
                                      "output_tokens": 0, "reconciled_cost": 0.0})
        row["calls"] += 1
        if rec is None:
            row["input_tokens"] = UNKNOWN
            row["output_tokens"] = UNKNOWN
            row["reconciled_cost"] = UNKNOWN
            continue
        it, ot, cost = rec.get("input_tokens"), rec.get("output_tokens"), rec.get("actual_usd")
        if isinstance(row["input_tokens"], int):
            row["input_tokens"] += int(it or 0)
            row["output_tokens"] += int(ot or 0)
            row["reconciled_cost"] = round(float(row["reconciled_cost"]) + float(cost or 0), 9)
            total_in += int(it or 0)
            total_out += int(ot or 0)
            total_cost += float(cost or 0)
        # 缓存分档 token：账本未记录 → unknown（不反推）
        cache_fields = _DEEPSEEK_CACHE if model and model.startswith("deepseek") else _ANTHROPIC_CACHE
        row["cache_token_fields"] = {k: UNKNOWN for k in cache_fields}
        # DeepSeek 角色的 token 字段官方口径名（prompt/completion）
        row["provider_usage_convention"] = ("prompt_tokens/completion_tokens"
                                            if model and model.startswith("deepseek")
                                            else "input_tokens/output_tokens")

    return {
        "roles": roles,
        "totals": {"input_tokens": total_in, "output_tokens": total_out,
                   "reconciled_cost": round(total_cost, 9),
                   "role_cost_sum": round(sum(v["reconciled_cost"] for v in roles.values()
                                              if isinstance(v["reconciled_cost"], float)), 9)},
        "reservations": {"reserved": len(reserved), "reconciled": len(reconciled),
                         "open": len(open_uids)},
        "usage_unknown": usage_unknown, "provider_may_have_billed": failed_maybe_billed,
        "retries": retries, "rejected_before_invoke": rejected, "cancelled": cancelled,
        "cache_breakdown_available": False,     # 账本只记 input/output，无缓存分档
    }


def reconcile_against_sealed(audit: dict, sealed_total_usd: float) -> dict:
    """证明角色费用之和 == 原封存总费用；如有四舍五入差异，报告原始精度与差值。"""
    role_sum = audit["totals"]["role_cost_sum"]
    diff = round(role_sum - float(sealed_total_usd), 12)
    return {"sealed_total_usd": sealed_total_usd, "role_cost_sum": role_sum,
            "exact_match": diff == 0.0, "difference": diff,
            "note": "角色费用之和与封存总费用逐位一致" if diff == 0.0
                    else "存在四舍五入差异，见 difference（原始精度）"}


def audit_report(ledger_path: str, sealed_total_usd: float) -> dict:
    a = audit_ledger(ledger_path)
    a["reconciliation_vs_sealed"] = reconcile_against_sealed(a, sealed_total_usd)
    a["ledger_sha256"] = sha256_file(ledger_path)
    a["ledger_size_bytes"] = os.path.getsize(ledger_path)
    return a


__all__ = ["audit_ledger", "audit_report", "reconcile_against_sealed", "sha256_file", "UNKNOWN"]
