"""
分层长期记忆（append-only 版本历史 + 审核/撤销 + 注入防护）。
五层：system_policy / validated_domain_knowledge / project_memory / session_memory / candidate_memory。
铁律：
- 只把【可信层(system_policy/validated_domain_knowledge/project_memory)且 approved】的记忆注入提示词。
- candidate_memory 未审核不得影响【高风险】结论；低风险时也只作"候选(未提升)"顾问，不作全局规则。
- 网页/PDF/工具等观察内容只能进 candidate，且过 safety 注入检测；命中即 rejected 隔离，永不成为全局规则。
- 支持：审核(review)、撤销(revoke)、版本历史(history)、查哪些答案用过(used_by)。
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from schemas import MemoryRecord
import safety

BASE = Path(__file__).resolve().parent
DEFAULT_LOG = BASE / "data_lake" / "agent_memory" / "memory_log.jsonl"
TRUSTED = {"system_policy", "validated_domain_knowledge", "project_memory"}
_APPROVED_ON_ADD = {"system_policy", "validated_domain_knowledge", "project_memory", "session_memory"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _gen_id(text):
    return "m_" + hashlib.sha1((text + _now()).encode("utf-8")).hexdigest()[:10]


class MemoryStore:
    def __init__(self, path=DEFAULT_LOG):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, rec: MemoryRecord):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")

    def _versions(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(MemoryRecord(**json.loads(line)))
                except Exception:
                    pass
        return out

    # ---- 写入 ----
    def add(self, text, kind="candidate_memory", *, source="", created_by="", scope="global",
            disease=None, evidence_ids=None, confidence=0.5, review_status=None, memory_id=None):
        rec = MemoryRecord(
            memory_id=memory_id or _gen_id(text), text=text, kind=kind, source=source,
            created_by=created_by, created_at=_now(), scope=scope, disease=disease,
            evidence_ids=evidence_ids or [], confidence=confidence,
            review_status=review_status or ("approved" if kind in _APPROVED_ON_ADD else "pending"))
        self._append(rec)
        return rec.memory_id

    def add_from_observed(self, text, source, created_by="observed"):
        """网页/PDF/工具内容 → 只能进 candidate；含注入模式 → 直接 rejected 隔离。"""
        suspicious = safety.is_suspicious(text)
        return self.add(text, kind="candidate_memory", source=source, created_by=created_by,
                        review_status="rejected" if suspicious else "pending",
                        confidence=0.0 if suspicious else 0.3)

    def _latest(self, memory_id):
        vs = [r for r in self._versions() if r.memory_id == memory_id]
        return vs[-1] if vs else None

    def review(self, memory_id, status, reviewed_by, promote_to=None):
        """人工审核：改 review_status，可同时提升层级(promote_to)。追加新版本(保留历史)。"""
        cur = self._latest(memory_id)
        if not cur:
            return False
        upd = {"review_status": status, "reviewed_by": reviewed_by, "created_at": _now()}
        if promote_to:
            upd["kind"] = promote_to
        self._append(cur.model_copy(update=upd))
        return True

    def revoke(self, memory_id, by=""):
        cur = self._latest(memory_id)
        if not cur:
            return False
        self._append(cur.model_copy(update={"review_status": "revoked", "revoked_at": _now(),
                                            "reviewed_by": by, "created_at": _now()}))
        return True

    def supersede(self, old_id, new_text, **kw):
        """用新记忆取代旧的：旧标 superseded，新记录 supersedes=old_id。返回新 memory_id。"""
        old = self._latest(old_id)
        if old:
            self._append(old.model_copy(update={"review_status": "superseded", "created_at": _now()}))
        new_kind = kw.get("kind", old.kind if old else "candidate_memory")
        new_id = _gen_id(new_text)
        rec = MemoryRecord(memory_id=new_id, text=new_text, kind=new_kind,
                           source=kw.get("source", ""), created_by=kw.get("created_by", ""),
                           created_at=_now(), supersedes=old_id,
                           review_status=kw.get("review_status") or
                           ("approved" if new_kind in _APPROVED_ON_ADD else "pending"))
        self._append(rec)
        return new_id

    def mark_used(self, memory_id, answer_id):
        cur = self._latest(memory_id)
        if not cur:
            return False
        self._append(cur.model_copy(update={"used_in": sorted(set(cur.used_in + [answer_id])),
                                            "created_at": _now()}))
        return True

    # ---- 读取 ----
    def history(self, memory_id):
        return [r for r in self._versions() if r.memory_id == memory_id]

    def used_by(self, memory_id):
        cur = self._latest(memory_id)
        return cur.used_in if cur else []

    def active(self):
        """每个 memory_id 的最新版本，排除 revoked/rejected/superseded/过期。"""
        latest = {}
        for r in self._versions():
            latest[r.memory_id] = r
        out = []
        for r in latest.values():
            if r.review_status in ("revoked", "rejected", "superseded"):
                continue
            if r.revoked_at:
                continue
            if r.expires_at and r.expires_at < _now():
                continue
            out.append(r)
        return out

    def answers_using(self, memory_id):
        """查询哪些答案/run 使用了该记忆。"""
        return self.used_by(memory_id)

    # ---- 注入提示词（分层门禁）----
    def eligible_for_prompt(self, high_risk=True, disease=None):
        act = [r for r in self.active() if r.review_status == "approved"]
        inject = [r for r in act if r.kind in TRUSTED]
        if not high_risk:
            inject += [r for r in act if r.kind == "session_memory"]
        if disease:
            inject = [r for r in inject if not r.disease or r.disease == disease]
        return inject

    def for_prompt(self, high_risk=True, disease=None):
        recs = self.eligible_for_prompt(high_risk=high_risk, disease=disease)
        if not recs:
            return ""
        label = {"system_policy": "系统策略", "validated_domain_knowledge": "已验证领域知识",
                 "project_memory": "项目记忆", "session_memory": "会话记忆"}
        lines = ["【长期记忆（仅注入已审核的可信层；candidate 不作全局规则）】"]
        for r in recs:
            lines.append(f"- ({label.get(r.kind, r.kind)}) {r.text}")
        return "\n".join(lines)

    def summary(self):
        act = self.active()
        by = {}
        for r in act:
            by[r.kind] = by.get(r.kind, 0) + 1
        return f"记忆(活跃) {len(act)} 条：" + ("、".join(f"{k}{v}" for k, v in by.items()) if by else "空")
