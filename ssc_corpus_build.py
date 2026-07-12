"""
SSc 文献语料库：把近 N 年 SSc 文献的元数据+摘要批量拉到本地（Europe PMC，游标翻页）。
存成 data_lake/ssc_corpus/corpus.jsonl（一篇一行），供离线关键词检索、趋势分析、批量证据卡片。
用法：python ssc_corpus_build.py [起始年] [结束年]
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "data_lake" / "ssc_corpus"
OUT_DIR.mkdir(parents=True, exist_ok=True)
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _clean(t):
    import re
    return re.sub(r"<[^>]+>", " ", t or "").strip()


def build(start_year=2016, end_year=2026):
    query = (f'(TITLE_ABS:"systemic sclerosis" OR TITLE_ABS:"scleroderma") '
             f'AND FIRST_PDATE:[{start_year}-01-01 TO {end_year}-12-31] AND HAS_ABSTRACT:Y')
    out_path = OUT_DIR / "corpus.jsonl"
    cursor = "*"
    total = 0
    seen = set()
    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            r = requests.get(EPMC, params={
                "query": query, "format": "json", "resultType": "core",
                "pageSize": 1000, "cursorMark": cursor,
            }, timeout=90)
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
                rec = {
                    "pmid": it.get("pmid"), "doi": it.get("doi"),
                    "title": it.get("title", ""),
                    "authors": it.get("authorString", ""),
                    "journal": it.get("journalTitle") or it.get("source", ""),
                    "year": (it.get("firstPublicationDate", "") or "")[:4],
                    "pub_type": it.get("pubType", ""),
                    "is_oa": it.get("isOpenAccess", "N"),
                    "abstract": _clean(it.get("abstractText", "")),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1
            next_cursor = js.get("nextCursorMark")
            print(f"  已拉取 {total} 篇...", flush=True)
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            time.sleep(0.3)  # 对 API 友好

    meta = {"built_at": datetime.now().isoformat(timespec="seconds"),
            "years": f"{start_year}-{end_year}", "count": total, "source": "Europe PMC"}
    (OUT_DIR / "corpus_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    size_mb = out_path.stat().st_size / 1024 / 1024
    return f"SSc 语料库已建：{total} 篇（{start_year}-{end_year}），{size_mb:.1f} MB → {out_path}"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sy = int(sys.argv[1]) if len(sys.argv) > 1 else 2016
    ey = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    print(build(sy, ey))
