"""A.6.5 §9：与 A1 形状一致的**离线端到端**测试。零真实 API、零 HTTP。

形状：一个有效 PMID + 一个不存在 DOI → fake 模型请求 search_evidence →
PMID exact_hit / DOI zero_hits → 模型结束 → Verifier → Claim extractor → Shadow → Manifest。
断言总 Executor 调用**明显低于 16**，且不产生伪造引用。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot import hard_gate as HG
from pilot import paid_transport as PT
from pilot.executor_trace import ExecutorTrace
from pilot.hard_gate import GatedModel, HardBudgetGate
from pilot.loop_guard import ExecutorLoopGuard

GOOD_PMID = "41657283"                          # 真实存在
BAD_DOI = "10.1080/03009742.2024.2302553"       # 三个权威来源均不可解析
QUESTION = f"请检索 PMID {GOOD_PMID} 与 DOI {BAD_DOI}，分别报告标题、年份、期刊。"
CAPS = {"planner": 2, "verifier": 2, "claim_extractor": 1, "executor": 16}


@pytest.fixture(autouse=True)
def _sw(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("REUMANI_PILOT_PAID", "1")
    monkeypatch.setenv("REUMANI_PILOT_CONFIRM", "test")


def mkgate(tmp_path):
    return HardBudgetGate(stage="test", ledger_path=tmp_path / "e2e.jsonl",
                          max_usd_global=25, max_usd_stage=3, max_usd_task=1.5,
                          max_calls_global=200, max_calls_task=21,
                          max_calls_per_model={"fake-model": 999},
                          max_calls_per_role=dict(CAPS),
                          task_timeout_s=600, max_retries=0, default_max_tokens=3000)


class ScriptedChat:
    def __init__(self, replies):
        self.calls, self._i = 0, 0
        self.replies = list(replies)
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
        self.extra_body = dict(PT.THINKING_DISABLED)

    def invoke(self, *a, **k):
        self.calls += 1
        r = self.replies[min(self._i, len(self.replies) - 1)]
        self._i += 1
        return SimpleNamespace(content=r.get("content", ""),
                               tool_calls=r.get("tool_calls", []),
                               invalid_tool_calls=[],
                               usage_metadata={"input_tokens": 200, "output_tokens": 50},
                               response_metadata={"finish_reason": r.get("finish", "stop"),
                                                  "model_name": "fake-model"})

    def bind_tools(self, *a, **k):
        return self


def fake_search_evidence(query):
    """离线替身：精确 ID 命中/未命中的确定性语义（与 A.6 契约一致）。
    只有那个真实存在的 PMID 命中；那个不可解析的 DOI 必须 zero_hits。"""
    if query == GOOD_PMID:
        return {"retrieval_mode": "exact_id", "retrieval_status": "exact_hit",
                "source_ids": [GOOD_PMID], "candidate_count": 1,
                "title": "From technological iteration to clinical breakthrough",
                "content_level": "abstract"}
    return {"retrieval_mode": "exact_id", "retrieval_status": "zero_hits",
            "source_ids": [], "candidate_count": 0,
            "warnings": ["零命中：仅表示本地库无该条精确记录，≠该领域没有研究"]}


@pytest.mark.unit
def test_a1_shape_offline_end_to_end(tmp_path):
    import tool_registry as TR

    g = mkgate(tmp_path)
    trace = ExecutorTrace(tmp_path / "trace.jsonl", "e2e-run")
    guard = ExecutorLoopGuard()

    # 1) 路由：必须是 exact_id 且选中 search_evidence
    mode, pmids, dois = TR.routing_mode(QUESTION)
    assert mode == "exact_id" and pmids == [GOOD_PMID] and dois == [BAD_DOI]
    selected = TR.select_tool_names(QUESTION)
    assert "search_evidence" in selected
    trace.record_selected(selected)

    # 2) Executor：两次工具调用（PMID / DOI）后收尾 —— 共 3 次模型调用
    exec_script = [
        {"content": "", "tool_calls": [{"name": "search_evidence",
                                        "args": {"query": GOOD_PMID}, "id": "c1"}],
         "finish": "tool_calls"},
        {"content": "", "tool_calls": [{"name": "search_evidence",
                                        "args": {"query": BAD_DOI}, "id": "c2"}],
         "finish": "tool_calls"},
        {"content": f"PMID {GOOD_PMID} 精确命中；DOI {BAD_DOI} 零命中（≠该领域没有研究）。",
         "tool_calls": [], "finish": "stop"},
    ]
    inner_exec = ScriptedChat(exec_script)
    executor = GatedModel(inner_exec, g, role="executor", model_id="fake-model",
                          max_tokens=3000)
    g.start_task("A1_shape")

    results = {}
    for i, step in enumerate(exec_script, 1):
        resp = executor.invoke("react step")
        tcs = list(resp.tool_calls or [])
        trace.record_model_response(outer_iteration=1, executor_call_index=i,
                                    provider="deepseek", model="fake-model",
                                    role="executor", response=resp,
                                    input_tokens=200, output_tokens=50,
                                    next_graph_node=("tools" if tcs else "END"),
                                    termination_reason=(None if tcs else "no_tool_calls"))
        for tc in tcs:
            guard.before_tool_round(tc["name"], tc["args"])
            trace.record_tool_start(tool_call_id=tc["id"], tool_name=tc["name"],
                                    arguments=tc["args"])
            out = fake_search_evidence(tc["args"]["query"])
            results[tc["args"]["query"]] = out
            trace.record_tool_end(tool_call_id=tc["id"], tool_name=tc["name"],
                                  status="ok", result=json.dumps(out, ensure_ascii=False),
                                  structured=True, returned_tool_message=True)
            guard.record_progress(out.get("source_ids") or [f"zero:{tc['args']['query']}"])

    # 3) 两个标识符分别得到正确状态
    assert results[GOOD_PMID]["retrieval_status"] == "exact_hit"
    assert results[BAD_DOI]["retrieval_status"] == "zero_hits"
    assert results[BAD_DOI]["candidate_count"] == 0          # 语义候选未顶替
    assert any("没有研究" in w for w in results[BAD_DOI]["warnings"])

    # 4) Executor 调用数**明显低于 16**
    assert inner_exec.calls == 3, f"Executor 调用 {inner_exec.calls} 次"
    assert g.calls_by_role["executor"] == 3
    assert inner_exec.calls < 16 / 2

    # 5) Verifier + Claim extractor + Shadow 都跑到了
    verifier = GatedModel(ScriptedChat([{"content": json.dumps(
        {"passed": True, "reason": "证据充分", "missing": "无"})}]), g,
        role="verifier", model_id="fake-model", max_tokens=2000)
    claim = GatedModel(ScriptedChat([{"content": "[]"}]), g,
                       role="claim_extractor", model_id="fake-model", max_tokens=2000)
    verifier.invoke("verify")
    claim.invoke("claims")
    assert g.calls_by_role["verifier"] == 1
    assert g.calls_by_role["claim_extractor"] == 1

    import manifest_safety as MS
    manifest = MS.sanitize_manifest({
        "run_id": "e2e-run", "shadow_status": "ok",
        "manifest_schema_version": "runmanifest-v1",
        "selected_tools": selected, "allowed_tools": selected,
        "tool_events": [{"tool_name": "search_evidence", "ok": True}],
        "evidence_cards": [{"evidence_id": f"pmid:{GOOD_PMID}"}],
        "claims": [], "comparison": {"agree": True}})
    assert manifest["manifest_schema_version"] == "runmanifest-v1"
    assert manifest["shadow_status"] == "ok"

    # 6) 不产生伪造引用：只出现真实 PMID，DOI 未被伪装成命中
    cons = trace.consistency()
    assert cons["requested"] == ["search_evidence"]
    assert cons["executed"] == ["search_evidence"]
    assert cons["observed"] == ["search_evidence"]
    assert cons["unauthorized_executed"] == []
    blob = json.dumps(manifest, ensure_ascii=False)
    assert GOOD_PMID in blob and BAD_DOI not in blob     # 不存在的 DOI 未进证据卡

    # 7) 循环护栏未误触发
    assert guard.rounds == 2
    assert not any(e["event"] == "loop_guard_triggered" for e in guard.events)

    # 8) 轨迹不含 Prompt / 密钥 / 认证头 / 绝对路径
    raw = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    for leak in ("sk-", "Authorization", "Cookie", "ANTHROPIC_API_KEY",
                 "DEEPSEEK_API_KEY", "react step", str(ROOT)):
        assert leak not in raw, f"轨迹泄露 {leak!r}"


@pytest.mark.unit
def test_trace_has_no_prompt_or_secret_anywhere(tmp_path):
    """§9-30：任何轨迹都不得包含 Prompt、key、认证头或绝对路径。"""
    tr = ExecutorTrace(tmp_path / "t.jsonl", "r")
    tr.record_model_response(
        outer_iteration=1, executor_call_index=1, provider="p", model="m",
        role="executor",
        response=SimpleNamespace(
            content="Authorization: Bearer sk-secret123 系统提示全文……",
            tool_calls=[], invalid_tool_calls=[],
            response_metadata={"finish_reason": "stop"}))
    tr.record_tool_start(tool_call_id="c1", tool_name="t",
                         arguments={"api_key": "sk-should-not-appear",
                                    "path": r"C:\Users\SomeUser\secret.txt"})
    raw = (tmp_path / "t.jsonl").read_text(encoding="utf-8")
    for leak in ("sk-secret123", "Bearer", "sk-should-not-appear",
                 r"C:\Users\SomeUser", "系统提示全文"):
        assert leak not in raw, f"轨迹泄露 {leak!r}"
    # 但结构化元数据仍在
    recs = [json.loads(l) for l in raw.splitlines() if l.strip()]
    assert recs[0]["content_length"] > 0 and len(recs[0]["content_hash"]) == 64
    assert recs[1]["argument_keys"] == ["api_key", "path"]      # 只留键名
