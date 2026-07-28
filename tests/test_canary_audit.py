"""A.7.4.7.1 —— 金丝雀账本**只读**审计逻辑测试（合成账本；不依赖 git-ignore 的真实运行目录）。

真实 A.7.4.7 账本不入库；这里用与其同结构的合成账本验证聚合/对账/只读性。
真实付费调用 0；不修改任何历史运行数据。
"""
import json

import pytest

from pilot.canary_audit import audit_report, audit_ledger, reconcile_against_sealed, sha256_file, UNKNOWN

pytestmark = pytest.mark.unit


def _synthetic_ledger(path):
    # 与真实 A.7.4.7 账本同结构（reserved 带 role，reconciled 带真实 usage/cost）
    rows = [
        {"event": "reserved", "call_uid": "s:1", "role": "synthesizer", "model": "claude-opus-4-8",
         "worst_case_usd": 0.04143, "is_retry": False},
        {"event": "reconciled", "call_uid": "s:1", "model": "claude-opus-4-8",
         "input_tokens": 566, "output_tokens": 599, "actual_usd": 0.020635, "released_usd": 0.020795},
        {"event": "reserved", "call_uid": "s:2", "role": "verifier", "model": "claude-opus-4-8",
         "worst_case_usd": 0.03222, "is_retry": False},
        {"event": "reconciled", "call_uid": "s:2", "model": "claude-opus-4-8",
         "input_tokens": 384, "output_tokens": 167, "actual_usd": 0.008015, "released_usd": 0.024205},
        {"event": "reserved", "call_uid": "s:3", "role": "claim_extractor", "model": "deepseek-v4-flash",
         "worst_case_usd": 0.000389, "is_retry": False},
        {"event": "reconciled", "call_uid": "s:3", "model": "deepseek-v4-flash",
         "input_tokens": 457, "output_tokens": 111, "actual_usd": 9.5e-05, "released_usd": 0.000294},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def test_role_level_tokens_and_cost(tmp_path):
    led = _synthetic_ledger(tmp_path / "l.jsonl")
    a = audit_ledger(led)
    assert a["roles"]["synthesizer"] == {"model": "claude-opus-4-8", "calls": 1, "input_tokens": 566,
        "output_tokens": 599, "reconciled_cost": 0.020635,
        "cache_token_fields": {"cache_creation_input_tokens": UNKNOWN, "cache_read_input_tokens": UNKNOWN},
        "provider_usage_convention": "input_tokens/output_tokens"}
    assert a["roles"]["claim_extractor"]["model"] == "deepseek-v4-flash"
    assert a["roles"]["claim_extractor"]["provider_usage_convention"] == "prompt_tokens/completion_tokens"
    # DeepSeek 缓存分档字段未记录 → unknown（不反推）
    assert a["roles"]["claim_extractor"]["cache_token_fields"] == {
        "prompt_cache_hit_tokens": UNKNOWN, "prompt_cache_miss_tokens": UNKNOWN}


def test_role_cost_sum_matches_sealed_exactly(tmp_path):
    led = _synthetic_ledger(tmp_path / "l.jsonl")
    a = audit_ledger(led)
    r = reconcile_against_sealed(a, 0.028745)
    assert r["exact_match"] is True and r["difference"] == 0.0
    assert a["totals"]["role_cost_sum"] == 0.028745
    assert a["totals"]["input_tokens"] == 1407 and a["totals"]["output_tokens"] == 877


def test_reservations_and_guards(tmp_path):
    led = _synthetic_ledger(tmp_path / "l.jsonl")
    a = audit_ledger(led)
    assert a["reservations"] == {"reserved": 3, "reconciled": 3, "open": 0}
    assert a["usage_unknown"] == 0 and a["provider_may_have_billed"] == 0
    assert a["retries"] == 0 and a["rejected_before_invoke"] == 0
    assert a["cache_breakdown_available"] is False


def test_missing_reconcile_marked_unknown_not_guessed(tmp_path):
    # 一个 reserved 无 reconciled → open + tokens/cost = unknown（不反推）
    (tmp_path / "l.jsonl").write_text(
        json.dumps({"event": "reserved", "call_uid": "x:1", "role": "synthesizer",
                    "model": "claude-opus-4-8", "worst_case_usd": 0.04}) + "\n", encoding="utf-8")
    a = audit_ledger(str(tmp_path / "l.jsonl"))
    assert a["reservations"]["open"] == 1
    assert a["roles"]["synthesizer"]["reconciled_cost"] == UNKNOWN


def test_audit_is_read_only(tmp_path):
    led = _synthetic_ledger(tmp_path / "l.jsonl")
    before_hash, before_size = sha256_file(led), (tmp_path / "l.jsonl").stat().st_size
    audit_report(led, 0.028745)
    audit_report(led, 0.028745)
    assert sha256_file(led) == before_hash                    # 账本字节/hash 前后不变
    assert (tmp_path / "l.jsonl").stat().st_size == before_size
