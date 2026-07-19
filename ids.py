"""标识符校验/抽取的【唯一权威】——避免 PMID/DOI 正则在多处各写一套导致规则漂移。
verifier / lit_cleaning / shadow / evidence_build 都从这里取，不再各自 re.compile。"""

import re

_PMID = re.compile(r"^\d{1,9}$")                       # PMID 纯数字（旧文献可短）
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_PMID_URL = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})")
_PMID_TAG = re.compile(r"PMID[:\s]+(\d{1,9})")
_DOI_URL = re.compile(r"doi\.org/(10\.[^\s|)\]]+)")


def valid_pmid(p):
    return bool(p and _PMID.match(str(p)))


def valid_doi(d):
    return bool(d and _DOI.match(str(d)))


def extract_pmids(text):
    t = text or ""
    return list(dict.fromkeys(_PMID_URL.findall(t) + _PMID_TAG.findall(t)))


def extract_dois(text):
    return list(dict.fromkeys(_DOI_URL.findall(text or "")))


_CIT = re.compile(r"\bPMID[:\s]*\d+|\bPMC\d+|10\.\d{4,}/\S+|pubmed\.ncbi\.nlm\.nih\.gov/\d+|\bGSE\d+", re.I)


def has_citation(text):
    """文本是否含任何可核实引用（PMID/PMC/DOI/PubMed链接/GSE）。"""
    return bool(_CIT.search(text or ""))
