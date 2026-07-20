"""
RunManifest 安全加固：递归脱敏 + 字段白名单 + 长度/体积上限 + 敏感头剔除 + 相对路径 + PHI 警告。
目的：shadow_manifest.json 保存工具输出前，绝不落盘 API key/Authorization/.env/绝对路径/超大内容。
"""

import json
import re
from pathlib import Path

MANIFEST_SCHEMA_VERSION = "runmanifest-v1"
MAX_FIELD = 4000          # 单字段字符上限
MAX_TOTAL = 200_000       # 整体 manifest 字符上限
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}|(?:ANTHROPIC|DEEPSEEK|OPENAI)_API_KEY\s*[=:]\s*\S+"
                        r"|Bearer\s+[A-Za-z0-9._\-]{10,}", re.I)
# 键名命中即整段丢弃（默认不保存环境变量/授权头/密钥）
_DROP_KEYS = {"authorization", "api_key", "apikey", "api-key", "token", "secret", "password",
              "cookie", "set-cookie", "headers", "header", "env", "environ", "os_environ", "authorization_header"}


def _redact_str(s, base=None):
    s = _SECRET_RE.sub("[REDACTED]", s)
    if base:
        s = s.replace(str(base), ".")           # 绝对路径 → 相对
    if len(s) > MAX_FIELD:
        s = s[:MAX_FIELD] + f"…[truncated {len(s)-MAX_FIELD} chars]"
    return s


def _walk(obj, base):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _DROP_KEYS:
                out[k] = "[DROPPED:sensitive]"
                continue
            out[k] = _walk(v, base)
        return out
    if isinstance(obj, list):
        return [_walk(x, base) for x in obj]
    if isinstance(obj, str):
        return _redact_str(obj, base)
    return obj


# 字段白名单：只允许这些审计字段落盘，其它一律丢弃（防意外泄漏）
WHITELIST = {
    "shadow_verification", "shadow_status", "shadow_error_type", "shadow_error_message",
    "run_id", "created_at", "git_commit", "model_id", "question", "research_plan",
    "selected_tools", "allowed_tools", "unauthorized_tool_calls", "tool_events",
    "evidence_cards", "claims", "claim_extraction_error", "any_tool_failed",
    "old_verifier_result", "shadow_verifier_result", "comparison", "note",
    "manifest_schema_version", "phi_warning", "_size_note",
}


def sanitize_manifest(manifest, base=None):
    """返回脱敏 + 字段白名单 + 限长 + 限体积后的 manifest（不改原对象）。"""
    base = base or Path(__file__).resolve().parent
    walked = _walk(manifest, base)
    m = {k: v for k, v in walked.items() if k in WHITELIST}   # 字段白名单
    m["manifest_schema_version"] = MANIFEST_SCHEMA_VERSION
    m["phi_warning"] = "本 manifest 可能含科研文本；不得保存患者可识别信息(PHI)/密钥/授权头，已递归脱敏。"
    # 体积上限：超限则把最大的字符串字段替换为 hash+长度占位
    blob = json.dumps(m, ensure_ascii=False, default=str)
    if len(blob) > MAX_TOTAL:
        m = _shrink(m)
        m["_size_note"] = f"manifest 超过 {MAX_TOTAL} 字符，已压缩大字段。"
    return m


def _shrink(m):
    from tool_envelope import compute_hash, HASH_ALGORITHM

    def sh(obj):
        if isinstance(obj, dict):
            return {k: sh(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sh(x) for x in obj]
        if isinstance(obj, str) and len(obj) > 500:
            return {"hash_value": compute_hash(obj), "hash_algorithm": HASH_ALGORITHM,
                    "_len": len(obj), "_preview": obj[:200]}
        return obj
    return sh(m)


def artifact_ref(path, content=None):
    """大型 artifact 只存 路径 + hash + size（不内联二进制/大矩阵）。新记录一律 SHA-256。"""
    from tool_envelope import hash_bytes, compute_hash, HASH_ALGORITHM
    p = Path(path)
    h, size = None, None
    try:
        if p.exists():
            b = p.read_bytes()
            h, size = hash_bytes(b), len(b)
        elif content is not None:
            h = compute_hash(content)
    except Exception:
        pass
    return {"path": str(p), "hash_value": h, "hash_algorithm": (HASH_ALGORITHM if h else None),
            "size": size, "inline": False}
