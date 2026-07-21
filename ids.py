"""标识符校验/抽取的【唯一权威】——避免 PMID/DOI 正则在多处各写一套导致规则漂移。
verifier / lit_cleaning / shadow / evidence_build 都从这里取，不再各自 re.compile。"""

import re

_PMID = re.compile(r"^\d{1,9}$")                       # PMID 纯数字（旧文献可短）
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_PMID_URL = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})")
_PMID_TAG = re.compile(r"PMID[:\s]+(\d{1,9})")
_DOI_URL = re.compile(r"doi\.org/(10\.[^\s|)\]]+)")
# 文本中的 DOI（裸写 / doi: 前缀 / URL），大小写不敏感。
# 结尾的标点由 _strip_doi_tail 统一清理，避免维护两套不一致的正则。
# DOI 正文只可能是可打印 ASCII（[!-~]），因此遇到中文/全角字符会自动截断，
# 不会把 "…2302553，分别报告" 整段吞进来。
_DOI_ANY = re.compile(r"(?:doi\s*[:：]\s*|https?://(?:dx\.)?doi\.org/)?"
                      r"(10\.\d{4,9}/[!-~]+)", re.I)
# DOI 结尾常见的粘连标点（含中文全角），逐个剥掉；右括号仅在无配对左括号时剥
_DOI_TAIL = "。，、；：）】》」』.,;:)]}>\"'!?！？"


def valid_pmid(p):
    return bool(p and _PMID.match(str(p)))


def valid_doi(d):
    return bool(d and _DOI.match(str(d)))


def extract_pmids(text):
    t = text or ""
    return list(dict.fromkeys(_PMID_URL.findall(t) + _PMID_TAG.findall(t)))


def _strip_doi_tail(s):
    """剥掉 DOI 末尾粘连的标点。右括号只在没有配对左括号时才剥。"""
    while s:
        c = s[-1]
        if c in ")]}" and s.count({")": "(", "]": "[", "}": "{"}[c]) >= s.count(c):
            break                       # 括号是 DOI 自身的一部分（成对），保留
        if c in _DOI_TAIL:
            s = s[:-1]
            continue
        break
    return s


def extract_dois(text):
    """提取文本中的 DOI：裸写 / `doi:10...` / `https://doi.org/10...`，
    大小写不敏感，清理尾部中英文标点，按首次出现顺序去重，返回规范化 DOI。

    与 `valid_doi()` 共用同一判定权威——凡是提取出来的都必须 `valid_doi` 为真，
    避免出现"能提取但校验不过"或"校验合法却提取不到"的口径分裂。
    """
    out = []
    for raw in _DOI_ANY.findall(text or ""):
        d = _strip_doi_tail(raw.strip())
        if not valid_doi(d):            # 单一权威：普通小数、版本号等在此被排除
            continue
        if d not in out:
            out.append(d)               # 去重且保持首次出现顺序
    return out


_CIT = re.compile(r"\bPMID[:\s]*\d+|\bPMC\d+|10\.\d{4,}/\S+|pubmed\.ncbi\.nlm\.nih\.gov/\d+|\bGSE\d+", re.I)


def has_citation(text):
    """文本是否含任何可核实引用（PMID/PMC/DOI/PubMed链接/GSE）。"""
    return bool(_CIT.search(text or ""))
