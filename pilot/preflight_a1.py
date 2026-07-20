"""A1 运行前检查（19 项，**默认 dry-run，永不调用模型**）。

铁律：
- 只构造客户端、只读取配置，**绝不** invoke/ainvoke/stream/batch，绝不发 HTTP；
- API key 只判断"是否存在"；key 的**值**不得进入日志、异常、repr、快照或任何落盘文件；
- 检查按顺序执行；第一个失败后停止后续**有副作用**的检查，纯静态安全检查仍继续；
- 失败时退出码非 0，且不创建 run 目录、不创建付费账本、不标记"任务已通过"、不自动进入 A1。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

V1_SHA = "5d166bce159de665c4df677aef6765803575a48827afdc5d061cb49ff54f0f22"
V2_SHA = "c76f589485e4ebfd728c27b653d2735f3ebd1c6930087c244e4efbdba9d66696"

# 被批准运行的 commit 由**外部传入**（环境变量或 CLI），不写死：
# 写死会让 preflight 在每次提交后自我失效；缺省时**不自动通过**，只记录实际值待人工确认。
ENV_EXPECT_DEV = "REUMANI_EXPECT_DEV_HEAD"
ENV_EXPECT_PUBLIC = "REUMANI_EXPECT_PUBLIC_HEAD"
ENV_CI_EVIDENCE = "REUMANI_CI_EVIDENCE"          # CI run number / URL
ENV_PYTEST_EVIDENCE = "REUMANI_PYTEST_EVIDENCE"  # pytest 报告或人工确认


VERIFIED_AUTO = "verified_automatic"        # 脚本实际验证
VERIFIED_EXT = "verified_external"          # 外部证据确认（必须给出证据来源）
UNVERIFIED_EXT = "unverified_external"      # 需要外部证据但没给 → 不得显示为 PASS
FAILED = "failed"
SKIPPED = "skipped"


class Preflight:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.rows = []
        self.side_effects_blocked = False

    def _add(self, cid, name, status, detail, evidence=None):
        rec = {"check_id": cid, "name": name, "status": status,
               "passed": status in (VERIFIED_AUTO, VERIFIED_EXT),
               "detail": str(detail)[:300], "evidence_source": evidence}
        self.rows.append(rec)
        tag = {VERIFIED_AUTO: "PASS", VERIFIED_EXT: "PASS*", UNVERIFIED_EXT: "UNVERIF",
               FAILED: "FAIL", SKIPPED: "SKIP"}[status]
        ev = f"  [{evidence}]" if evidence else ""
        print(f"[{tag:>7}] {cid:>2}. {name} — {rec['detail']}{ev}"[:200])
        if not rec["passed"]:
            self.side_effects_blocked = True
        return rec["passed"]

    def check(self, cid, name, ok, detail=""):
        """脚本自己验证的项。"""
        return self._add(cid, name, VERIFIED_AUTO if ok else FAILED, detail)

    def external(self, cid, name, evidence, detail=""):
        """需要外部证据的项。缺证据 → unverified_external，**不得伪装成 PASS**。"""
        if evidence and str(evidence).strip():
            return self._add(cid, name, VERIFIED_EXT, detail, str(evidence).strip())
        return self._add(cid, name, UNVERIFIED_EXT,
                         f"{detail}（缺外部证据 → Pilot 拒绝启动）")

    def skip(self, cid, name, why):
        return self._add(cid, name, SKIPPED, why)

    @property
    def failed(self):
        return [r for r in self.rows if not r["passed"]]


def _sha_lf(p):
    return hashlib.sha256(Path(p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _git(args):
    return subprocess.run(["git"] + args, cwd=BASE, capture_output=True,
                          text=True).stdout.strip()


def _key_present(var):
    """只判断存在性。**绝不返回、记录或打印值。**"""
    v = os.environ.get(var)
    if v is not None and str(v).strip():
        return True
    p = BASE / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith(var + "="):
                return bool(line.split("=", 1)[1].strip())
    return False


def run(dry_run=True):
    pf = Preflight(dry_run)
    from pilot import hard_gate as HG
    from pilot import paid_transport as PT
    from pilot import prices as PR
    from pilot import round2_runner as RUN
    from pilot.round2_tasks import PROTOCOL_SHA256, TASKS

    # ---- 1-7：纯静态 ----
    # 1 —— 远端 public HEAD：脚本不查远端，必须由外部提供证据
    exp_pub = os.environ.get(ENV_EXPECT_PUBLIC, "")
    pf.external(1, "public HEAD 与被批准的 commit 一致",
                exp_pub and f"externally supplied commit={exp_pub.strip()}",
                "脚本不访问远端，也不读取任何 GitHub token")
    # 2 —— dev HEAD：本地可自动验证，但期望值仍来自外部
    dev = _git(["rev-parse", "--short", "HEAD"])
    exp_dev = os.environ.get(ENV_EXPECT_DEV, "").strip()
    if not exp_dev:
        pf.external(2, "dev HEAD 与被批准的 commit 一致", None,
                    f"actual={dev}，未提供 {ENV_EXPECT_DEV}")
    else:
        pf.check(2, "dev HEAD 与被批准的 commit 一致", dev.startswith(exp_dev),
                 f"actual={dev} expect={exp_dev}")
    dirty = [l for l in _git(["status", "--porcelain"]).splitlines() if l.strip()]
    pf.check(3, "工作区干净", not dirty, f"{len(dirty)} 项未提交" if dirty else "clean")
    # 4/5 —— CI 状态与完整 pytest 统计：脚本无法自证，必须外部证据
    pf.external(4, "CI 全绿", os.environ.get(ENV_CI_EVIDENCE, ""),
                f"需提供 CI run number 或 URL（设 {ENV_CI_EVIDENCE}）")
    pf.external(5, "全量 pytest 通过", os.environ.get(ENV_PYTEST_EVIDENCE, ""),
                f"需提供 pytest 报告或人工确认（设 {ENV_PYTEST_EVIDENCE}）")
    v1, v2 = _sha_lf(BASE / "SHADOW_PILOT_ROUND2_PROTOCOL.md"), \
        _sha_lf(BASE / "SHADOW_PILOT_ROUND2_PROTOCOL_V2.md")
    pf.check(6, "v1/v2 协议 hash 未变", v1 == V1_SHA and v2 == V2_SHA,
             f"v1={v1[:10]} v2={v2[:10]}")
    a1 = TASKS["A1"]
    v1_text = (BASE / "SHADOW_PILOT_ROUND2_PROTOCOL.md").read_text(encoding="utf-8")
    pf.check(7, "A1 题目/评分与冻结版逐字一致",
             PROTOCOL_SHA256 == V1_SHA and "PMID 41657283" in a1["question"]
             and "PMID 41657283" in v1_text, "ok")

    # ---- 19：key 存在性（纯静态，先做，便于 8/9 的前置判断）----
    keys = {v: _key_present(v) for v in (PT.ENV_ANTHROPIC_KEY, PT.ENV_DEEPSEEK_KEY)}
    pf.check(19, "API key 仅检查存在性（绝不打印值）", all(keys.values()),
             {k: ("present" if ok else "MISSING") for k, ok in keys.items()})

    # ---- 导入顺序防御：必须在包装前 ----
    try:
        PT.assert_import_order_clean()
        order_ok = True
    except Exception as e:
        order_ok = False
        pf.check(11, "导入顺序干净（ssc_a1/ssc_skill_agent 未提前导入）", False, e)

    if pf.side_effects_blocked or not order_ok:
        for cid, name in ((8, "Planner/Verifier 标准模式"), (9, "Executor/Claim = flash"),
                          (10, "thinking disabled"), (11, "四角色经 GatedModel"),
                          (12, "max_retries=0"), (13, "timeout 有限"),
                          (15, "dry-run 最坏费用 ≤ $1.50"), (16, "无历史未结算 reservation"),
                          (17, "唯一 run_id"), (18, "输出目录无覆盖风险")):
            if not any(r["check_id"] == cid for r in pf.rows):
                pf.skip(cid, name, "前序检查已失败，跳过有副作用的检查")
        pf.check(14, "price_config_version 正确",
                 PR.PRICE_TABLE_VERSION == "2026-07-20.1", PR.PRICE_TABLE_VERSION)
        return finish(pf, None)

    # ---- 8-13：构造客户端（**只构造，不调用**）----
    ledger = BASE / "pilot" / "round2_results" / "stage1_ledger.jsonl"
    gate = HG.HardBudgetGate(stage="stage1", ledger_path=ledger, **RUN.LIMITS)
    os.environ.setdefault(HG.ENV_PAID, "1")
    os.environ.setdefault(HG.ENV_CONFIRM, "stage1")
    try:
        roles, runconf = PT.build_pilot_roles(gate, anthropic_model=RUN.ANTHROPIC_MODEL,
                                              deepseek_model=RUN.DEEPSEEK_MODEL)
    except Exception as e:
        pf.check(8, "四角色客户端构造成功", False, f"{type(e).__name__}: {e}")
        return finish(pf, None)

    b = runconf["anthropic_billing"]["planner"]
    pf.check(8, "Planner/Verifier = claude-opus-4-8 标准/global",
             b["resolved_speed"] == "standard" and b["resolved_inference_geo"] == "global",
             f"speed={b['resolved_speed']} geo={b['resolved_inference_geo']}")
    pf.check(9, "Executor/Claim = deepseek-v4-flash",
             runconf["deepseek_model"] == PT.PINNED_DEEPSEEK, runconf["deepseek_model"])
    ok10 = all(_safe(PT.assert_deepseek_nonthinking, r, roles[r], RUN.DEEPSEEK_MODEL)
               for r in PT.DEEPSEEK_ROLES)
    pf.check(10, "两个 DeepSeek 角色 thinking=disabled", ok10,
             json.dumps(PT.read_extra_body(roles["executor"]), ensure_ascii=False))

    # 11：替换入口 → 首次导入 → 逐项身份断言
    import ssc_pi_agent as P
    P.judge_llm = roles["planner"]         # 兜底入口；Verifier 走显式注入，不复用此对象
    P.deepseek_llm_pro = roles["executor"]
    neutralized = PT.neutralize_unused_paid_clients(gate, approved=roles)
    try:
        names = PT.assert_bindings_after_import(roles, gate)
        PT.assert_no_raw_paid_client_reachable(approved=roles)
        assert roles["planner"] is not roles["verifier"]
        assert object.__getattribute__(roles["planner"], "_role") == "planner"
        assert object.__getattribute__(roles["verifier"], "_role") == "verifier"
        pf.check(11, "六绑定身份 + Planner/Verifier 独立 wrapper", True,
                 f"{len(names)} 绑定；中和 {len(neutralized)} 个；"
                 f"planner/verifier 为不同对象")
    except Exception as e:
        pf.check(11, "六绑定身份 + Planner/Verifier 独立 wrapper", False, e)

    ok12 = ok13 = True
    tmax = {}
    for r, m in roles.items():
        t = PT.inspect_transport(m)
        ok12 &= (t["max_retries"] == 0)
        to = t["timeout"] if t["timeout"] is not None else t["request_timeout"]
        ok13 &= isinstance(to, (int, float)) and 0 < to < float("inf")
        tmax[r] = t["max_tokens"]
    pf.check(12, "四角色 max_retries=0", ok12)
    pf.check(13, "四角色 timeout 有限", ok13, json.dumps(PT.TIMEOUTS))
    pf.check(14, "price_config_version 正确",
             PR.PRICE_TABLE_VERSION == "2026-07-20.1", PR.PRICE_TABLE_VERSION)

    from pilot.budget_precheck import precheck
    pc = precheck("A1")
    pf.check(15, "A1 dry-run 最坏费用 ≤ $1.50", pc["within_task_cap"],
             f"${pc['task_worst_usd']:.4f} / ${pc['task_cap_usd']}")
    open_res = gate.ledger.open_reservations()
    pf.check(16, "无历史未结算 reservation 影响本次额度", not open_res, f"{len(open_res)} 条")

    run_id = f"A1_{uuid.uuid4().hex[:12]}"
    out_dir = BASE / "pilot" / "round2_results"
    pf.check(17, "使用新的唯一 run_id", not list(out_dir.glob(f"*{run_id}*")), run_id)
    pf.check(18, "输出目录无同名覆盖风险",
             not (out_dir / f"A1_result_{run_id}.json").exists(), out_dir.name)
    return finish(pf, run_id, {"run_config": _sanitize(runconf), "precheck": pc,
                               "max_tokens_by_role": tmax})


def _safe(fn, *a):
    try:
        return bool(fn(*a))
    except Exception as e:
        print("    ", type(e).__name__, str(e)[:120])
        return False


def _sanitize(obj):
    """落盘前再过一道：只允许布尔/模型ID/安全配置，任何疑似密钥字段一律丢弃。"""
    banned = ("key", "token", "secret", "authorization", "cookie", "password")
    if isinstance(obj, dict):
        return {k: ("[DROPPED]" if any(b in str(k).lower() for b in banned)
                    else _sanitize(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, str) and len(obj) > 200:
        return obj[:200] + "…"
    return obj


def finish(pf, run_id, extra=None):
    out = {"preflight_version": "A.6.3.2", "dry_run": True, "real_api_calls": 0,
           "run_id": run_id, "checks": pf.rows}
    if extra:
        out.update(_sanitize(extra))
    p = BASE / "pilot" / "round2_results" / "A1_preflight.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n" + "=" * 62)
    if pf.failed:
        print(f"运行前检查未通过（{len(pf.failed)} 项）→ 停止，不运行 A1")
        for r in pf.failed:
            print(f"  {r['check_id']:>2}. {r['name']}: {r['detail'][:120]}")
        return 1
    print(f"19 项全部通过。run_id={run_id}（**未运行 A1**，需人工批准）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="默认且唯一模式：preflight 永不调用模型")
    ap.parse_args()
    sys.exit(run(dry_run=True))
