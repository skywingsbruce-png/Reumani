"""Addendum 2 分流与确定性执行流测试。零真实 API、零付费 LLM（fake 模型 + fake 来源）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot.exact_id_agent import run_exact_id_flow, run_routed_agent
from pilot.query_classifier import classify

PMID = "41657283"
DOI = "10.1080/03009742.2024.2302553"
A1Q = (f"请检索 PMID {PMID} 与 DOI {DOI}，分别报告标题、年份、期刊，"
       f"并说明这两条记录你是通过精确 ID 命中还是排序候选得到的。")


def hit(source, meta): return {"source": source, "retrieval_status": "exact_hit",
                               "metadata": meta, "error_type": None, "http_status": 200}
def zero(source): return {"source": source, "retrieval_status": "zero_hits",
                          "metadata": {}, "error_type": None, "http_status": 200}


class FakeSources:
    def __init__(self, pubmed=None, epmc_pmid=None, crossref=None, doiorg=None, epmc_doi=None):
        self.pubmed, self.epmc_pmid = pubmed or {}, epmc_pmid or {}
        self.crossref, self.doiorg, self.epmc_doi = crossref or {}, doiorg or {}, epmc_doi or {}
    def pubmed_by_pmid(self, p): return self.pubmed.get(p, zero("pubmed"))
    def epmc_by_pmid(self, p): return self.epmc_pmid.get(p, zero("europepmc"))
    def crossref_by_doi(self, d): return self.crossref.get(d, zero("crossref"))
    def doiorg_by_doi(self, d): return self.doiorg.get(d, zero("doi.org"))
    def epmc_by_doi(self, d): return self.epmc_doi.get(d, zero("europepmc"))


def a1_sources():
    meta = {"pmid": PMID, "doi": None, "title": "CAR-T advances", "journal": "MED", "year": "2026"}
    return FakeSources(pubmed={PMID: hit("pubmed", meta)},
                       epmc_pmid={PMID: hit("europepmc", meta)},
                       crossref={DOI: zero("crossref")}, doiorg={DOI: zero("doi.org")},
                       epmc_doi={DOI: zero("europepmc")})


class FakeModel:
    """假 Verifier/LLM：计数 invoke，返回预置内容。"""
    def __init__(self, content):
        self.content_val, self.calls = content, 0
        self.max_retries, self.timeout, self.max_tokens = 0, 120.0, 3000
    def invoke(self, *a, **k):
        from langchain_core.messages import AIMessage
        self.calls += 1
        return AIMessage(content=self.content_val)
    def bind_tools(self, *a, **k): return self


def fake_claim_extractor(calls):
    def _ex(final_text, card_ids):
        calls["n"] += 1
        sup = [card_ids[0]] if card_ids else []
        return [{"text": "PMID 41657283 存在且元数据可核验", "claim_type": "existence",
                 "causal_strength": "none", "supporting_ids": sup}]
    return _ex


# ---------------- 分流（#25 + 确定性）----------------
@pytest.mark.unit
def test_classifier_deterministic():
    assert classify(A1Q) == "exact_id"                         # 含 ID + 核验意图
    assert classify("请综述 SSc 成纤维细胞的机制") == "open"    # 无 ID
    assert classify(f"背景里提到 PMID {PMID}，但请讨论其研究方向") == "open"  # 有 ID 无核验意图→保守 open


@pytest.mark.unit
def test_open_task_still_uses_react(monkeypatch):
    """#25：开放式任务仍走原 ssc_a1.run_agent。"""
    import ssc_a1
    called = {"n": 0}
    def spy(q, **k):
        called["n"] += 1
        return ssc_a1.AgentState(user_query=q)
    monkeypatch.setattr(ssc_a1, "run_agent", spy)
    run_routed_agent("请综述 SSc 机制（无 ID）", shadow=False)
    assert called["n"] == 1


@pytest.mark.unit
def test_exact_id_task_does_not_call_react(monkeypatch):
    """#15/#16/#17：exact-ID 路径不进入 ReAct，Executor LLM=0，search_literature=0。"""
    import ssc_a1
    import ssc_skill_agent as SK
    react = {"n": 0}
    monkeypatch.setattr(ssc_a1, "execute", lambda *a, **k: react.__setitem__("n", react["n"] + 1))
    lit = {"n": 0}
    orig = SK.search_literature
    def lit_spy(*a, **k):
        lit["n"] += 1
        return orig(*a, **k)
    monkeypatch.setattr(SK, "search_literature", lit_spy)
    exec_spy = FakeModel("SHOULD-NOT-BE-CALLED")

    cc = {"n": 0}
    state = run_routed_agent(A1Q, sources=a1_sources(),
                             verifier_model=FakeModel('{"passed": true, "reason": "ok", "missing": "无"}'),
                             claim_extractor=fake_claim_extractor(cc),
                             executor_model=exec_spy)
    assert react["n"] == 0                                      # #15 未进入 ReAct execute
    assert exec_spy.calls == 0                                  # #16 Executor LLM 调用 0
    assert lit["n"] == 0                                        # #17 search_literature 调用 0


@pytest.mark.unit
def test_a1_shape_reaches_verifier_claim_shadow_manifest():
    """#21：A1 形状完整到 Verifier / Claim / Shadow / Manifest。"""
    verifier = FakeModel('{"passed": true, "reason": "PMID 已精确核验", "missing": "无"}')
    cc = {"n": 0}
    state = run_exact_id_flow(A1Q, sources=a1_sources(), verifier_model=verifier,
                              claim_extractor=fake_claim_extractor(cc))
    # Verifier 实际运行且决定最终答案
    assert verifier.calls >= 1
    assert state.verification_results and state.verification_results[-1]["passed"] is True
    assert "未验证" not in state.final_answer
    # Claim extractor 实际运行
    assert cc["n"] >= 1
    # Shadow 实际运行并生成 Manifest
    assert state.shadow.get("shadow_status") == "ok"
    assert state.shadow.get("manifest_schema_version") or state.shadow.get("run_id")
    # EvidenceCard 只含有效 PMID，不含 not_found 的 DOI
    cards = state.shadow.get("evidence_cards") or []
    assert len(cards) == 1 and cards[0].get("pmid") == PMID
    assert DOI not in str(cards)
    # 两个 ID 都进入终态
    assert state.research_plan["all_terminal"] is True
    ids = {i["id"]: i["status"] for i in state.research_plan["ids"]}
    assert ids[PMID] == "verified" and ids[DOI] == "not_found"


@pytest.mark.unit
def test_a1_pmid_verified_doi_not_found_offline():
    """#8 形态的离线 A1：PMID→verified、DOI→not_found（EPMC 仅补充）。"""
    state = run_exact_id_flow(A1Q, sources=a1_sources(),
                              verifier_model=FakeModel('{"passed": true, "reason": "ok", "missing": "无"}'),
                              claim_extractor=fake_claim_extractor({"n": 0}))
    ids = {i["id"]: i["status"] for i in state.research_plan["ids"]}
    assert ids[PMID] == "verified" and ids[DOI] == "not_found"


@pytest.mark.unit
def test_concurrent_run_ids_isolated():
    """#24：两个并发 run 的 run_id / 卡 / manifest 互不串扰。"""
    other_pmid = "30000001"
    q2 = f"核验 PMID {other_pmid} 的标题年份期刊，是否精确命中？"
    s2 = FakeSources(pubmed={other_pmid: hit("pubmed", {"pmid": other_pmid, "title": "Other", "year": "2020"})},
                     epmc_pmid={other_pmid: hit("europepmc", {"pmid": other_pmid, "year": "2020"})})
    st1 = run_exact_id_flow(A1Q, sources=a1_sources(), stamp="RUN_A",
                            verifier_model=FakeModel('{"passed": true, "reason": "ok", "missing": "无"}'),
                            claim_extractor=fake_claim_extractor({"n": 0}))
    st2 = run_exact_id_flow(q2, sources=s2, stamp="RUN_B",
                            verifier_model=FakeModel('{"passed": true, "reason": "ok", "missing": "无"}'),
                            claim_extractor=fake_claim_extractor({"n": 0}))
    assert st1.shadow["run_id"] == "RUN_A" and st2.shadow["run_id"] == "RUN_B"
    c1 = {c["pmid"] for c in st1.shadow["evidence_cards"]}
    c2 = {c["pmid"] for c in st2.shadow["evidence_cards"]}
    assert c1 == {PMID} and c2 == {other_pmid}                 # 卡不串扰
    assert PMID not in str(st2.shadow["evidence_cards"])


@pytest.mark.unit
def test_manifest_no_secrets_or_abs_paths():
    """#23：Manifest 无密钥、无绝对路径。"""
    import json
    state = run_exact_id_flow(A1Q, sources=a1_sources(),
                              verifier_model=FakeModel('{"passed": true, "reason": "ok", "missing": "无"}'),
                              claim_extractor=fake_claim_extractor({"n": 0}))
    blob = json.dumps(state.shadow, ensure_ascii=False, default=str)
    # 用拼接构造禁用模式，避免测试文件自身触发敏感信息扫描
    bad_patterns = ["F:" + chr(92) + "SSC", "sk" + "-", "ANTHROPIC_" + "API_" + "KEY",
                    "DEEPSEEK_" + "API_" + "KEY", "-----" + "BEGIN"]
    for bad in bad_patterns:
        assert bad not in blob
