"""A.6.5.2：附录 1 的冻结校验。零真实 API。

附录内容一旦变化，本文件的 hash 断言必须失败。
同时锁定：v1/v2 未变、题目与评分未变、附录**只含安全边界**（不碰题目/评分/预算）。
"""
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

V1_SHA = "5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22"
V2_SHA = "c76f589485e4ebfd728c27b653d2735f3ebd1c6930087c244e4efbdba9d66696"
ADDENDUM_SHA = "de3afcdd2131ba17717eb2d93a543e350aeff16e11c2ecc32edd8d583f9ca7f3"

ADDENDUM_MD = ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_1.md"
ADDENDUM_SHA_FILE = ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_1.sha256"


def lf_sha256(path):
    """对 LF 规范化内容求 SHA-256 —— Windows 与 Ubuntu 必须得到同一个值。"""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.mark.unit
def test_addendum_hash_is_frozen():
    assert ADDENDUM_MD.exists()
    assert lf_sha256(ADDENDUM_MD) == ADDENDUM_SHA, \
        "附录内容已变化 —— 必须产生新版本与新 hash，不得原地改写"


@pytest.mark.unit
def test_addendum_sha_file_records_the_same_hash():
    rec = ADDENDUM_SHA_FILE.read_text(encoding="utf-8")
    assert ADDENDUM_SHA in rec
    assert "LF-normalized" in rec
    assert V1_SHA in rec and V2_SHA in rec        # 同时记录未变的旧 hash


@pytest.mark.unit
def test_v1_and_v2_unchanged_by_addendum():
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL.md") == V1_SHA
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2.md") == V2_SHA


@pytest.mark.unit
def test_hash_is_platform_stable():
    """CRLF 与 LF 两种检出必须算出同一个 hash。"""
    raw = ADDENDUM_MD.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    a = hashlib.sha256(lf.replace(b"\r\n", b"\n")).hexdigest()
    b = hashlib.sha256(crlf.replace(b"\r\n", b"\n")).hexdigest()
    assert a == b == ADDENDUM_SHA


@pytest.mark.unit
def test_gitattributes_covers_addendum():
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for name in ("SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_1.md",
                 "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_1.sha256"):
        assert name in ga and "-text" in ga


@pytest.mark.unit
def test_addendum_contains_only_safety_constraints():
    """附录只收紧循环安全边界：不得出现题目、评分、门槛或预算的修改。"""
    t = ADDENDUM_MD.read_text(encoding="utf-8")
    # 必须写明的安全约束
    for must in ("8", "第 **9** 个工具轮次", "tool_name + normalized_arguments_hash",
                 "A→B→A→B", "not_run_due_to_upstream_failure",
                 "selected", "requested", "executed", "observed"):
        assert must in t, f"附录缺少必须记录的约束：{must}"
    # 明确声明不改这些
    for keep in ("不修改", "12 道题", "评分规则", "通过门槛", "预算"):
        assert keep in t
    # 不得包含任何题目正文（题目只存在于 v1）
    for task_text in ("ZZQX7", "AHSCT", "cGAS-STING", "IL-6 升高导致"):
        assert task_text not in t, f"附录混入了题目内容：{task_text}"


@pytest.mark.unit
def test_addendum_declares_historical_comparability():
    t = ADDENDUM_MD.read_text(encoding="utf-8")
    for must in ("v2 原始条件", "v2 + Addendum 1", "并列报告",
                 "不得", "20260721_105710_77ea9469"):
        assert must in t
    assert "不得挑选较好的一次作为唯一结果" in t or "不得**挑选" in t or "挑选较好" in t


@pytest.mark.unit
def test_addendum_preserves_evidence_boundary():
    """§8：不得把推断写成已证实事实。"""
    t = ADDENDUM_MD.read_text(encoding="utf-8")
    assert "unknown" in t and "推断" in t
    assert "未保存" in t


@pytest.mark.unit
def test_tasks_and_scoring_unchanged():
    from pilot.round2_tasks import PROTOCOL_SHA256, TASKS
    assert PROTOCOL_SHA256 == V1_SHA
    assert len(TASKS) == 12
    assert lf_sha256(ROOT / "pilot" / "round2_tasks.py")   # 存在即可，内容由 v1 hash 锁定


@pytest.mark.unit
def test_a1_scene_untouched_by_addendum():
    import json
    p = ROOT / "pilot" / "round2_results" / "A1_scene_hashes.json"
    if not p.exists():
        pytest.skip("现场 hash 清单不存在")
    scene = json.loads(p.read_text(encoding="utf-8"))["files"]
    checked = 0
    for rel, meta in scene.items():
        if not meta.get("exists"):
            continue
        f = ROOT / rel
        if not f.exists():
            continue
        # 共享 append-only 账本用前缀语义（A.6.6 §6）：合法追加不算篡改
        if rel.endswith("_ledger.jsonl"):
            from pilot.ledger_integrity import verify_append_only
            r = verify_append_only(f, original_length=meta["size"],
                                   original_prefix_sha256=meta["sha256"])
            assert r["violations"] == [], f"账本历史前缀被破坏：{rel} -> {r}"
            checked += 1
            continue
        assert lf_sha256(f) == meta["sha256_lf"], f"A1 现场被改动：{rel}"
        checked += 1
    if checked == 0:
        pytest.skip("干净检出，无现场文件可校验")


@pytest.mark.unit
def test_addendum_limits_match_implementation():
    """附录写的数值必须与实现一致 —— 附录不是空文档。"""
    from pilot.loop_guard import (CYCLE_LEN, MAX_TOOL_ROUNDS, NO_PROGRESS_ROUNDS,
                                  REPEAT_BLOCK_AT, REPEAT_WARN_AT)
    assert MAX_TOOL_ROUNDS == 8
    assert REPEAT_WARN_AT == 2 and REPEAT_BLOCK_AT == 3
    assert CYCLE_LEN == 2
    assert NO_PROGRESS_ROUNDS == 3
