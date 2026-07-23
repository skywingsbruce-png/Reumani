"""确定性多来源 Exact-ID 解析（Addendum 2 实现）。

铁律（见 SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_2.md）：
- 提取 / 规范化 / 去重 / 来源选择 / 精确查询 / 交叉核验 / 状态裁决 / 构卡 / 完成判断，
  **全部由程序完成，零 LLM**；
- 两层状态：来源级 retrieval_status（exact_hit/zero_hits/source_error/not_queried）与
  综合级 resolution_status（verified/not_found/mismatch/manual_needed）；
- 单一来源 zero_hits **不得**升级为全局 not_found；
- PMID/DOI 只来自结构化来源响应，不从自然语言猜；EvidenceCard 只为 verified 构建；
- 网络错误**不得**判 zero_hits；仅网络错误允许程序控制的一次退避重试（非 LLM、不改查询）。

来源客户端可注入（tests 传 fake），默认 HttpSources 只访问免费 API（PubMed/EPMC/Crossref/
doi.org），**绝不**调用付费 LLM。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

import ids as _ids
from tool_envelope import compute_hash

SCHEMA_VERSION = "exactid-v1"

RetrievalStatus = Literal["exact_hit", "zero_hits", "source_error", "not_queried"]
ResolutionStatus = Literal["verified", "not_found", "mismatch", "manual_needed"]
IdType = Literal["pmid", "doi"]


# ============================ 规范化与提取 ============================
def normalize_pmid(raw) -> Optional[str]:
    """`PMID:41657283` / `PMID 41657283` / URL / 裸数字 → `41657283`；非法 → None。"""
    s = str(raw or "").strip()
    low = s.lower()
    for pre in ("pmid:", "pmid ", "pmid"):
        if low.startswith(pre):
            s = s[len(pre):].strip()
            break
    m = _ids._PMID_URL.search(s)
    if m:
        s = m.group(1)
    s = s.strip().strip(".,;:)]}")
    return s if _ids.valid_pmid(s) else None


def normalize_doi(raw) -> Optional[str]:
    """`doi:10..` / `https://doi.org/10..` / 裸 DOI → 小写规范化 DOI；非法 → None。
    DOI 大小写不敏感，规范化为小写。"""
    s = str(raw or "").strip()
    low = s.lower()
    for pre in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                "http://dx.doi.org/", "doi:", "doi "):
        if low.startswith(pre):
            s = s[len(pre):].strip()
            break
    s = _ids._strip_doi_tail(s.strip())
    s = s.lower()
    return s if _ids.valid_doi(s) else None


def extract_ids(query_or_ids) -> list[dict]:
    """从字符串问题或 ID 列表中提取并规范化、去重。返回 [{id_type, normalized_id, original_input}]。"""
    raw_items: list[str] = []
    if isinstance(query_or_ids, (list, tuple)):
        raw_items = [str(x) for x in query_or_ids]
    else:
        text = str(query_or_ids or "")
        # 结构化提取（PMID 需 tag/URL；DOI 支持裸写/doi:/URL）
        raw_items += [f"PMID:{p}" for p in _ids.extract_pmids(text)]
        raw_items += _ids.extract_dois(text)
        # 补充：逗号/空白 token 里的裸 ID（供 "41657283" 这类直接输入）
        raw_items += [t for t in __import__("re").split(r"[\s,;、，]+", text) if t]

    seen, out = set(), []
    for item in raw_items:
        norm = normalize_doi(item) if ("/" in str(item) or str(item).lower().lstrip().startswith("doi")) \
            else (normalize_pmid(item) or normalize_doi(item))
        id_type = None
        if norm and _ids.valid_doi(norm):
            id_type = "doi"
        elif norm and _ids.valid_pmid(norm):
            id_type = "pmid"
        if not id_type:
            continue
        key = (id_type, norm)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id_type": id_type, "normalized_id": norm, "original_input": str(item)})
    return out


# ============================ 结构化契约 ============================
class ExactIdResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION
    original_input: str
    normalized_id: str
    id_type: IdType
    source_results: list[dict] = Field(default_factory=list)
    retrieval_status_by_source: dict[str, str] = Field(default_factory=dict)
    resolution_status: ResolutionStatus
    canonical_title: Optional[str] = None
    canonical_doi: Optional[str] = None
    canonical_pmid: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[str] = None
    provenance: dict = Field(default_factory=dict)
    content_level: str = "metadata_only"
    warnings: list[str] = Field(default_factory=list)
    error_type: Optional[str] = None
    queried_at: Optional[str] = None
    sha256: Optional[str] = None

    def is_terminal(self) -> bool:
        return self.resolution_status in ("verified", "not_found", "mismatch", "manual_needed")


class ExactIdBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION
    original_query: str = ""
    ids: list[dict] = Field(default_factory=list)          # ExactIdResolution.model_dump()
    all_terminal: bool = False
    verified_count: int = 0
    not_found_count: int = 0
    mismatch_count: int = 0
    manual_needed_count: int = 0
    evidence_cards: list[dict] = Field(default_factory=list)
    completion_reason: str = ""
    queried_at: Optional[str] = None
    sha256: Optional[str] = None


# ============================ 来源客户端 ============================
def _with_retry(fn, *, retries=1):
    """仅对网络错误做程序控制的一次退避重试；不改查询、非 LLM 调用。
    返回 (result, error_type)。error_type=None 表示成功。"""
    import time
    last = None
    for attempt in range(retries + 1):
        try:
            return fn(), None
        except _Retryable as e:          # 429 / 超时 / 连接错误 → 允许一次重试
            last = e
            if attempt < retries:
                time.sleep(min(0.2 * (attempt + 1), 1.0))
                continue
            return None, e.error_type
        except Exception as e:           # 解析等其它异常 → 不重试
            return None, f"parse_error:{type(e).__name__}"
    return None, getattr(last, "error_type", "source_error")


class _Retryable(Exception):
    def __init__(self, error_type):
        super().__init__(error_type)
        self.error_type = error_type


def _src(source, status, meta=None, error_type=None, http_status=None):
    return {"source": source, "retrieval_status": status, "metadata": meta or {},
            "error_type": error_type, "http_status": http_status}


class HttpSources:
    """默认真实来源：只访问免费 API，绝不调用付费 LLM。"""

    def __init__(self, timeout=20):
        import requests
        self._requests = requests
        self.timeout = timeout

    def _get(self, url, params=None):
        def _do():
            r = self._requests.get(url, params=params, timeout=self.timeout)
            if r.status_code == 429:
                raise _Retryable("rate_limited_429")
            if r.status_code >= 500:
                raise _Retryable(f"http_{r.status_code}")
            return r
        try:
            r, err = _with_retry(_do)
        except Exception as e:                 # 连接层异常
            return None, "network_error"
        if err:
            return None, err
        return r, None

    def pubmed_by_pmid(self, pmid):
        r, err = self._get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                           {"db": "pubmed", "id": pmid, "retmode": "json"})
        if err:
            return _src("pubmed", "source_error", error_type=err)
        try:
            res = r.json().get("result", {})
            rec = res.get(str(pmid))
            if not rec or rec.get("error") or "uids" in res and str(pmid) not in res.get("uids", []):
                return _src("pubmed", "zero_hits", http_status=r.status_code)
            doi = None
            for a in rec.get("articleids", []):
                if a.get("idtype") == "doi":
                    doi = a.get("value")
            meta = {"pmid": str(pmid), "doi": doi, "title": rec.get("title"),
                    "journal": rec.get("fulljournalname") or rec.get("source"),
                    "year": (rec.get("pubdate", "") or "")[:4]}
            return _src("pubmed", "exact_hit", meta, http_status=r.status_code)
        except Exception as e:
            return _src("pubmed", "source_error", error_type=f"parse_error:{type(e).__name__}")

    def epmc_by_pmid(self, pmid):
        return self._epmc(f"EXT_ID:{pmid} AND SRC:MED", "europepmc")

    def epmc_by_doi(self, doi):
        return self._epmc(f'DOI:"{doi}"', "europepmc")

    def _epmc(self, query, source):
        r, err = self._get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                           {"query": query, "format": "json", "resultType": "core", "pageSize": 1})
        if err:
            return _src(source, "source_error", error_type=err)
        try:
            res = r.json().get("resultList", {}).get("result", [])
            if not res:
                return _src(source, "zero_hits", http_status=r.status_code)
            it = res[0]
            meta = {"pmid": it.get("pmid"), "doi": it.get("doi"), "title": it.get("title"),
                    "journal": it.get("journalTitle") or it.get("source"),
                    "year": (it.get("firstPublicationDate", "") or "")[:4]}
            return _src(source, "exact_hit", meta, http_status=r.status_code)
        except Exception as e:
            return _src(source, "source_error", error_type=f"parse_error:{type(e).__name__}")

    def crossref_by_doi(self, doi):
        r, err = self._get(f"https://api.crossref.org/works/{doi}")
        if err:
            return _src("crossref", "source_error", error_type=err)
        if r.status_code == 404:
            return _src("crossref", "zero_hits", http_status=404)
        try:
            msg = r.json().get("message", {})
            title = (msg.get("title") or [None])[0]
            year = None
            parts = (msg.get("issued", {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                year = str(parts[0])
            meta = {"pmid": None, "doi": msg.get("DOI"), "title": title,
                    "journal": (msg.get("container-title") or [None])[0], "year": year}
            return _src("crossref", "exact_hit", meta, http_status=r.status_code)
        except Exception as e:
            return _src("crossref", "source_error", error_type=f"parse_error:{type(e).__name__}")

    def doiorg_by_doi(self, doi):
        r, err = self._get(f"https://doi.org/api/handles/{doi}")
        if err:
            return _src("doi.org", "source_error", error_type=err)
        try:
            code = r.json().get("responseCode")
            if code == 1:
                return _src("doi.org", "exact_hit", {"doi": doi}, http_status=r.status_code)
            return _src("doi.org", "zero_hits", http_status=r.status_code)   # 100 = handle not found
        except Exception as e:
            return _src("doi.org", "source_error", error_type=f"parse_error:{type(e).__name__}")


# ============================ 裁决（状态机核心）============================
def _conflict(a: dict, b: dict) -> bool:
    """结构化元数据冲突：年份不同，或 DOI 规范化后不同。标题过噪不用于冲突判定。"""
    ay, by = a.get("year"), b.get("year")
    if ay and by and str(ay) != str(by):
        return True
    ad, bd = a.get("doi"), b.get("doi")
    if ad and bd and normalize_doi(ad) != normalize_doi(bd):
        return True
    return False


def _resolve_pmid(norm, sources):
    primary = sources.pubmed_by_pmid(norm)
    results = [primary]
    warnings, canonical, error_type = [], {}, None
    if primary["retrieval_status"] == "source_error":
        aux = _src("europepmc", "not_queried")
        return "manual_needed", {}, results + [aux], warnings + ["PubMed 不可用，未确认存在性"], \
            primary.get("error_type")
    aux = sources.epmc_by_pmid(norm)
    results.append(aux)
    if primary["retrieval_status"] == "exact_hit":
        canonical = dict(primary["metadata"])
        if aux["retrieval_status"] == "exact_hit":
            resolution = "mismatch" if _conflict(primary["metadata"], aux["metadata"]) else "verified"
            if resolution == "mismatch":
                warnings.append("PubMed 与 Europe PMC 元数据冲突")
        elif aux["retrieval_status"] == "zero_hits":
            resolution = "verified"
            warnings.append("Europe PMC 未交叉命中（辅助来源，不降级）")
        else:
            resolution = "verified"
            warnings.append("Europe PMC 交叉核验不可用（辅助来源）")
    elif primary["retrieval_status"] == "zero_hits":
        if aux["retrieval_status"] == "exact_hit":
            resolution, canonical = "mismatch", dict(aux["metadata"])
            warnings.append("PubMed 无记录但 Europe PMC 命中（来源冲突）")
        elif aux["retrieval_status"] == "zero_hits":
            resolution = "not_found"
        else:
            resolution = "manual_needed"
            warnings.append("PubMed 无记录且 Europe PMC 不可用，无法确认")
    else:
        resolution = "manual_needed"
    return resolution, canonical, results, warnings, error_type


def _resolve_doi(norm, sources):
    cr = sources.crossref_by_doi(norm)
    dr = sources.doiorg_by_doi(norm)
    aux = sources.epmc_by_doi(norm) if hasattr(sources, "epmc_by_doi") else _src("europepmc", "not_queried")
    results = [cr, dr, aux]
    warnings, canonical, error_type = [], {}, None

    def consistent(meta):
        d = meta.get("doi")
        return (not d) or (normalize_doi(d) == norm)

    if cr["retrieval_status"] == "exact_hit":
        canonical = dict(cr["metadata"])
        if not consistent(cr["metadata"]):
            return "mismatch", canonical, results, \
                warnings + ["Crossref 返回的 DOI 与规范化输入不一致"], error_type
        if dr["retrieval_status"] == "exact_hit" and _conflict(cr["metadata"], dr["metadata"]):
            resolution = "mismatch"
            warnings.append("Crossref 与 doi.org 元数据冲突")
        elif dr["retrieval_status"] == "source_error":
            resolution = "verified"
            warnings.append("doi.org 解析不可用；Crossref 已精确解析")
        else:
            resolution = "verified"
    elif cr["retrieval_status"] == "zero_hits":
        if dr["retrieval_status"] == "exact_hit":
            resolution, canonical = "manual_needed", dict(dr["metadata"])
            warnings.append("doi.org 命中但 Crossref 无记录，待人工核验元数据")
        elif dr["retrieval_status"] == "zero_hits":
            resolution = "not_found"          # 两个主来源均明确 not found
        elif dr["retrieval_status"] == "source_error":
            resolution = "manual_needed"
            warnings.append("Crossref 无记录且 doi.org 不可用，网络错误不得判 not_found")
        else:
            resolution = "manual_needed"
    else:  # crossref source_error
        error_type = cr.get("error_type")
        if dr["retrieval_status"] == "exact_hit":
            resolution, canonical = "manual_needed", dict(dr["metadata"])
            warnings.append("Crossref 不可用，doi.org 命中，待人工核验元数据")
        else:
            resolution = "manual_needed"
            warnings.append("Crossref 不可用，无法确认（网络错误不得判 zero_hits/not_found）")
    return resolution, canonical, results, warnings, error_type


def resolve_one(id_type, normalized, original, sources) -> ExactIdResolution:
    if id_type == "pmid":
        resolution, canonical, results, warnings, err = _resolve_pmid(normalized, sources)
    else:
        resolution, canonical, results, warnings, err = _resolve_doi(normalized, sources)
    retrieval_by_source = {r["source"]: r["retrieval_status"] for r in results}
    prov = {"tool_name": "resolve_exact_ids", "id_type": id_type,
            "normalized_id": normalized, "original_input": original,
            "sources": [{"source": r["source"], "retrieval_status": r["retrieval_status"]}
                        for r in results]}
    core = {"id_type": id_type, "normalized_id": normalized,
            "resolution_status": resolution,
            "canonical_pmid": canonical.get("pmid"), "canonical_doi": canonical.get("doi"),
            "canonical_title": canonical.get("title"), "year": canonical.get("year"),
            "journal": canonical.get("journal"),
            "retrieval_status_by_source": retrieval_by_source}
    res = ExactIdResolution(
        original_input=original, normalized_id=normalized, id_type=id_type,
        source_results=results, retrieval_status_by_source=retrieval_by_source,
        resolution_status=resolution,
        canonical_title=canonical.get("title"),
        canonical_doi=(normalize_doi(canonical["doi"]) if canonical.get("doi") else
                       (normalized if id_type == "doi" and resolution == "verified" else None)),
        canonical_pmid=(canonical.get("pmid") or
                        (normalized if id_type == "pmid" and resolution == "verified" else None)),
        journal=canonical.get("journal"), year=canonical.get("year"),
        provenance=prov, content_level="metadata_only",
        warnings=warnings, error_type=err,
        queried_at=datetime.now().isoformat(timespec="seconds"),
        sha256=compute_hash(core))
    return res


# ============================ EvidenceCard（仅 verified）============================
def _card_for(res: ExactIdResolution):
    """只为 verified 构卡；PMID/DOI 仅取自结构化来源响应。"""
    if res.resolution_status != "verified":
        return None
    import evidence_build as EB
    paper = {"pmid": res.canonical_pmid, "doi": res.canonical_doi,
             "title": res.canonical_title or "", "journal": res.journal or "",
             "year": res.year or "", "link": (f"https://pubmed.ncbi.nlm.nih.gov/{res.canonical_pmid}/"
                                               if res.canonical_pmid else
                                               (f"https://doi.org/{res.canonical_doi}" if res.canonical_doi else "")),
             "content_level": "metadata_only", "supporting_excerpt": "",
             "source_database": "PubMed/Crossref exact-id"}
    return EB.abstract_card_from_paper(paper, tool_name="resolve_exact_ids",
                                       query=res.normalized_id)


# ============================ 批量 Action ============================
def resolve_exact_ids(query_or_ids, *, sources=None) -> ExactIdBatchResult:
    """确定性 Exact-ID 解析 Action。零 LLM。返回 ExactIdBatchResult。"""
    src = sources if sources is not None else HttpSources()
    extracted = extract_ids(query_or_ids)
    resolutions, cards = [], []
    for e in extracted:
        res = resolve_one(e["id_type"], e["normalized_id"], e["original_input"], src)
        resolutions.append(res)
        card = _card_for(res)
        if card is not None:
            cards.append(card)

    counts = {"verified": 0, "not_found": 0, "mismatch": 0, "manual_needed": 0}
    for r in resolutions:
        counts[r.resolution_status] += 1
    all_terminal = all(r.is_terminal() for r in resolutions)
    id_dumps = [r.model_dump() for r in resolutions]
    core = {"ids": [{"id": r.normalized_id, "status": r.resolution_status,
                     "sha": r.sha256} for r in resolutions], "counts": counts}
    batch = ExactIdBatchResult(
        original_query=str(query_or_ids)[:500],
        ids=id_dumps, all_terminal=all_terminal,
        verified_count=counts["verified"], not_found_count=counts["not_found"],
        mismatch_count=counts["mismatch"], manual_needed_count=counts["manual_needed"],
        evidence_cards=[c.model_dump() for c in cards],
        completion_reason=("all_ids_terminal" if all_terminal else "incomplete"),
        queried_at=datetime.now().isoformat(timespec="seconds"),
        sha256=compute_hash(core))
    return batch
