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


def content_hash(obj):
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        blob = str(obj)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def make_toolresult(tool_name, ok, data, *, content_level, source="", source_ids=None,
                    warnings=None, error_type=None, error_message=None,
                    dataset_version=None, parameters=None, tool_version=None):
    """返回 ToolResult.model_dump()（已校验）。失败(ok=False)必须给 error_type+error_message。"""
    prov = Provenance(
        tool_name=tool_name, source=source, retrieved_at=now(),
        parameters=parameters or {}, tool_version=tool_version, code_commit=code_commit(),
        dataset_version=dataset_version, source_ids=source_ids or [],
        content_level=content_level, content_hash=content_hash(data) if data is not None else None)
    return ToolResult(tool_name=tool_name, ok=ok, data=data if ok else None,
                      error_type=error_type, error_message=error_message,
                      provenance=prov, warnings=warnings or []).model_dump()
