"""Addendum 3（开放任务有界收敛契约）冻结校验。零真实 API。
不改 v1/v2/Addendum 1/2 及其 hash；附录不含 B1 原题/评分答案；不扩大预算/上限；
novelty 与步骤预算与离线可执行 replay 一致。"""
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ADDENDUM3_SHA = "49486a3a80004163c1d648c3592078b7e89c8e8800fef65dcfd1f69bc045a0b1"
ADDENDUM3_MD = ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_3.md"
ADDENDUM3_SHA_FILE = ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_3.sha256"

V1_SHA = "5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22"
V2_SHA = "c76f589485e4ebfd728c27b653d2735f3ebd1c6930087c244e4efbdba9d66696"
A1_SHA = "de3afcdd2131ba17717eb2d93a543e350aeff16e11c2ecc32edd8d583f9ca7f3"
A2_SHA = "b3646d346c1e18fa1293fa275bc81a04e95f6aed917213d6689ee6c528a31381"


def lf_sha256(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.mark.unit
def test_addendum3_hash_is_frozen():
    assert lf_sha256(ADDENDUM3_MD) == ADDENDUM3_SHA


@pytest.mark.unit
def test_addendum3_hash_is_platform_stable():
    raw = ADDENDUM3_MD.read_bytes()
    lf, crlf = raw.replace(b"\r\n", b"\n"), raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    a = hashlib.sha256(lf.replace(b"\r\n", b"\n")).hexdigest()
    b = hashlib.sha256(crlf.replace(b"\r\n", b"\n")).hexdigest()
    assert a == b == ADDENDUM3_SHA           # LF/CRLF 一致


@pytest.mark.unit
def test_addendum3_sha_file_records_same_hash():
    first = ADDENDUM3_SHA_FILE.read_text(encoding="utf-8").splitlines()[0]
    assert first.split()[0] == ADDENDUM3_SHA and "ADDENDUM_3.md" in first


@pytest.mark.unit
def test_prior_protocols_unchanged():
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL.md") == V1_SHA
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2.md") == V2_SHA
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_1.md") == A1_SHA
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_2.md") == A2_SHA


@pytest.mark.unit
def test_gitattributes_covers_addendum3():
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for name in ("SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_3.md",
                 "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_3.sha256"):
        assert any(l.startswith(name) and l.rstrip().endswith("-text") for l in ga.splitlines())


@pytest.mark.unit
def test_addendum3_does_not_contain_b1_question_or_scoring():
    """附录不得包含 B1 原题文本或评分答案（避免题目/答案进入受版本控制的规范）。"""
    txt = ADDENDUM3_MD.read_text(encoding="utf-8")
    from pilot.round2_tasks import TASKS
    b1 = TASKS["B1"]
    assert b1["question"] not in txt                 # 不含 B1 原题
    assert "IL-6" not in txt and "IL6" not in txt     # 不含题目关键词
    for scoring_field in ("forbidden", "hard_fail"):  # list 型评分条目
        for item in b1.get(scoring_field, []):
            assert item not in txt
    assert b1.get("min_evidence", "___") not in txt   # string 型评分条目（整句）


@pytest.mark.unit
def test_addendum3_does_not_widen_caps():
    """附录不扩大 v2/Addendum 1 的上限：显式声明不变，且不出现更高的角色/预算数字。"""
    txt = ADDENDUM3_MD.read_text(encoding="utf-8")
    assert "不扩大" in txt and "保持不变" in txt
    assert "工具总轮次 8 仍作外层上限" in txt
    # 不得把角色上限写成比 v2 更大的值
    for bad in ("Executor≤17", "Executor≤32", "Planner≤3", "Verifier≤3", "Claim≤2",
                "$3.00 / 单题", "单题≤$2", "单题≤$3"):
        assert bad not in txt


@pytest.mark.unit
def test_novelty_and_step_budget_consistent_with_replay():
    """novelty 四级与步骤预算须与离线可执行 replay 一致。"""
    txt = ADDENDUM3_MD.read_text(encoding="utf-8")
    for tier in ("transport novelty", "identifier novelty", "evidence novelty", "decision novelty"):
        assert tier in txt
    assert "transport novelty 绝不能单独重置" in txt
    assert "文献检索步骤：**最多 2 次**" in txt and "数据湖查询步骤：**最多 1 次**" in txt
    # 与离线 replay 的实际预算一致
    replay = (ROOT / "tests" / "test_open_task_convergence_replay.py").read_text(encoding="utf-8")
    assert '"call_budget": 2' in replay and '"call_budget": 1' in replay
    for tier in ("transport", "identifier", "evidence", "decision"):
        assert f'"{tier}"' in replay


@pytest.mark.unit
def test_controlled_insufficient_fields_complete():
    txt = ADDENDUM3_MD.read_text(encoding="utf-8")
    for f in ("resolved_question", "available_evidence", "unsupported_claims", "causal_strength",
              "missing_evidence", "limitations", "recommended_next_action"):
        assert f in txt
    assert "禁止出现“还缺：[]”" in txt


@pytest.mark.unit
def test_single_telemetry_authority_and_failure_semantics():
    txt = ADDENDUM3_MD.read_text(encoding="utf-8")
    for src in ("Lifecycle", "EvidenceAccumulator", "Gate", "Stage counters"):
        assert src in txt
    assert "不得" in txt and "metrics.execution.tool_calls" in txt   # 淘汰旧口径
    for fail in ("structured_result_invalid", "evidence_normalization_failed", "step_budget_exhausted",
                 "scientific_no_progress", "synthesis_failed", "verification_failed",
                 "telemetry_conflict"):
        assert fail in txt
    assert "primary_failure" in txt


@pytest.mark.unit
def test_exact_id_unaffected_and_historical_comparability():
    txt = ADDENDUM3_MD.read_text(encoding="utf-8")
    assert "exact-ID 路径" in txt and "route=open" in txt          # 仅作用 open
    assert "旧 Verifier 的最终裁决权" in txt
    # 历史可比性声明存在
    assert "自由 ReAct" in txt and "不是相同软件条件" in txt
    assert "不得覆盖" in txt and "重测通过" in txt


@pytest.mark.unit
def test_addendum3_scope_is_open_task_contract():
    txt = ADDENDUM3_MD.read_text(encoding="utf-8")
    for must in ("OpenTaskExecutionContract", "PlanStepState", "LiteratureRecord",
                 "Controlled insufficient", "因果校准", "遥测单一权威",
                 "plan → collect → normalize → accumulate → assess_step → synthesize → verify → finish"):
        assert must in txt
