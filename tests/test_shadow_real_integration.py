"""手动真实 Shadow 试运行（默认 CI 跳过：marker=shadow_real_integration）。

运行命令：
    python -m pytest tests/test_shadow_real_integration.py -m shadow_real_integration -s

可选环境：
    REUMANI_REAL_LLM=1   —— 才会用【付费模型】做 Claim 提取；默认用确定性 fake，不产生费用。
是否产生费用：默认【不产生】（只联网 Europe PMC，免费；GEO 分析需本地 data_lake，缺则 skip）。
输出位置：stdout（-s 可见）+ runs/<run_id>/shadow_manifest.json（若走 run_agent；本脚本直接打印指标）。
清理方法：本脚本不写敏感数据；如产生 runs/ 记录，删除 agent_workspace/ 与 runs/ 即可。

原则：缺数据/断网 → skip 或 unavailable，绝不假装 zero_hits；默认不调付费 API。
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytestmark = pytest.mark.shadow_real_integration


def _fake_causal_claim(ft, card_ids):
    # 任务5：故意的"因果过度表述"——只有相关性证据却主张因果
    return [{"text": "cGAS-STING 因果驱动 SSc 纤维化", "claim_type": "causal", "supporting_ids": card_ids[:1]}]


def test_shadow_real_5_public_tasks(capsys):
    import ssc_skill_agent as SK
    import shadow as SH

    real_llm = os.environ.get("REUMANI_REAL_LLM") == "1"
    tasks = [
        ("SSc文献检索", "search_evidence", {"query": "systemic sclerosis fibrosis", "n": 5}),
        ("精确PMID", "search_evidence", {"query": "cGAS STING scleroderma", "n": 5}),
        ("本地数据湖", "query_data_lake", {"kind": "corpus", "query": "SSc: fibrosis"}),
        ("公开GEO signature", "triage_hypothesis",
         {"signature_a": "CIN", "signature_b": "cGAS_STING", "geo_datasets": "GSE58095"}),
    ]
    tools = {t.name: t for t in SK.SKILL_AGENT_TOOLS}
    messages, structured, legacy = [], 0, 0
    for label, name, args in tasks:
        try:
            tm = tools[name].invoke({"type": "tool_call", "name": name, "args": args, "id": label})
        except Exception as e:
            print(f"[skip] {label}: {type(e).__name__} {str(e)[:80]}")
            continue
        art = getattr(tm, "artifact", None)
        if isinstance(art, dict) and art.get("schema_version") == "toolresult-v1":
            structured += 1
        else:
            legacy += 1
        messages.append(tm)

    if not messages:
        pytest.skip("无可用工具结果（可能断网/缺 data_lake）——不假装 zero_hits")

    extractor = (SH.default_claim_extractor("deepseek") if real_llm else _fake_causal_claim)
    t0 = time.time()
    manifest = SH.run_shadow("SSc cGAS-STING 关联", messages=messages,
                             allowed_tools=list(tools), old_verify={"passed": True},
                             claim_extractor=extractor)
    runtime = round(time.time() - t0, 2)

    cards = manifest["evidence_cards"]
    claims = manifest["claims"]
    citation_layer = manifest["shadow_verifier_result"]["layers"]["citation"]
    metrics = {
        "structured_tool_rate": round(structured / max(structured + legacy, 1), 2),
        "legacy_tool_rate": round(legacy / max(structured + legacy, 1), 2),
        "evidence_card_count": len(cards),
        "unsupported_claims": sum(1 for c in claims if c["verdict"] in
                                  ("insufficient_evidence", "not_supported", "contradicted", "partially_supported")),
        "citation_failures": 0 if citation_layer["passed"] else 1,
        "causal_overstatement": sum(1 for c in claims if c["claim_type"] == "causal" and c["verdict"] != "supported"),
        "old_shadow_agreement": manifest["comparison"]["agree"],
        "divergence": manifest["comparison"]["divergence"],
        "runtime_s": runtime,
        "model_calls": (1 if real_llm else 0),
        "estimated_cost": ("unknown" if real_llm else "0 (fake claim extractor)"),
    }
    print("\n=== shadow_real_integration 指标 ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    # 因果过度表述必须被抓到（相关证据撑不起因果）
    assert metrics["causal_overstatement"] >= 1
    assert manifest["manifest_schema_version"] == "runmanifest-v1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-m", "shadow_real_integration", "-s"]))
