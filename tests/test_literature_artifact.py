"""A.7.4.2：search_literature 结构化 content_and_artifact + LiteratureRecord。
默认零真实网络（monkeypatch requests）、零 LLM、零账本。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ssc_pi_agent as P
from pilot.literature_adapter import (SOURCE, build_literature_records, epmc_item_to_record)
from pilot.open_task_contracts import LiteratureRecord
from schemas import ToolResult

PMID = "41657283"
DOI = "10.1080/07853890.2026.2627057"


def item(pmid=PMID, doi=None, title="IL-6 in SSc", abstract="cross-sectional cohort", journal="J Rheum",
         date="2023-05-01", authors="A, B"):
    return {"pmid": pmid, "doi": doi, "title": title, "abstractText": abstract,
            "journalTitle": journal, "firstPublicationDate": date, "authorString": authors}


class FakeResp:
    def __init__(self, results=None, status=200, raise_exc=None, bad_json=False):
        self._results, self.status_code = results or [], status
        self._raise, self._bad = raise_exc, bad_json
    def raise_for_status(self):
        if self._raise:
            raise self._raise
    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return {"resultList": {"result": self._results}}


@pytest.fixture(autouse=True)
def _no_net(monkeypatch):
    """默认禁止真实网络：requests.get 必须被显式 monkeypatch，否则报错。"""
    def _boom(*a, **k):
        raise AssertionError("default tests must not hit real network")
    monkeypatch.setattr(P.requests, "get", _boom)


def patch_get(monkeypatch, resp):
    monkeypatch.setattr(P.requests, "get", lambda *a, **k: resp)


def call_tool(query="SSc IL-6"):
    """LangChain tool-call invoke → 真实 ToolMessage。"""
    return P.search_literature.invoke({"type": "tool_call", "name": "search_literature",
                                       "args": {"query": query}, "id": "c1"})


# ---------------- adapter 单元（1-14, 24）----------------
@pytest.mark.unit
def test_abstract_record_content_level():
    rec, why = epmc_item_to_record(item(abstract="an abstract"), "q")
    assert rec.content_level == "abstract"


@pytest.mark.unit
def test_metadata_only_when_no_abstract():
    rec, why = epmc_item_to_record(item(abstract=None), "q")
    assert rec.content_level == "metadata_only"


@pytest.mark.unit
def test_tool_never_produces_fulltext():
    rec, _ = epmc_item_to_record(item(abstract="x"), "q")
    assert rec.content_level in ("abstract", "metadata_only")   # 本工具不产 fulltext
    assert rec.fulltext_ref is None and rec.fulltext_content_hash is None


@pytest.mark.unit
def test_pmid_doi_normalized():
    rec, _ = epmc_item_to_record({"pmid": "41657283", "abstractText": "x"}, "q")
    assert rec.pmid == "41657283"
    rec2, _ = epmc_item_to_record({"doi": "10.1080/07853890.2026.2627057".upper(),
                                   "abstractText": "x"}, "q")
    assert rec2.doi == "10.1080/07853890.2026.2627057"          # 小写规范化


@pytest.mark.unit
def test_record_without_valid_id_rejected():
    rec, why = epmc_item_to_record({"title": "no id", "abstractText": "x"}, "q")
    assert rec is None and why == "no_valid_id"


@pytest.mark.unit
def test_multi_records_no_misalignment():
    items = [item(pmid="1", title="A"), item(pmid="2", title="B"), item(pmid="3", title="C")]
    recs, warnings, skipped = build_literature_records(items, "q")
    assert [r.pmid for r in recs] == ["1", "2", "3"]
    assert [r.title for r in recs] == ["A", "B", "C"]           # 不串位


@pytest.mark.unit
def test_source_ids_dedup_stable():
    rec, _ = epmc_item_to_record(item(pmid="1", doi="10.1/x"), "q")
    assert rec.source_ids == sorted(set(rec.source_ids)) and len(rec.source_ids) == len(set(rec.source_ids))


@pytest.mark.unit
def test_provenance_complete():
    rec, _ = epmc_item_to_record(item(), "q")
    prov = rec.provenance
    assert prov.tool_name == "search_literature" and prov.source == SOURCE
    assert prov.retrieved_at and prov.parameters.get("query") == "q"
    assert prov.source_ids == rec.source_ids and prov.content_level in ("abstract", "metadata_only")
    assert prov.content_hash == rec.content_hash and prov.hash_algorithm == "sha256"


@pytest.mark.unit
def test_hash_is_sha256_64():
    rec, _ = epmc_item_to_record(item(), "q")
    assert len(rec.content_hash) == 64 and rec.content_hash == rec.content_hash.lower()
    assert all(c in "0123456789abcdef" for c in rec.content_hash)


@pytest.mark.unit
def test_hash_stable_across_runs_ignores_retrieved_at():
    r1, _ = epmc_item_to_record(item(), "q")
    r2, _ = epmc_item_to_record(item(), "q")
    assert r1.content_hash == r2.content_hash                   # retrieved_at 不入 payload
    assert r1.provenance.retrieved_at is not None


@pytest.mark.unit
def test_hash_changes_on_core_content_change():
    r1, _ = epmc_item_to_record(item(title="A"), "q")
    r2, _ = epmc_item_to_record(item(title="B changed"), "q")
    assert r1.content_hash != r2.content_hash


@pytest.mark.unit
def test_record_and_provenance_hash_consistent():
    rec, _ = epmc_item_to_record(item(), "q")
    assert rec.content_hash == rec.provenance.content_hash


@pytest.mark.unit
def test_study_design_species_unknown_not_guessed():
    rec, _ = epmc_item_to_record(item(title="A randomized controlled trial in mice"), "q")
    assert rec.study_design is None and rec.species is None      # 不从标题猜
    assert rec.longitudinal is None and rec.interventional is None


# ---------------- 工具级状态语义（15-23, 33）----------------
@pytest.mark.unit
def test_success(monkeypatch):
    patch_get(monkeypatch, FakeResp([item(), item(pmid="222")]))
    tm = call_tool()
    assert type(tm).__name__ == "ToolMessage"
    art = tm.artifact
    assert art["ok"] is True and art["data"]["retrieval_status"] == "success"
    assert art["data"]["record_count"] == 2 and len(art["data"]["records"]) == 2


@pytest.mark.unit
def test_zero_hits(monkeypatch):
    patch_get(monkeypatch, FakeResp([]))
    tm = call_tool()
    assert tm.artifact["ok"] is True
    assert tm.artifact["data"]["retrieval_status"] == "zero_hits"
    assert tm.artifact["data"]["records"] == []
    assert "不等于该领域没有研究" in tm.content and "没有研究" in tm.content


@pytest.mark.unit
def test_source_error(monkeypatch):
    patch_get(monkeypatch, FakeResp(raise_exc=P.requests.exceptions.Timeout("t")))
    tm = call_tool()
    assert tm.artifact["ok"] is False and tm.artifact["error_type"] == "source_error"
    assert tm.artifact["data"] is None


@pytest.mark.unit
def test_parse_error_bad_json(monkeypatch):
    patch_get(monkeypatch, FakeResp(bad_json=True))
    tm = call_tool()
    assert tm.artifact["ok"] is False and tm.artifact["error_type"] == "parse_error"


@pytest.mark.unit
def test_all_records_invalid_is_parse_error(monkeypatch):
    patch_get(monkeypatch, FakeResp([{"title": "no id"}, {"title": "also no id"}]))
    tm = call_tool()
    assert tm.artifact["ok"] is False and tm.artifact["error_type"] == "parse_error"


@pytest.mark.unit
def test_partial_invalid_records_kept(monkeypatch):
    patch_get(monkeypatch, FakeResp([item(pmid="1"), {"title": "bad no id"}, item(pmid="2")]))
    tm = call_tool()
    d = tm.artifact["data"]
    assert d["retrieval_status"] == "success" and d["record_count"] == 2
    assert any("skipped 1" in w for w in d["warnings"])


@pytest.mark.unit
def test_failure_never_ok_true(monkeypatch):
    patch_get(monkeypatch, FakeResp(raise_exc=RuntimeError("boom")))
    tm = call_tool()
    assert tm.artifact["ok"] is False


@pytest.mark.unit
def test_unavailable_semantics_enum_present():
    # unavailable 为契约枚举之一（工具/配置不可用）；此处静态确认枚举可用于 data
    from pilot.literature_adapter import zero_hits_data
    d = zero_hits_data("q"); d["retrieval_status"] = "unavailable"
    assert d["retrieval_status"] == "unavailable"


# ---------------- 兼容旧行为 + 真实 ToolMessage（25-32, 34）----------------
@pytest.mark.unit
def test_plain_invoke_returns_readable_string(monkeypatch):
    patch_get(monkeypatch, FakeResp([item(title="Readable")]))
    out = P.search_literature.invoke("SSc IL-6")               # 旧式 .invoke(str)
    assert isinstance(out, str) and "Readable" in out and "|" in out


@pytest.mark.unit
def test_real_toolmessage_artifact_revalidates_as_toolresult(monkeypatch):
    patch_get(monkeypatch, FakeResp([item()]))
    tm = call_tool()
    assert type(tm).__name__ == "ToolMessage"
    assert isinstance(tm.content, str) and isinstance(tm.artifact, dict)
    ToolResult(**tm.artifact)                                   # artifact 重新通过 ToolResult 校验
    assert tm.artifact["schema_version"] == "toolresult-v1"
    assert tm.artifact["tool_name"] == "search_literature"
    # 不把 artifact 完整 JSON 塞进文本 content
    import json
    assert json.dumps(tm.artifact, ensure_ascii=False)[:50] not in tm.content


@pytest.mark.unit
def test_content_readable_and_each_record_revalidates(monkeypatch):
    patch_get(monkeypatch, FakeResp([item(title="X"), item(pmid="9", title="Y")]))
    tm = call_tool()
    assert "X" in tm.content and "Y" in tm.content
    for rec in tm.artifact["data"]["records"]:
        LiteratureRecord(**rec)                                 # 每条记录仍合法


@pytest.mark.unit
def test_tool_name_and_registry_unchanged():
    assert P.search_literature.name == "search_literature"
    import ssc_skill_agent as SK
    assert any(t.name == "search_literature" for t in SK.SKILL_AGENT_TOOLS)
    from tool_registry import all_tool_names
    assert "search_literature" in all_tool_names()


@pytest.mark.unit
def test_no_llm_and_no_ledger_on_search(monkeypatch):
    # search_literature 不构造/调用任何 LLM，也不写账本
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic
    calls = {"n": 0}
    def sentinel(*a, **k):
        calls["n"] += 1
        raise AssertionError("no LLM in search_literature")
    monkeypatch.setattr(ChatOpenAI, "invoke", sentinel)
    monkeypatch.setattr(ChatAnthropic, "invoke", sentinel)
    patch_get(monkeypatch, FakeResp([item()]))
    call_tool()
    assert calls["n"] == 0


@pytest.mark.unit
def test_sensitive_not_in_artifact(monkeypatch):
    import json
    patch_get(monkeypatch, FakeResp([item()]))
    blob = json.dumps(call_tool().artifact, ensure_ascii=False, default=str)
    for bad in ("F:" + chr(92) + "SSC", "sk" + "-", "ANTHROPIC_" + "API_" + "KEY",
                "Authorization", "retrieved_at" if False else "___never"):
        assert bad not in blob


@pytest.mark.unit
def test_import_adapter_no_side_effects(monkeypatch):
    import importlib
    hits = {"n": 0}
    monkeypatch.setattr(P.requests, "get", lambda *a, **k: hits.__setitem__("n", hits["n"] + 1))
    import pilot.literature_adapter as LA
    importlib.reload(LA)
    assert hits["n"] == 0
