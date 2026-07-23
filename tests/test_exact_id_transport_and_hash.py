"""A.7.1.2：传输层异常分类 + 有界重试 + EvidenceCard 内容 hash。全部零付费、零外网。"""
import socket
import sys
from pathlib import Path

import pytest
import requests as _rq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pilot.exact_id_resolver import (ERR_CLIENT, ERR_CONNECTION, ERR_DNS, ERR_NOT_FOUND,
                                     ERR_PARSE, ERR_RATE_LIMITED, ERR_SERVER, ERR_TIMEOUT,
                                     MAX_ATTEMPTS, HttpSources, card_hash_payload,
                                     resolve_exact_ids, resolve_one)

PMID = "41657283"
DOI = "10.1080/03009742.2024.2302553"

PUBMED_OK = {"result": {PMID: {"title": "T", "fulljournalname": "J", "pubdate": "2026",
                               "articleids": [{"idtype": "doi", "value": "10.1/x"}]},
                        "uids": [PMID]}}


class FakeResp:
    def __init__(self, status=200, payload=None, bad_json=False, headers=None):
        self.status_code, self._payload = status, payload if payload is not None else {}
        self._bad, self.headers = bad_json, headers or {}

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._payload


def transport(behaviour):
    """behaviour(n) -> FakeResp 或抛异常。返回 (hs, state)。"""
    state = {"n": 0, "calls": []}

    class _T:
        def get(self, url=None, params=None, timeout=None, **k):
            state["n"] += 1
            state["calls"].append({"url": url, "params": params})
            return behaviour(state["n"])

    hs = HttpSources()
    hs._requests = _T()
    return hs, state


def raise_(exc):
    def _b(n):
        raise exc
    return _b


# ---------------- 1-7 分类 ----------------
@pytest.mark.unit
@pytest.mark.parametrize("exc,expect", [
    (_rq.exceptions.Timeout("t"), ERR_TIMEOUT),
    (_rq.exceptions.ReadTimeout("rt"), ERR_TIMEOUT),
    (_rq.exceptions.ConnectionError("connection reset by peer"), ERR_CONNECTION),
])
def test_exception_classification(exc, expect):
    hs, st = transport(raise_(exc))
    r = hs.pubmed_by_pmid(PMID)
    assert r["retrieval_status"] == "source_error"
    assert r["error_type"] == expect
    assert r["error_type"] != ERR_PARSE          # 绝不误标为 parse_error


@pytest.mark.unit
def test_dns_error_classification():
    exc = _rq.exceptions.ConnectionError("Failed to resolve: [Errno -2] Name or service not known")
    hs, st = transport(raise_(exc))
    r = hs.pubmed_by_pmid(PMID)
    assert r["error_type"] == ERR_DNS and r["retrieval_status"] == "source_error"


@pytest.mark.unit
def test_dns_error_via_gaierror_chain():
    inner = socket.gaierror(-2, "Name or service not known")
    exc = _rq.exceptions.ConnectionError("wrapped")
    exc.__cause__ = inner
    hs, _ = transport(raise_(exc))
    assert hs.pubmed_by_pmid(PMID)["error_type"] == ERR_DNS


@pytest.mark.unit
@pytest.mark.parametrize("status,expect", [
    (429, ERR_RATE_LIMITED), (500, ERR_SERVER), (502, ERR_SERVER),
    (503, ERR_SERVER), (504, ERR_SERVER), (403, ERR_CLIENT), (400, ERR_CLIENT)])
def test_http_status_classification(status, expect):
    hs, _ = transport(lambda n: FakeResp(status))
    r = hs.pubmed_by_pmid(PMID)
    assert r["error_type"] == expect and r["retrieval_status"] == "source_error"


@pytest.mark.unit
def test_parse_error_classification():
    hs, st = transport(lambda n: FakeResp(200, bad_json=True))
    r = hs.pubmed_by_pmid(PMID)
    assert r["error_type"] == ERR_PARSE and r["retrieval_status"] == "source_error"
    assert st["n"] == 1                          # #15 parse_error 不重试


@pytest.mark.unit
def test_404_is_zero_hits_not_error():
    """#7/#14：权威来源 404 → zero_hits（协议级明确无记录），且不重试。"""
    hs, st = transport(lambda n: FakeResp(404))
    r = hs.crossref_by_doi(DOI)
    assert r["retrieval_status"] == "zero_hits" and r["error_type"] is None
    assert st["n"] == 1


# ---------------- 8-13 有界重试 ----------------
def _then_ok(first_exc=None, first_status=None):
    def _b(n):
        if n == 1:
            if first_exc:
                raise first_exc
            return FakeResp(first_status)
        return FakeResp(200, PUBMED_OK)
    return _b


@pytest.mark.unit
@pytest.mark.parametrize("kind,first", [
    ("timeout", _rq.exceptions.Timeout("t")),
    ("connection", _rq.exceptions.ConnectionError("connection reset")),
])
def test_retry_succeeds_on_second_attempt_exceptions(kind, first):
    hs, st = transport(_then_ok(first_exc=first))
    r = hs.pubmed_by_pmid(PMID)
    assert r["retrieval_status"] == "exact_hit" and st["n"] == 2
    assert [a["attempt"] for a in r["attempts"]] == [1, 2]


@pytest.mark.unit
@pytest.mark.parametrize("status", [429, 503])
def test_retry_succeeds_on_second_attempt_status(status):
    hs, st = transport(_then_ok(first_status=status))
    r = hs.pubmed_by_pmid(PMID)
    assert r["retrieval_status"] == "exact_hit" and st["n"] == 2


@pytest.mark.unit
def test_both_attempts_fail_gives_manual_needed():
    """#12：两次均失败 → 来源 source_error → 综合 manual_needed。"""
    hs, st = transport(lambda n: FakeResp(503))
    r = resolve_one("pmid", PMID, PMID, hs)
    assert r.resolution_status == "manual_needed"
    assert r.retrieval_status_by_source["pubmed"] == "source_error"


@pytest.mark.unit
def test_at_most_two_requests_per_source():
    """#13：同一来源同一 ID 总请求 ≤ 2。"""
    hs, st = transport(lambda n: FakeResp(503))
    hs.pubmed_by_pmid(PMID)
    assert st["n"] == MAX_ATTEMPTS == 2


@pytest.mark.unit
def test_retry_does_not_change_query():
    """#16：重试使用完全相同的 url/params。"""
    hs, st = transport(_then_ok(first_status=429))
    hs.pubmed_by_pmid(PMID)
    assert len(st["calls"]) == 2
    assert st["calls"][0]["url"] == st["calls"][1]["url"]
    assert st["calls"][0]["params"] == st["calls"][1]["params"]


@pytest.mark.unit
def test_retry_calls_no_llm(monkeypatch):
    """#17：重试路径绝不触发任何 LLM。"""
    hits = {"n": 0}

    def sentinel(*a, **k):
        hits["n"] += 1
        raise AssertionError("LLM must not be called")

    # 类级哨兵：覆盖任何已构造/新构造的 LLM 客户端
    from langchain_openai import ChatOpenAI
    monkeypatch.setattr(ChatOpenAI, "invoke", sentinel)
    try:
        from langchain_anthropic import ChatAnthropic
        monkeypatch.setattr(ChatAnthropic, "invoke", sentinel)
    except Exception:
        pass
    hs, st = transport(_then_ok(first_status=429))
    r = hs.pubmed_by_pmid(PMID)
    assert r["retrieval_status"] == "exact_hit" and st["n"] == 2
    assert hits["n"] == 0


@pytest.mark.unit
def test_retry_after_header_is_bounded():
    """尊重 Retry-After 但受上限约束（不会长时间阻塞）。"""
    import time
    def _b(n):
        return FakeResp(429, headers={"Retry-After": "3600"}) if n == 1 else FakeResp(200, PUBMED_OK)
    hs, st = transport(_b)
    t0 = time.time()
    r = hs.pubmed_by_pmid(PMID)
    assert r["retrieval_status"] == "exact_hit"
    assert time.time() - t0 < 5                  # 被 MAX_BACKOFF_S 截断，未睡 3600s


@pytest.mark.unit
def test_no_network_error_ever_becomes_not_found():
    for beh in (raise_(_rq.exceptions.Timeout("t")),
                raise_(_rq.exceptions.ConnectionError("Name or service not known")),
                lambda n: FakeResp(503), lambda n: FakeResp(429),
                lambda n: FakeResp(200, bad_json=True)):
        hs, _ = transport(beh)
        for idt, nid in (("pmid", PMID), ("doi", DOI)):
            assert resolve_one(idt, nid, nid, hs).resolution_status != "not_found"


# ---------------- 18-25 EvidenceCard hash ----------------
def _verified_sources():
    class S:
        def pubmed_by_pmid(self, p):
            return {"source": "pubmed", "retrieval_status": "exact_hit",
                    "metadata": {"pmid": p, "doi": "10.1/x", "title": "Title A",
                                 "journal": "J", "year": "2026"},
                    "error_type": None, "http_status": 200, "attempts": []}
        def epmc_by_pmid(self, p):
            return {"source": "europepmc", "retrieval_status": "exact_hit",
                    "metadata": {"pmid": p, "doi": "10.1/x", "year": "2026"},
                    "error_type": None, "http_status": 200, "attempts": []}
        def crossref_by_doi(self, d):
            return {"source": "crossref", "retrieval_status": "zero_hits", "metadata": {},
                    "error_type": None, "http_status": 200, "attempts": []}
        def doiorg_by_doi(self, d):
            return {"source": "doi.org", "retrieval_status": "zero_hits", "metadata": {},
                    "error_type": None, "http_status": 200, "attempts": []}
        def epmc_by_doi(self, d):
            return {"source": "europepmc", "retrieval_status": "zero_hits", "metadata": {},
                    "error_type": None, "http_status": 200, "attempts": []}
    return S()


@pytest.mark.unit
def test_card_has_sha256_content_hash():
    b = resolve_exact_ids(f"核验 PMID {PMID} 是否精确命中", sources=_verified_sources())
    assert len(b.evidence_cards) == 1
    prov = b.evidence_cards[0]["provenance"]
    h = prov["content_hash"]
    assert prov["hash_algorithm"] == "sha256"                     # #19
    assert isinstance(h, str) and len(h) == 64                    # #20
    assert h == h.lower() and all(c in "0123456789abcdef" for c in h)


@pytest.mark.unit
def test_hash_stable_across_runs_and_ignores_time_and_runid():
    """#21/#23：相同元数据 → 相同 hash；查询时间/run_id 不影响。"""
    q = f"核验 PMID {PMID} 是否精确命中"
    b1 = resolve_exact_ids(q, sources=_verified_sources())
    b2 = resolve_exact_ids(q, sources=_verified_sources())
    h1 = b1.evidence_cards[0]["provenance"]["content_hash"]
    h2 = b2.evidence_cards[0]["provenance"]["content_hash"]
    assert h1 == h2
    assert b1.queried_at is not None and b1.ids[0]["queried_at"] is not None
    # payload 里不含时间/run_id
    r = type("R", (), {})()
    payload = card_hash_payload(
        __import__("pilot.exact_id_resolver", fromlist=["x"]).ExactIdResolution(
            **{k: v for k, v in b1.ids[0].items() if k in
               {"original_input", "normalized_id", "id_type", "resolution_status",
                "canonical_title", "canonical_doi", "canonical_pmid", "journal", "year"}}),
        "src")
    assert not any(k in payload for k in ("queried_at", "run_id", "sha256", "path"))


@pytest.mark.unit
def test_hash_changes_when_metadata_changes():
    """#22：关键元数据变化 → hash 变化。"""
    s1 = _verified_sources()
    b1 = resolve_exact_ids(f"核验 PMID {PMID} 是否精确命中", sources=s1)

    class S2(type(s1)):
        def pubmed_by_pmid(self, p):
            r = super().pubmed_by_pmid(p)
            r["metadata"] = dict(r["metadata"], title="Title B CHANGED")
            return r
    b2 = resolve_exact_ids(f"核验 PMID {PMID} 是否精确命中", sources=S2())
    assert (b1.evidence_cards[0]["provenance"]["content_hash"]
            != b2.evidence_cards[0]["provenance"]["content_hash"])


@pytest.mark.unit
def test_not_found_builds_no_card_and_no_hash():
    """#24/#25：not_found 不构卡；content 中的伪造 ID 不影响卡或 hash。"""
    class Zero:
        def pubmed_by_pmid(self, p): return {"source": "pubmed", "retrieval_status": "zero_hits",
                                             "metadata": {}, "error_type": None,
                                             "http_status": 200, "attempts": []}
        def epmc_by_pmid(self, p): return {"source": "europepmc", "retrieval_status": "zero_hits",
                                           "metadata": {}, "error_type": None,
                                           "http_status": 200, "attempts": []}
    b = resolve_exact_ids("结论里写着 PMID 99999999，请核验是否精确命中", sources=Zero())
    assert b.evidence_cards == [] and b.ids[0]["resolution_status"] == "not_found"


@pytest.mark.unit
def test_card_hash_payload_differs_from_resolution_sha():
    """不得把 ExactIdResolution.sha256 冒充 EvidenceCard 内容 hash。"""
    b = resolve_exact_ids(f"核验 PMID {PMID} 是否精确命中", sources=_verified_sources())
    assert (b.evidence_cards[0]["provenance"]["content_hash"] != b.ids[0]["sha256"])


@pytest.mark.unit
def test_provenance_keeps_source_and_level():
    b = resolve_exact_ids(f"核验 PMID {PMID} 是否精确命中", sources=_verified_sources())
    prov = b.evidence_cards[0]["provenance"]
    assert prov["content_level"] == "metadata_only"
    assert PMID in str(prov["source"]) and PMID in prov["source_ids"]
