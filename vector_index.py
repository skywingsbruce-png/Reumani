"""
向量索引 + 重排序（步骤4-5）。
- 文献级 dense 向量索引（标题+摘要，每篇一个向量），本地缓存。
- Cross-encoder 重排序，把真正相关的顶上去、蹭关键词的压下去。
默认用轻快的 all-MiniLM-L6-v2（几分钟能建完9万篇）；换成生物医学模型(PubMedBert/SPECTER2)
只需改 EMBED_MODEL 一行、重建索引即可（质量更好但更慢）。
"""

import os
from pathlib import Path

import numpy as np

import data_lake_query as dlq

BASE = Path(__file__).resolve().parent
VEC_DIR = BASE / "data_lake" / "vector"
VEC_DIR.mkdir(parents=True, exist_ok=True)

EMBED_MODEL = os.environ.get("SSC_EMBED_MODEL", "all-MiniLM-L6-v2")
RERANK_MODEL = os.environ.get("SSC_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

_MODEL = None
_RERANKER = None
_EMB_CACHE = {}


def _model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(EMBED_MODEL)
    return _MODEL


def _reranker():
    global _RERANKER
    if _RERANKER is None:
        from sentence_transformers import CrossEncoder
        _RERANKER = CrossEncoder(RERANK_MODEL)
    return _RERANKER


def _emb_path(name):
    return VEC_DIR / f"{name}.npy"


def build_index(name, batch_size=64):
    """为某个语料库建 dense 向量索引并存盘。"""
    docs = dlq._load_corpus(name)
    if not docs:
        return f"{name}: 语料为空"
    texts = [(d.get("title", "") + ". " + d.get("abstract", ""))[:2000] for d in docs]
    m = _model()
    embs = m.encode(texts, batch_size=batch_size, show_progress_bar=False,
                    normalize_embeddings=True).astype(np.float32)
    np.save(_emb_path(name), embs)
    return f"{name}: 向量索引已建 {embs.shape[0]} 篇 × {embs.shape[1]} 维 ({EMBED_MODEL})"


def _load_emb(name):
    if name not in _EMB_CACHE:
        p = _emb_path(name)
        _EMB_CACHE[name] = np.load(p) if p.exists() else None
    return _EMB_CACHE[name]


def has_index(name):
    return _emb_path(name).exists()


def dense_rank(query, name, top_k=50):
    """返回该库里与 query 语义最近的文档下标（按相似度降序）。无索引则返回 None。"""
    embs = _load_emb(name)
    if embs is None:
        return None
    q = _model().encode([query], normalize_embeddings=True)[0].astype(np.float32)
    sims = embs @ q
    return list(np.argsort(sims)[::-1][:top_k])


def rerank(query, candidates, top_k=15):
    """candidates: [(name, doc)]。用 cross-encoder 对 (query, 标题+摘要) 打分重排。"""
    if not candidates:
        return []
    pairs = [[query, (d.get("title", "") + ". " + d.get("abstract", ""))[:1500]] for _, d in candidates]
    scores = _reranker().predict(pairs)
    order = np.argsort(scores)[::-1][:top_k]
    return [candidates[i] for i in order]


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    which = sys.argv[1:] or ["CIN", "SSc"]
    for n in which:
        print(build_index(n))
