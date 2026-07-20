"""
结构化工具信封：把工具结果包成【经 schemas.ToolResult 校验】的 artifact（供 content_and_artifact）。
好处：ToolResult 的 model_validator 强制"失败必带 error、成功不得带 error"，即【工具失败不能 ok=True】。
content_hash 记录、code_commit 缓存、retrieved_at 时间戳。规则不在此重写，只组装。
"""

import hashlib
import json
import subprocess
from datetime import datetime

from schemas import ToolResult, Provenance

_COMMIT = None


def code_commit():
    global _COMMIT
    if _COMMIT is None:
        try:
            _COMMIT = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                     text=True, timeout=5).stdout.strip() or "unknown"
        except Exception:
            _COMMIT = "unknown"
    return _COMMIT


def now():
    return datetime.now().isoformat(timespec="seconds")


HASH_ALGORITHM = "sha256"        # 新记录一律 SHA-256；不再生成 SHA-1


def _blob(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def compute_hash(obj):
    """新记录的权威 hash：SHA-256 全长(64位十六进制)。"""
    return hashlib.sha256(_blob(obj).encode("utf-8")).hexdigest()


def hash_bytes(data: bytes):
    return hashlib.sha256(data).hexdigest()


def detect_hash_algorithm(value):
    """识别既有记录的 hash 算法（兼容旧数据，不把旧 SHA-1 误标为 SHA-256）。"""
    if not isinstance(value, str) or not value:
        return "unknown"
    v = value.strip().lower()
    if not all(c in "0123456789abcdef" for c in v):
        return "unknown"
    if len(v) == 64:
        return "sha256"
    if len(v) == 40:
        return "sha1"
    return "unknown"          # 含旧的 16 位截断值 → legacy/unknown，不冒充 sha256


def content_hash(obj):
    """向后兼容名；现在返回 SHA-256（见 compute_hash）。"""
    return compute_hash(obj)


def make_toolresult(tool_name, ok, data, *, content_level, source="", source_ids=None,
                    warnings=None, error_type=None, error_message=None,
                    dataset_version=None, parameters=None, tool_version=None):
    """返回 ToolResult.model_dump()（已校验）。失败(ok=False)必须给 error_type+error_message。"""
    prov = Provenance(
        tool_name=tool_name, source=source, retrieved_at=now(),
        parameters=parameters or {}, tool_version=tool_version, code_commit=code_commit(),
        dataset_version=dataset_version, source_ids=source_ids or [],
        content_level=content_level,
        content_hash=compute_hash(data) if data is not None else None,
        hash_algorithm=HASH_ALGORITHM if data is not None else None)
    return ToolResult(tool_name=tool_name, ok=ok, data=data if ok else None,
                      error_type=error_type, error_message=error_message,
                      provenance=prov, warnings=warnings or []).model_dump()
