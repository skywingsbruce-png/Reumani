"""A.7.5.5 —— 冻结证据加载器：只加载已审计冻结的 Canary 子集，任何漂移都在**第一次模型调用前** fail-closed。

零网络、零付费、零 LLM。所有校验都是纯 hash / schema / 计数比对。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Optional

DEFAULT_SUBSET_DIR = "evidence_packs/ssc_cgas_sting_canary_v1"
DEFAULT_SOURCE_DIR = "evidence_packs/ssc_cgas_sting_v1"


class FrozenEvidenceError(RuntimeError):
    """冻结证据 hash / schema / 计数不一致 → fail-closed（provider 调用必须为 0）。"""


def _sha_obj(o) -> str:
    return hashlib.sha256(json.dumps(o, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _jsonl(p: pathlib.Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class FrozenEvidence:
    """通过全部校验后的只读证据视图（core / context-only 严格分离）。"""

    def __init__(self, *, subset_id, subset_hash, source_pack_hash, protocol_hash,
                 core_cards, context_only, manifest):
        self.subset_id = subset_id
        self.subset_hash = subset_hash
        self.source_pack_hash = source_pack_hash
        self.protocol_hash = protocol_hash
        self.core_cards = list(core_cards)          # 完整原始 EvidenceCard（未被改写）
        self.context_only = list(context_only)      # 仅背景导航，禁止作为实验依据
        self.manifest = dict(manifest)

    # ---- 供 Prompt / 断言使用的派生视图 ----
    @property
    def allowed_citation_ids(self) -> set:
        return {c["evidence_id"] for c in self.core_cards}

    @property
    def context_only_ids(self) -> set:
        return {c["evidence_id"] for c in self.context_only}

    @property
    def direct_human_causal_count(self) -> int:
        return int(self.manifest.get("direct_human_causal_count", 0))

    @property
    def causal_ceiling(self) -> str:
        """冻结证据允许的最高结论强度（确定性，不由模型决定）。"""
        if self.direct_human_causal_count > 0:
            return "direct_human_causal_supported"
        tiers = {c.get("causal_tier") for c in self.core_cards}
        if tiers & {"C3", "C4"}:
            return "preclinical_perturbation_support"
        if tiers & {"C2"}:
            return "temporal_association"
        return "association_only"

    def authoritative_facts(self) -> dict:
        """权威事实：模型**不得**改写这些字段。"""
        return {
            "subset_id": self.subset_id, "subset_hash": self.subset_hash,
            "source_pack_hash": self.source_pack_hash, "protocol_hash": self.protocol_hash,
            "core_card_count": len(self.core_cards),
            "context_only_count": len(self.context_only),
            "direct_count": sum(1 for c in self.core_cards
                                if c["disease_scope"] == "systemic_sclerosis_direct"),
            "indirect_count": sum(1 for c in self.core_cards
                                  if c["disease_scope"] == "non_ssc_mechanistic_transfer"),
            "direct_human_causal_count": self.direct_human_causal_count,
            "causal_ceiling": self.causal_ceiling,
            "evidence_gap": self.manifest.get("evidence_gap", {}),
            "allowed_citation_ids": sorted(self.allowed_citation_ids),
            "context_only_ids": sorted(self.context_only_ids),
            "cards": [{"evidence_id": c["evidence_id"], "pmid": c.get("normalized_pmid"),
                       "doi": c.get("normalized_doi"), "year": c.get("year"),
                       "disease_scope": c["disease_scope"], "relevance_tier": c["relevance_tier"],
                       "causal_tier": c["causal_tier"], "content_level": c["content_level"],
                       "publication_status": c["publication_status"],
                       "species": c.get("species"), "study_design": c.get("study_design"),
                       "does_not_support": c.get("does_not_support", []),
                       "limitations": c.get("limitations", [])}
                      for c in self.core_cards],
        }


class FrozenEvidenceLoader:
    """只从**已冻结、已审计**的 Canary 子集目录加载；绝不回退到 v1 全量包，绝不联网。"""

    def __init__(self, repo_root: str = ".", *, subset_dir: str = DEFAULT_SUBSET_DIR,
                 source_dir: str = DEFAULT_SOURCE_DIR,
                 expected_subset_hash: Optional[str] = None,
                 expected_source_pack_hash: Optional[str] = None,
                 expected_protocol_hash: Optional[str] = None):
        self.root = pathlib.Path(repo_root).resolve()
        self.subset = self.root / subset_dir
        self.source = self.root / source_dir
        self.expected = {"subset": expected_subset_hash, "pack": expected_source_pack_hash,
                         "protocol": expected_protocol_hash}

    def load(self) -> FrozenEvidence:
        if not self.subset.is_dir():
            raise FrozenEvidenceError(f"冻结子集目录不存在：{self.subset}")
        try:
            manifest = json.loads((self.subset / "CANARY_INPUT_MANIFEST.json").read_text(encoding="utf-8"))
            ref = json.loads((self.subset / "source_pack_reference.json").read_text(encoding="utf-8"))
            core_rows = _jsonl(self.subset / "canary_evidence_cards.jsonl")
            ctx_rows = _jsonl(self.subset / "context_only.jsonl")
            manual_rows = _jsonl(self.subset / "manual_review_assessment.jsonl")
            src_manifest = json.loads(
                (self.source / "manifests" / "evidence_pack_manifest.json").read_text(encoding="utf-8"))
            src_cards = {c["evidence_id"]: c for c in
                         _jsonl(self.source / "evidence" / "evidence_cards.jsonl")}
        except FileNotFoundError as e:
            raise FrozenEvidenceError(f"冻结证据文件缺失：{e}") from e
        except json.JSONDecodeError as e:
            raise FrozenEvidenceError(f"冻结证据 JSON 损坏：{e}") from e

        # ---- schema ----
        if manifest.get("schema_version") != "canary-input-manifest-v1":
            raise FrozenEvidenceError(f"未知 manifest schema：{manifest.get('schema_version')}")
        if ref.get("schema_version") != "canary-source-reference-v1":
            raise FrozenEvidenceError(f"未知 source reference schema：{ref.get('schema_version')}")

        # ---- subset hash（确定性重算） ----
        recomputed = _sha_obj({k: v for k, v in manifest.items() if k != "subset_hash"})
        if recomputed != manifest.get("subset_hash"):
            raise FrozenEvidenceError("subset_hash 与 manifest 内容不一致（子集被修改）")

        # ---- source pack hash（确定性重算） ----
        pack_recomputed = _sha_obj({k: v for k, v in src_manifest.items()
                                    if k not in ("pack_hash", "created_at")})
        if pack_recomputed != src_manifest.get("pack_hash"):
            raise FrozenEvidenceError("source pack_hash 与其 manifest 内容不一致（v1 包被修改）")
        if ref.get("source_pack_hash") != src_manifest["pack_hash"]:
            raise FrozenEvidenceError("子集引用的 source_pack_hash 与 v1 包不一致")
        if manifest.get("source_pack_hash") != src_manifest["pack_hash"]:
            raise FrozenEvidenceError("manifest 的 source_pack_hash 与 v1 包不一致")
        if ref.get("protocol_hash") != src_manifest["protocol_hash"]:
            raise FrozenEvidenceError("protocol_hash 不一致")

        # ---- 调用方钉住的期望值（若提供） ----
        for key, actual in (("subset", manifest["subset_hash"]),
                            ("pack", src_manifest["pack_hash"]),
                            ("protocol", src_manifest["protocol_hash"])):
            exp = self.expected.get(key)
            if exp and not actual.startswith(exp.rstrip("…")):
                raise FrozenEvidenceError(f"{key}_hash 与期望不符：{actual[:16]} != {exp}")

        # ---- selected ids / 计数 ----
        selected = list(manifest.get("selected_card_ids") or [])
        if sorted(selected) != sorted(r["evidence_id"] for r in core_rows):
            raise FrozenEvidenceError("selected_card_ids 与 canary_evidence_cards.jsonl 不一致")
        if len(core_rows) != sum(manifest.get("counts_by_scope", {}).values()):
            raise FrozenEvidenceError("counts_by_scope 与 core 卡数不一致")
        if manifest.get("context_only_count") != len(ctx_rows):
            raise FrozenEvidenceError("context_only_count 与文件不一致")
        if manifest.get("manual_review_excluded_count") != len(manual_rows):
            raise FrozenEvidenceError("manual_review_excluded_count 与文件不一致")

        # ---- 每张原卡 hash + 准入边界 ----
        core_cards, manual_ids = [], set()
        for m in manual_rows:
            if m.get("enters_canary_subset"):
                raise FrozenEvidenceError("manual-review 项被标记进入子集（禁止）")
            for k in ("pmid", "doi"):
                if m.get(k):
                    manual_ids.add(str(m[k]))
        selected_hashes = manifest.get("selected_card_hashes", {})
        for row in core_rows:
            eid = row["evidence_id"]
            card = src_cards.get(eid)
            if card is None:
                raise FrozenEvidenceError(f"core 卡不在 v1 包中：{eid}")
            # 必须**重算**卡内容 hash：只比对存储字段无法发现正文（含不可信 excerpt）被篡改
            recomputed_card = _sha_obj({k: v for k, v in card.items()
                                        if k not in ("content_hash", "retrieved_at",
                                                     "previous_version", "superseded_by",
                                                     "counts_as_independent_evidence")})
            if recomputed_card != card.get("content_hash"):
                raise FrozenEvidenceError(
                    f"core 卡内容与其 content_hash 不一致（正文被篡改）：{eid}")
            if card["content_hash"] != row.get("source_card_content_hash"):
                raise FrozenEvidenceError(f"core 卡 content_hash 漂移（原卡被修改）：{eid}")
            if selected_hashes.get(eid) != card["content_hash"]:
                raise FrozenEvidenceError(f"manifest 记录的 card hash 与原卡不一致：{eid}")
            if card["disease_scope"] == "review_navigation" or card["relevance_tier"] == "S7":
                raise FrozenEvidenceError(f"综述不得进入 core：{eid}")
            if card["content_level"] != "abstract":
                raise FrozenEvidenceError(f"metadata_only 不得进入 core：{eid}")
            if not (set(card["provenance"]["verified_by"]) & {"pubmed", "crossref"}):
                raise FrozenEvidenceError(f"core 卡缺少 T1 精确核验来源：{eid}")
            if str(card.get("normalized_pmid")) in manual_ids:
                raise FrozenEvidenceError(f"manual-review 项混入 core：{eid}")
            core_cards.append(card)

        ctx_cards = []
        for row in ctx_rows:
            c = src_cards.get(row["evidence_id"])
            if c is None:
                raise FrozenEvidenceError(f"context-only 卡不在 v1 包中：{row['evidence_id']}")
            if not row.get("non_evidentiary_context"):
                raise FrozenEvidenceError("context-only 卡必须标记 non_evidentiary_context")
            ctx_cards.append(c)
        if set(r["evidence_id"] for r in ctx_rows) & set(selected):
            raise FrozenEvidenceError("context-only 与 core 存在交集（必须严格分离）")

        # ---- 因果事实 ----
        declared = int(manifest.get("direct_human_causal_count", -1))
        actual_direct_causal = sum(1 for c in core_cards if c["causal_tier"] == "C5")
        if declared != actual_direct_causal:
            raise FrozenEvidenceError(
                f"direct_human_causal_count 与卡片不符：声明 {declared}，实际 C5 卡 {actual_direct_causal}")
        if "evidence_gap" not in manifest:
            raise FrozenEvidenceError("manifest 缺少 evidence_gap")

        # subset_id 必须标识**子集本身**（目录名）。manifest 里的 `source_pack_id` 指的是
        # 上游全量包，用它当 subset_id 会把 canary 子集错标成 v1 全量包。
        return FrozenEvidence(subset_id=self.subset.name,
                              subset_hash=manifest["subset_hash"],
                              source_pack_hash=src_manifest["pack_hash"],
                              protocol_hash=src_manifest["protocol_hash"],
                              core_cards=core_cards, context_only=ctx_cards, manifest=manifest)


__all__ = ["FrozenEvidenceLoader", "FrozenEvidence", "FrozenEvidenceError",
           "DEFAULT_SUBSET_DIR", "DEFAULT_SOURCE_DIR"]
