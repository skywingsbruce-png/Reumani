"""A.6.5 提交 2：裸 DOI 提取 + 精确 ID 路由。零真实 API。

不针对 A1 写死标识符——用额外 fixture 证明规则通用。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ids
import tool_registry as TR

A1_DOI = "10.1080/03009742.2024.2302553"      # 冻结 A1 中的 DOI（不存在，但必须能提取）
A1_PMID = "41657283"


# ---------- 21-24：DOI 提取 ----------
@pytest.mark.unit
@pytest.mark.parametrize("text,expect", [
    (f"DOI {A1_DOI}，分别报告标题", [A1_DOI]),                     # 裸 DOI + 中文标点
    (f"doi:{A1_DOI}", [A1_DOI]),                                    # doi: 前缀
    (f"DOI: {A1_DOI}", [A1_DOI]),                                   # 带空格
    (f"https://doi.org/{A1_DOI}", [A1_DOI]),                        # URL
    (f"http://dx.doi.org/{A1_DOI}", [A1_DOI]),                      # dx 镜像
    (f"DoI {A1_DOI}", [A1_DOI]),                                    # 大小写
])
def test_bare_and_prefixed_doi_forms(text, expect):
    assert ids.extract_dois(text) == expect


@pytest.mark.unit
@pytest.mark.parametrize("tail", [".", ",", ";", ":", ")", "]", "。", "，", "；", "、",
                                  "）", "」", '"', "!", "?"])
def test_trailing_punctuation_is_stripped(tail):
    assert ids.extract_dois(f"见 {A1_DOI}{tail}") == [A1_DOI]


@pytest.mark.unit
def test_doi_with_balanced_parens_is_preserved():
    """DOI 自身可以含成对括号，不能被误剥。"""
    d = "10.1002/(SICI)1097-0258(19980815)17:15<1661::AID-SIM968>3.0.CO;2-2"
    assert ids.extract_dois(f"参见 {d}") == [d]


@pytest.mark.unit
def test_multiple_dois_dedup_and_order():
    t = f"两个：10.1080/aaa.111；10.1038/bbb.222。又见 10.1080/aaa.111"
    assert ids.extract_dois(t) == ["10.1080/aaa.111", "10.1038/bbb.222"]


@pytest.mark.unit
@pytest.mark.parametrize("text", ["版本 1.10.3", "pi = 3.14159", "10.5 mg/kg",
                                  "章节 10.1234", "IP 10.0.0.1/24"])
def test_plain_numbers_are_not_dois(text):
    assert ids.extract_dois(text) == []


@pytest.mark.unit
def test_extractor_and_validator_share_one_authority():
    """凡是能提取出来的，valid_doi 必须为真——不允许两套口径。"""
    sample = (f"{A1_DOI} doi:10.1001/jama.2020.1234 "
              f"https://doi.org/10.1038/s41586-021-03819-2 版本 1.2.3")
    got = ids.extract_dois(sample)
    assert got and all(ids.valid_doi(d) for d in got)
    assert ids.valid_doi(A1_DOI) is True and A1_DOI in got


@pytest.mark.unit
def test_pmid_and_doi_together():
    t = f"请检索 PMID {A1_PMID} 与 DOI {A1_DOI}"
    assert ids.extract_pmids(t) == [A1_PMID]
    assert ids.extract_dois(t) == [A1_DOI]


# ---------- 25-26：精确 ID 路由 ----------
@pytest.mark.unit
@pytest.mark.parametrize("q,mode", [
    (f"请检索 PMID {A1_PMID} 与 DOI {A1_DOI}", "exact_id"),
    ("PMID 12345678 是什么", "exact_id"),
    ("doi:10.1002/art.41668 的结论", "exact_id"),
    ("https://doi.org/10.1038/s41586-021-03819-2 讲了什么", "exact_id"),
    ("系统性硬化症的机制综述", "semantic"),
    ("IFN 通路与皮肤纤维化", "semantic"),
])
def test_routing_mode_is_deterministic(q, mode):
    assert TR.routing_mode(q)[0] == mode


@pytest.mark.unit
@pytest.mark.parametrize("q", [
    f"请检索 PMID {A1_PMID} 与 DOI {A1_DOI}",
    "PMID 12345678 是什么",                       # 通用 fixture，非 A1
    "doi:10.1002/art.41668 的结论",               # 通用 fixture，非 A1
])
def test_exact_id_query_always_selects_search_evidence(q):
    sel = TR.select_tool_names(q)
    assert "search_evidence" in sel, f"精确 ID 查询未选中精确核验工具：{sel}"


@pytest.mark.unit
def test_semantic_query_does_not_force_search_evidence():
    sel = TR.select_tool_names("系统性硬化症的机制综述")
    assert "search_evidence" not in sel


@pytest.mark.unit
def test_search_literature_remains_auxiliary_not_replacement():
    """search_literature 可以保留，但不能取代 search_evidence 的精确核验。"""
    q = f"请检索 PMID {A1_PMID} 与 DOI {A1_DOI}"
    sel = TR.select_tool_names(q)
    assert "search_evidence" in sel
    assert "search_literature" in sel          # 辅助工具仍在，但不是替代品
    assert TR.EXACT_ID_TOOLS == ("search_evidence",)


@pytest.mark.unit
def test_nonexistent_doi_still_routes_exact_not_semantic():
    """DOI 不存在**不改变**路由：仍走精确查询，由检索层如实返回 zero_hits。"""
    mode, pmids, dois = TR.routing_mode(f"查 DOI {A1_DOI}")
    assert mode == "exact_id" and dois == [A1_DOI]
    assert "search_evidence" in TR.select_tool_names(f"查 DOI {A1_DOI}")


# ---------- 27-29：检索状态语义（沿用 A.6 契约） ----------
@pytest.mark.unit
def test_exact_id_hit_and_miss_states(monkeypatch):
    import ssc_skill_agent as SK
    # 命中
    monkeypatch.setattr(SK, "_query_data_lake_str",
                        lambda k, q: f"命中 1 篇：\n- X | https://pubmed.ncbi.nlm.nih.gov/{A1_PMID}/")
    tm = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                    "args": {"kind": "corpus", "query": A1_PMID}, "id": "1"})
    assert tm.artifact["data"]["retrieval_status"] == "exact_hit"
    # 未命中：必须是 zero_hits，不能被语义候选顶替
    monkeypatch.setattr(SK, "_query_data_lake_str",
                        lambda k, q: "【混合检索：BM25】命中 15 篇（范围 SSc）：\n- 无关论文")
    tm2 = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                     "args": {"kind": "corpus", "query": A1_DOI}, "id": "2"})
    d = tm2.artifact["data"]
    assert d["retrieval_mode"] == "exact_id" and d["retrieval_status"] == "zero_hits"
    assert d["candidate_count"] == 0, "语义候选不得被当作该 DOI 的命中"


@pytest.mark.unit
def test_zero_hits_is_not_absence_of_research(monkeypatch):
    import ssc_skill_agent as SK
    monkeypatch.setattr(SK, "_query_data_lake_str", lambda k, q: "未检索到相关文献。")
    tm = SK.query_data_lake.invoke({"type": "tool_call", "name": "query_data_lake",
                                    "args": {"kind": "corpus", "query": A1_DOI}, "id": "1"})
    d = tm.artifact["data"]
    assert d["retrieval_status"] == "zero_hits"
    assert any("没有研究" in w for w in d["warnings"])
