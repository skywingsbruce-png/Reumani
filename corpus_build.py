"""
通用文献库下载器：把任意主题的近10年文献(元数据+摘要)拉成本地库。
存到 data_lake/corpus/<name>.jsonl。供离线检索、趋势分析、批量证据卡片。
用法：python corpus_build.py <name>        （name ∈ CORPORA）
      python corpus_build.py all           （全部）
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "data_lake" / "corpus"
OUT_DIR.mkdir(parents=True, exist_ok=True)
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

DATE = "FIRST_PDATE:[2016-01-01 TO 2026-12-31] AND HAS_ABSTRACT:Y"

CORPORA = {
    # CIN 机制（染色体不稳定 与 免疫/纤维化/衰老/cGAS 相关）—— 你模型的核心
    "CIN": (f'((TITLE_ABS:"chromosomal instability" OR TITLE_ABS:"micronuclei" '
            f'OR TITLE_ABS:"genomic instability" OR TITLE_ABS:"aneuploidy") '
            f'AND (TITLE_ABS:cGAS OR TITLE_ABS:STING OR TITLE_ABS:senescence '
            f'OR TITLE_ABS:inflammation OR TITLE_ABS:immune OR TITLE_ABS:fibrosis '
            f'OR TITLE_ABS:interferon)) AND {DATE}'),
    "SLE": (f'(TITLE_ABS:"systemic lupus erythematosus" OR TITLE_ABS:"lupus nephritis") AND {DATE}'),
    "RA": (f'(TITLE_ABS:"rheumatoid arthritis") AND {DATE}'),
}


def _clean(t):
    return re.sub(r"<[^>]+>", " ", t or "").strip()


def build(name, query):
    out_path = OUT_DIR / f"{name}.jsonl"
    cursor, total, seen = "*", 0, set()
    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            r = requests.get(EPMC, params={
                "query": query, "format": "json", "resultType": "core",
                "pageSize": 1000, "cursorMark": cursor}, timeout=90)
            r.raise_for_status()
            js = r.json()
            results = js.get("resultList", {}).get("result", [])
            if not results:
                break
            for it in results:
                pid = str(it.get("pmid") or it.get("doi") or it.get("id") or "")
                if pid in seen:
                    continue
                seen.add(pid)
                f.write(json.dumps({
                    "pmid": it.get("pmid"), "doi": it.get("doi"),
                    "title": it.get("title", ""), "authors": it.get("authorString", ""),
                    "journal": it.get("journalTitle") or it.get("source", ""),
                    "year": (it.get("firstPublicationDate", "") or "")[:4],
                    "pub_type": it.get("pubType", ""),
                    "abstract": _clean(it.get("abstractText", "")),
                }, ensure_ascii=False) + "\n")
                total += 1
            nxt = js.get("nextCursorMark")
            if total % 3000 < 1000:
                print(f"  {name}: {total} 篇...", flush=True)
            if not nxt or nxt == cursor:
                break
            cursor = nxt
            time.sleep(0.25)
    meta = {"name": name, "built_at": datetime.now().isoformat(timespec="seconds"),
            "count": total, "source": "Europe PMC", "query": query[:200]}
    (OUT_DIR / f"{name}_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    mb = out_path.stat().st_size / 1024 / 1024
    return f"{name} 文献库：{total} 篇，{mb:.1f} MB → {out_path}"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    names = list(CORPORA) if which == "all" else [which]
    for n in names:
        if n in CORPORA:
            print(build(n, CORPORA[n]))
        else:
            print(f"未知库：{n}（可选 {list(CORPORA)}）")
