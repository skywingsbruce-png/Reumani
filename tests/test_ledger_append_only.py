"""A.6.6 §6：共享 append-only 账本的前缀完整性。零真实 API。

替代原来的"整文件 hash 相等"——那种写法会把**合法追加**误判为篡改，
正是 A1-rerun 之后本机 2 项测试变红的原因。
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot.ledger_integrity import baseline_from_file, verify_append_only

# A1-original 结束时的账本基线（见 A1_scene_hashes.json / A1_rerun_boundary.json）
A1_ORIGINAL_LEN = 9935
A1_ORIGINAL_PREFIX_SHA = ("482f9b1e365be79f9ca82dc34cb8c593"
                          "dfeea10cf61ba7ae5203314bd0ec8353")
LEDGER = ROOT / "pilot" / "round2_results" / "stage1_ledger.jsonl"


def _mk(tmp_path, lines):
    p = tmp_path / "l.jsonl"
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines),
                 encoding="utf-8")
    return p


ORIG = [{"event": "reserved", "call_uid": f"s:A1:{i}", "stage": "stage1",
         "task_id": "A1", "role": "executor"} for i in range(3)]


# 1 —— 合法追加通过
@pytest.mark.unit
def test_legal_append_passes(tmp_path):
    p = _mk(tmp_path, ORIG)
    base = baseline_from_file(p)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "reserved", "stage": "stage1",
                            "task_id": "A1", "role": "planner"}) + "\n")
    r = verify_append_only(p, **base)
    assert r["violations"] == []
    assert r["appended_events"] == 1
    assert r["current_length"] > r["original_length"]


# 2 —— 修改历史字节失败
@pytest.mark.unit
def test_modifying_history_fails(tmp_path):
    p = _mk(tmp_path, ORIG)
    base = baseline_from_file(p)
    raw = p.read_bytes()
    p.write_bytes(raw.replace(b"executor", b"planner_", 1))
    r = verify_append_only(p, **base)
    assert "prefix_modified" in r["violations"]


# 3 —— 截断失败
@pytest.mark.unit
def test_truncation_fails(tmp_path):
    p = _mk(tmp_path, ORIG)
    base = baseline_from_file(p)
    raw = p.read_bytes()
    p.write_bytes(raw[: len(raw) // 2])
    r = verify_append_only(p, **base)
    assert "truncated" in r["violations"]


# 4 —— 中间插入失败
@pytest.mark.unit
def test_insertion_in_middle_fails(tmp_path):
    p = _mk(tmp_path, ORIG)
    base = baseline_from_file(p)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(1, json.dumps({"event": "inserted"}))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = verify_append_only(p, **base)
    assert "prefix_modified" in r["violations"]


# 5 —— 重排失败
@pytest.mark.unit
def test_reorder_fails(tmp_path):
    p = _mk(tmp_path, ORIG)
    base = baseline_from_file(p)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0], lines[2] = lines[2], lines[0]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = verify_append_only(p, **base)
    assert "prefix_modified" in r["violations"]


# 6 —— 追加冒充 original run 的事件失败
@pytest.mark.unit
def test_appending_events_claiming_original_run_fails(tmp_path):
    p = _mk(tmp_path, ORIG)
    base = baseline_from_file(p)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "reserved", "task_id": "A1-original"}) + "\n")
    r = verify_append_only(p, original_run_ids={"A1-original"}, **base)
    assert any("appended_events_from_original_run" in v for v in r["violations"])


# 7 —— 追加不可解析内容失败
@pytest.mark.unit
def test_unparsable_append_fails(tmp_path):
    p = _mk(tmp_path, ORIG)
    base = baseline_from_file(p)
    with p.open("a", encoding="utf-8") as f:
        f.write("{ this is not json\n")
    r = verify_append_only(p, **base)
    assert any("appended_unparsable" in v for v in r["violations"])


# 8 —— CI 无现场文件时明确 skip
@pytest.mark.unit
def test_missing_ledger_is_skipped_not_failed(tmp_path):
    r = verify_append_only(tmp_path / "nope.jsonl", original_length=1,
                           original_prefix_sha256="x")
    assert r["exists"] is False and r["violations"] == []
    assert "skipped" in r


# 9 —— 本机存在历史账本时，真实 A1-original 前缀必须完好
@pytest.mark.unit
def test_real_a1_original_prefix_intact():
    if not LEDGER.exists():
        pytest.skip("干净检出：共享账本被 .gitignore 排除，无可校验对象")
    r = verify_append_only(LEDGER, original_length=A1_ORIGINAL_LEN,
                           original_prefix_sha256=A1_ORIGINAL_PREFIX_SHA)
    assert r["violations"] == [], f"A1-original 账本前缀被破坏：{r}"
    assert r["current_length"] >= A1_ORIGINAL_LEN
    assert r["prefix_sha256"] == A1_ORIGINAL_PREFIX_SHA
    # rerun 的事件确实是追加上去的
    assert r["appended_events"] > 0
