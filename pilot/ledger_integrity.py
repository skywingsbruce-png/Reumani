"""共享 append-only 账本的完整性校验（A.6.6 §6）。

历史轮次要求"不删除历史账本"，而多次 run 共用同一个 stage 账本，
因此**整文件 hash 相等**是错误的校验方式 —— 追加合法内容就会假报"被改动"。

正确语义：**前缀不变 + 只允许追加**。
"""

import hashlib
import json


def prefix_sha256(path, length):
    """文件前 `length` 字节的 SHA-256。"""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read(length)).hexdigest()


def verify_append_only(path, *, original_length, original_prefix_sha256,
                       expect_run_ids=None, original_run_ids=None):
    """校验共享账本自基线以来只被**追加**过。

    返回诊断 dict；任何违规都在 `violations` 里，调用方 fail-closed。
    """
    import pathlib

    p = pathlib.Path(path)
    out = {"path": p.name, "exists": p.exists(), "violations": []}
    if not p.exists():
        out["skipped"] = "账本不存在（干净检出）"
        return out

    raw = p.read_bytes()
    out["current_length"] = len(raw)
    out["original_length"] = original_length

    # 1) 长度只能增长（截断 → 违规）
    if len(raw) < original_length:
        out["violations"].append("truncated")
        return out

    # 2) 前 original_length 字节必须逐字节不变（覆盖/中间插入/重排 → 违规）
    actual_prefix = hashlib.sha256(raw[:original_length]).hexdigest()
    out["prefix_sha256"] = actual_prefix
    if actual_prefix != original_prefix_sha256:
        out["violations"].append("prefix_modified")
        return out

    # 3) 追加部分必须能独立解析
    appended = raw[original_length:]
    out["appended_bytes"] = len(appended)
    events, bad = [], 0
    for line in appended.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            bad += 1
    out["appended_events"] = len(events)
    if bad:
        out["violations"].append(f"appended_unparsable:{bad}")

    # 4) 追加事件的归属：不得混入 original 的 run
    if original_run_ids:
        stray = [e for e in events
                 if e.get("task_id") and e.get("task_id") in original_run_ids]
        if stray:
            out["violations"].append(f"appended_events_from_original_run:{len(stray)}")
    if expect_run_ids:
        seen = {e.get("stage") for e in events if e.get("stage")}
        out["appended_stages"] = sorted(x for x in seen if x)

    return out


def baseline_from_file(path):
    """把当前文件状态记成基线（供首次冻结用）。"""
    import pathlib

    raw = pathlib.Path(path).read_bytes()
    return {"original_length": len(raw),
            "original_prefix_sha256": hashlib.sha256(raw).hexdigest()}
