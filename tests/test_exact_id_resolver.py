"""Addendum 2 resolver 测试。全部零真实 API（注入 FakeSources），零付费 LLM。"""
import collections
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot.exact_id_resolver import (ExactIdBatchResult, _Retryable, _with_retry,
                                     extract_ids, normalize_doi, normalize_pmid,
                                     resolve_exact_ids, resolve_one)

PMID = "41657283"
DOI = "10.1080/03009742.2024.2302553"


def hit(source, meta):
    return {"source": source, "retrieval_status": "exact_hit", "metadata": meta,
            "error_type": None, "http_status": 200}


def zero(source):
    return {"source": source, "retrieval_status": "zero_hits", "metadata": {},
            "error_type": None, "http_status": 200}


def err(source, etype="rate_limited_429"):
    return {"source": source, "retrieval_status": "source_error", "metadata": {},
            "error_type": etype, "http_status": None}


class FakeSources:
    """按 normalized_id 返回预置来源结果，并计数每个 (source,id) 的查询次数。"""

    def __init__(self, pubmed=None, epmc_pmid=None, crossref=None, doiorg=None, epmc_doi=None):
        self.pubmed, self.epmc_pmid = pubmed or {}, epmc_pmid or {}
        self.crossref, self.doiorg, self.epmc_doi = crossref or {}, doiorg or {}, epmc_doi or {}
        self.calls = collections.Counter()

    def _get(self, table, id_, source, default_zero=True):
        self.calls[(source, id_)] += 1
        v = table.get(id_)
        if v is None:
            return zero(source) if default_zero else v
        return v

    def pubmed_by_pmid(self, pmid): return self._get(self.pubmed, pmid, "pubmed")
    def epmc_by_pmid(self, pmid): return self._get(self.epmc_pmid, pmid, "europepmc")
    def crossref_by_doi(self, doi): return self._get(self.crossref, doi, "crossref")
    def doiorg_by_doi(self, doi): return self._get(self.doiorg, doi, "doi.org")
    def epmc_by_doi(self, doi): return self._get(self.epmc_doi, doi, "europepmc")


# ---------------- 规范化（#9）----------------
@pytest.mark.unit
def test_normalize_pmid_variants():
    for v in (PMID, f"PMID:{PMID}", f"PMID {PMID}", f"pmid:{PMID}",
              f"https://pubmed.ncbi.nlm.nih.gov/{PMID}/", f"{PMID}."):
        assert normalize_pmid(v) == PMID
    assert normalize_pmid("not-a-pmid") is None


@pytest.mark.unit
def test_normalize_doi_case_and_url():
    upper = DOI.upper()
    for v in (DOI, f"doi:{DOI}", f"https://doi.org/{DOI}", f"http://dx.doi.org/{DOI}", upper):
        assert normalize_doi(v) == DOI            # 统一小写规范化
    assert normalize_doi("10.5") is None


# ---------------- 提取去重（#10）----------------
@pytest.mark.unit
def test_extract_dedup():
    q = f"请检索 PMID {PMID} 与 PMID {PMID} 以及 DOI {DOI}，再看 doi:{DOI}"
    got = extract_ids(q)
    ids = [(g["id_type"], g["normalized_id"]) for g in got]
    assert ("pmid", PMID) in ids and ("doi", DOI) in ids
    assert len(ids) == len(set(ids)) == 2          # 去重后恰两个


# ---------------- PMID 判定（#1,#2,#3,#4）----------------
@pytest.mark.unit
def test_valid_pmid_verified():
    meta = {"pmid": PMID, "doi": None, "title": "CAR-T", "journal": "MED", "year": "2026"}
    s = FakeSources(pubmed={PMID: hit("pubmed", meta)}, epmc_pmid={PMID: hit("europepmc", meta)})
    r = resolve_one("pmid", PMID, PMID, s)
    assert r.resolution_status == "verified" and r.canonical_pmid == PMID


@pytest.mark.unit
def test_nonexistent_pmid_not_found():
    s = FakeSources(pubmed={PMID: zero("pubmed")}, epmc_pmid={PMID: zero("europepmc")})
    r = resolve_one("pmid", PMID, PMID, s)
    assert r.resolution_status == "not_found"


@pytest.mark.unit
def test_pubmed_error_is_manual_needed_not_not_found():
    s = FakeSources(pubmed={PMID: err("pubmed")}, epmc_pmid={PMID: zero("europepmc")})
    r = resolve_one("pmid", PMID, PMID, s)
    assert r.resolution_status == "manual_needed"    # 网络错误 != not_found


@pytest.mark.unit
def test_pubmed_epmc_conflict_is_mismatch():
    s = FakeSources(pubmed={PMID: hit("pubmed", {"pmid": PMID, "year": "2026"})},
                    epmc_pmid={PMID: hit("europepmc", {"pmid": PMID, "year": "1999"})})
    r = resolve_one("pmid", PMID, PMID, s)
    assert r.resolution_status == "mismatch"


@pytest.mark.unit
def test_pmid_single_source_zero_not_escalated():
    """PubMed 命中、EPMC 单一 zero_hits → 不降级为 not_found（仍 verified）。"""
    s = FakeSources(pubmed={PMID: hit("pubmed", {"pmid": PMID, "year": "2026"})},
                    epmc_pmid={PMID: zero("europepmc")})
    r = resolve_one("pmid", PMID, PMID, s)
    assert r.resolution_status == "verified"


# ---------------- DOI 判定（#5,#6,#7,#8）----------------
@pytest.mark.unit
def test_valid_doi_verified():
    s = FakeSources(crossref={DOI: hit("crossref", {"doi": DOI, "title": "T", "year": "2024"})},
                    doiorg={DOI: hit("doi.org", {"doi": DOI})})
    r = resolve_one("doi", DOI, DOI, s)
    assert r.resolution_status == "verified" and r.canonical_doi == DOI


@pytest.mark.unit
def test_nonexistent_doi_not_found_needs_both_primary():
    s = FakeSources(crossref={DOI: zero("crossref")}, doiorg={DOI: zero("doi.org")})
    r = resolve_one("doi", DOI, DOI, s)
    assert r.resolution_status == "not_found"


@pytest.mark.unit
def test_crossref_error_is_manual_needed():
    s = FakeSources(crossref={DOI: err("crossref")}, doiorg={DOI: hit("doi.org", {"doi": DOI})})
    r = resolve_one("doi", DOI, DOI, s)
    assert r.resolution_status == "manual_needed"


@pytest.mark.unit
def test_epmc_empty_but_crossref_hit_is_verified():
    """只有 Europe PMC 无命中 → 不得判 not_found；Crossref 命中 → verified。"""
    s = FakeSources(crossref={DOI: hit("crossref", {"doi": DOI, "year": "2024"})},
                    doiorg={DOI: hit("doi.org", {"doi": DOI})},
                    epmc_doi={DOI: zero("europepmc")})
    r = resolve_one("doi", DOI, DOI, s)
    assert r.resolution_status == "verified"
    assert r.retrieval_status_by_source.get("europepmc") == "zero_hits"


# ---------------- 批量 / 卡 / 隔离（#11,#12,#13,#14,#20,#22）----------------
@pytest.mark.unit
def test_multi_id_each_terminal_and_isolated():
    other_pmid = "12345678"
    s = FakeSources(
        pubmed={PMID: hit("pubmed", {"pmid": PMID, "year": "2026"}),
                other_pmid: err("pubmed")},                       # 一个错误
        epmc_pmid={PMID: hit("europepmc", {"pmid": PMID, "year": "2026"}),
                   other_pmid: zero("europepmc")},
        crossref={DOI: zero("crossref")}, doiorg={DOI: zero("doi.org")})
    q = f"PMID {PMID}, PMID {other_pmid}, DOI {DOI}"
    batch = resolve_exact_ids(q, sources=s)
    assert isinstance(batch, ExactIdBatchResult) and batch.all_terminal is True   # #20
    by = {r["normalized_id"]: r["resolution_status"] for r in batch.ids}
    assert by[PMID] == "verified"                                 # 未被其它 ID 的错误污染 #12
    assert by[other_pmid] == "manual_needed"
    assert by[DOI] == "not_found"


@pytest.mark.unit
def test_evidence_card_only_for_verified():
    s = FakeSources(pubmed={PMID: hit("pubmed", {"pmid": PMID, "title": "CAR-T", "year": "2026"})},
                    epmc_pmid={PMID: hit("europepmc", {"pmid": PMID, "year": "2026"})},
                    crossref={DOI: zero("crossref")}, doiorg={DOI: zero("doi.org")})
    batch = resolve_exact_ids(f"PMID {PMID} DOI {DOI}", sources=s)
    assert len(batch.evidence_cards) == 1                         # #13 仅 verified
    assert batch.evidence_cards[0].get("pmid") == PMID
    assert DOI not in str(batch.evidence_cards)                   # not_found 不构卡


@pytest.mark.unit
def test_fabricated_id_in_content_never_becomes_card():
    """#14：来源对该 PMID 返回 zero → not_found → 不构卡；ID 只认结构化来源，不认 content。"""
    fake = "99999999"
    s = FakeSources(pubmed={fake: zero("pubmed")}, epmc_pmid={fake: zero("europepmc")})
    batch = resolve_exact_ids(f"结论里写着 PMID {fake}", sources=s)
    assert batch.evidence_cards == []
    assert batch.ids[0]["resolution_status"] == "not_found"


@pytest.mark.unit
def test_one_query_per_source_per_id():
    """#18：同一来源同一 ID 默认只查一次。"""
    s = FakeSources(pubmed={PMID: hit("pubmed", {"pmid": PMID})},
                    epmc_pmid={PMID: hit("europepmc", {"pmid": PMID})})
    resolve_one("pmid", PMID, PMID, s)
    assert s.calls[("pubmed", PMID)] == 1 and s.calls[("europepmc", PMID)] == 1


@pytest.mark.unit
def test_controlled_retry_on_429_only_once():
    """#19：429 → 程序控制的一次退避重试（非 LLM，不改查询）。"""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Retryable("rate_limited_429")
        return "ok"

    val, etype = _with_retry(flaky, retries=1)
    assert val == "ok" and etype is None and calls["n"] == 2

    def always():
        calls["n"] += 1
        raise _Retryable("rate_limited_429")
    calls["n"] = 0
    val2, etype2 = _with_retry(always, retries=1)
    assert val2 is None and etype2 == "rate_limited_429" and calls["n"] == 2   # 只重试一次


@pytest.mark.unit
def test_two_non_a1_fixtures_prove_no_hardcoding():
    """#22：用非 A1 的 PMID/DOI 证明通用（不是对具体 ID 写死）。"""
    p2, d2 = "30000001", "10.1000/xyz.demo.0001"
    s = FakeSources(pubmed={p2: hit("pubmed", {"pmid": p2, "title": "Demo", "year": "2020"})},
                    epmc_pmid={p2: hit("europepmc", {"pmid": p2, "year": "2020"})},
                    crossref={d2: zero("crossref")}, doiorg={d2: zero("doi.org")})
    batch = resolve_exact_ids(f"PMID {p2} 和 DOI {d2}", sources=s)
    by = {r["normalized_id"]: r["resolution_status"] for r in batch.ids}
    assert by[p2] == "verified" and by[d2] == "not_found"
    assert len(batch.evidence_cards) == 1 and batch.evidence_cards[0]["pmid"] == p2


@pytest.mark.unit
def test_batch_has_schema_sha_and_terminal():
    s = FakeSources(pubmed={PMID: hit("pubmed", {"pmid": PMID, "year": "2026"})},
                    epmc_pmid={PMID: hit("europepmc", {"pmid": PMID, "year": "2026"})})
    batch = resolve_exact_ids(f"PMID {PMID}", sources=s)
    assert batch.schema_version == "exactid-v1" and batch.sha256
    assert batch.ids[0]["schema_version"] == "exactid-v1" and batch.ids[0]["sha256"]
    assert batch.all_terminal and batch.completion_reason == "all_ids_terminal"
