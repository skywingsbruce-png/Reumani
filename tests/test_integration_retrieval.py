"""集成测试：用最小 fixture 语料跑通检索管道（分类→扩展→BM25→融合），
不依赖开发者电脑上的 data_lake、不加载向量/重排序模型、不调 API。
验证：'SSc scarring'（关键词'scarring'摘要里没有）经同义词扩展到 fibrosis 后，
能召回纤维化论文，而不是把'整形疤痕'噪音排前面。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import retrieval as R
import data_lake_query as dlq

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_corpus.jsonl"


@pytest.fixture
def mini_corpus(monkeypatch):
    docs = [json.loads(l) for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()]
    monkeypatch.setattr(dlq, "_load_corpus", lambda name: docs)
    monkeypatch.setattr(dlq, "_corpora_to_search", lambda corpus: ["MINI"])
    R._BM25.clear()          # 清 BM25 缓存，强制用 fixture 重建
    yield docs
    R._BM25.clear()


@pytest.mark.integration
def test_synonym_expansion_recovers_fibrosis(mini_corpus):
    # synonym 通道：scarring 应扩到 fibrosis，召回纤维化论文（pmid 1001）
    docs = R.retrieve_docs("SSc scarring", corpus="MINI", mode="synonym", top_k=5)
    pmids = [d.get("pmid") for d in docs]
    assert "1001" in pmids, f"应召回纤维化论文，实际 {pmids}"
    # 整形疤痕噪音(1003)不应排在纤维化论文之前
    assert pmids.index("1001") < pmids.index("1003") if "1003" in pmids else True


@pytest.mark.integration
def test_keyword_mode_runs_without_datalake(mini_corpus):
    docs = R.retrieve_docs("chromosomal instability inflammation", corpus="MINI", mode="keyword", top_k=3)
    assert any(d.get("pmid") == "1005" for d in docs)   # CIN 论文被召回


if __name__ == "__main__":
    print("用 pytest 运行本集成测试：pytest tests/test_integration_retrieval.py")
