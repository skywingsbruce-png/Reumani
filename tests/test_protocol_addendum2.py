"""Addendum 2（Exact-ID 确定性执行契约）冻结校验。零真实 API。
不改 v1/v2/Addendum 1 及其 hash；只锁定 Addendum 2 自身。"""
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ADDENDUM2_SHA = "b3646d346c1e18fa1293fa275bc81a04e95f6aed917213d6689ee6c528a31381"
ADDENDUM2_MD = ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_2.md"
ADDENDUM2_SHA_FILE = ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_2.sha256"

V1_SHA = "5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22"
V2_SHA = "c76f589485e4ebfd728c27b653d2735f3ebd1c6930087c244e4efbdba9d66696"
A1_SHA = "de3afcdd2131ba17717eb2d93a543e350aeff16e11c2ecc32edd8d583f9ca7f3"


def lf_sha256(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.mark.unit
def test_addendum2_hash_is_frozen():
    assert lf_sha256(ADDENDUM2_MD) == ADDENDUM2_SHA


@pytest.mark.unit
def test_addendum2_sha_file_records_same_hash():
    first = ADDENDUM2_SHA_FILE.read_text(encoding="utf-8").splitlines()[0]
    assert first.split()[0] == ADDENDUM2_SHA
    assert "ADDENDUM_2.md" in first


@pytest.mark.unit
def test_v1_v2_addendum1_unchanged_by_addendum2():
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL.md") == V1_SHA
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2.md") == V2_SHA
    assert lf_sha256(ROOT / "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_1.md") == A1_SHA


@pytest.mark.unit
def test_addendum2_hash_is_platform_stable():
    raw = ADDENDUM2_MD.read_bytes()
    lf, crlf = raw.replace(b"\r\n", b"\n"), raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    a = hashlib.sha256(lf.replace(b"\r\n", b"\n")).hexdigest()
    b = hashlib.sha256(crlf.replace(b"\r\n", b"\n")).hexdigest()
    assert a == b == ADDENDUM2_SHA


@pytest.mark.unit
def test_gitattributes_covers_addendum2():
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for name in ("SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_2.md",
                 "SHADOW_PILOT_ROUND2_PROTOCOL_V2_ADDENDUM_2.sha256"):
        assert any(line.startswith(name) and line.rstrip().endswith("-text")
                   for line in ga.splitlines()), f"{name} 未在 .gitattributes 标 -text"


@pytest.mark.unit
def test_addendum2_scope_is_exact_id_only():
    txt = ADDENDUM2_MD.read_text(encoding="utf-8")
    # 契约要点必须在场
    for must in ("retrieval_status", "resolution_status", "verified", "not_found",
                 "mismatch", "manual_needed", "ExactIdResolution", "ExactIdBatchResult",
                 "resolve_exact_ids", "PubMed", "Crossref", "Europe PMC"):
        assert must in txt, f"Addendum 2 缺少契约要点：{must}"
    # 明确不改题目/评分/预算/上限
    assert "不改" in txt and "评分" in txt
