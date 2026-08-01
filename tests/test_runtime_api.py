"""A.7.4.5 —— 运行时 API + SSE 测试（Starlette）。无 starlette 的 CI unit job 会自动跳过。"""
import json

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
from starlette.testclient import TestClient          # noqa: E402

from pilot.runtime_api import create_app             # noqa: E402
from pilot.event_store import InMemoryEventStore     # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    return TestClient(create_app(store=InMemoryEventStore()))


def _create(client):
    r = client.post("/api/demo-runs", json={})
    assert r.status_code == 201 and r.json()["demo"] is True
    return r.json()["run_id"]


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["schema_version"] == "reumani-event-v1"


def test_create_demo_run_and_snapshot(client):
    run_id = _create(client)
    snap = client.get(f"/api/runs/{run_id}").json()
    assert snap["status"] == "finished" and snap["event_count"] == 30   # +3 stage-started events


def test_event_list_and_cursor(client):
    run_id = _create(client)
    allev = client.get(f"/api/runs/{run_id}/events").json()["events"]
    assert allev[0]["event_type"] == "run_created" and allev[-1]["event_type"] == "run_completed"
    after = client.get(f"/api/runs/{run_id}/events", params={"after": 0}).json()["events"]
    assert after[0]["sequence"] == 1


def _sse_events(body):
    return [json.loads(ln.split("data: ", 1)[1])["event_type"]
            for ln in body.splitlines() if ln.startswith("data: ")]


def test_sse_stream_terminates_at_run_completed(client):
    run_id = _create(client)
    with client.stream("GET", f"/api/runs/{run_id}/events/stream") as s:
        body = "".join(s.iter_text())
    events = _sse_events(body)
    assert events[0] == "run_created" and events[-1] == "run_completed"
    ids = [ln.split("id: ", 1)[1] for ln in body.splitlines() if ln.startswith("id: ")]
    assert ids == [str(i) for i in range(len(events))]        # 单调 id


def test_sse_last_event_id_no_duplicates(client):
    run_id = _create(client)
    with client.stream("GET", f"/api/runs/{run_id}/events/stream",
                       headers={"Last-Event-ID": "10"}) as s:
        body = "".join(s.iter_text())
    ids = [int(ln.split("id: ", 1)[1]) for ln in body.splitlines() if ln.startswith("id: ")]
    assert min(ids) == 11 and ids[-1] == 29                   # 重连不重复已应用事件（30 events）
    assert _sse_events(body)[-1] == "run_completed"


def test_stop_endpoint(client):
    run_id = _create(client)
    r = client.post(f"/api/runs/{run_id}/stop")
    assert r.status_code == 200 and r.json()["stop_requested"] is True


def test_invalid_run_id_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404
    assert client.get("/api/runs/does-not-exist/events").status_code == 404
    assert client.post("/api/runs/does-not-exist/stop").status_code == 404


def test_demo_run_ignores_arbitrary_payload(client):
    # 不接受任意任务/代码/路径：额外字段被忽略，仍只跑内置 demo
    r = client.post("/api/demo-runs", json={"cmd": "rm -rf /", "path": "/etc/passwd",
                                            "code": "import os"})
    assert r.status_code == 201
    run_id = r.json()["run_id"]
    evs = client.get(f"/api/runs/{run_id}/events").json()["events"]
    assert evs[1]["safe_payload"].get("step_count") == 2       # 固定的两步 demo


def test_real_demo_run_uses_real_components(client):
    r = client.post("/api/demo-runs", json={"real": True})
    assert r.status_code == 201 and r.json()["real"] is True
    run_id = r.json()["run_id"]
    assert run_id.startswith("real-")
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    types = [e["event_type"] for e in events]
    assert types[-1] == "run_completed"
    ev_counts = [e["safe_payload"].get("evidence_count", 0) for e in events
                 if e["event_type"] == "evidence_accumulated"]
    assert max(ev_counts) >= 1                                 # 真实证据卡进入事件流


def test_fake_canary_run_and_meta(client):
    r = client.post("/api/canary-runs", json={})
    assert r.status_code == 201 and r.json()["canary"] == "fake"
    run_id = r.json()["run_id"]
    assert run_id.startswith("canary-fake-")
    snap = client.get(f"/api/runs/{run_id}").json()
    meta = snap["canary"]
    assert meta["canary_kind"] == "fake"
    assert meta["model_calls"] == 3 and meta["usd_cost"] == 0.0    # zero-paid fake
    assert meta["calls_by_role"] == {"synthesizer": 1, "verifier": 1, "claim_extractor": 1}
    assert meta["causal_tier"] in ("association", "insufficient")   # capped, never causal
    types = [e["event_type"] for e in client.get(f"/api/runs/{run_id}/events").json()["events"]]
    for et in ("synthesis_completed", "verification_completed", "claims_extracted",
               "claim_graph_completed", "shadow_completed", "run_completed"):
        assert et in types                                         # SSE stage events reach the UI


def _hitl_start(client):
    rid = client.post("/api/hitl-runs").json()["run_id"]
    ctl = client.get(f"/api/runs/{rid}").json()["control"]
    return rid, ctl


def test_hitl_full_flow(client):
    rid, ctl = _hitl_start(client)
    assert rid.startswith("hitl-") and ctl["control_state"] == "awaiting_clarification"
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    r = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                    json={"expected_state_version": v, "idempotency_key": "a1", "selected_option_ids": ["skin"]})
    ctl = r.json()["control"]
    assert r.status_code == 200 and ctl["control_state"] == "awaiting_approval"
    apr, ah, v = ctl["pending"]["request_id"], ctl["pending"]["action_hash"], ctl["state_version"]
    rp = client.post(f"/api/runs/{rid}/pause", json={"idempotency_key": "p", "expected_state_version": v})
    v = rp.json()["control"]["state_version"]
    rr = client.post(f"/api/runs/{rid}/resume", json={"idempotency_key": "r", "expected_state_version": v})
    v = rr.json()["control"]["state_version"]
    ra = client.post(f"/api/runs/{rid}/approvals/{apr}/approve",
                     json={"expected_state_version": v, "idempotency_key": "ap", "action_hash": ah})
    assert ra.json()["control"]["control_state"] == "completed" and ra.json()["control"]["tool_calls"] == 1


def test_hitl_stale_version_returns_409(client):
    rid, ctl = _hitl_start(client)
    req = ctl["pending"]["request_id"]
    r = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                    json={"expected_state_version": 999, "idempotency_key": "x", "selected_option_ids": ["skin"]})
    assert r.status_code == 409 and r.json()["conflict"] is True


def test_hitl_unknown_field_rejected_400(client):
    rid, ctl = _hitl_start(client)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    r = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                    json={"expected_state_version": v, "idempotency_key": "x", "selected_option_ids": ["skin"],
                          "evil_cmd": "rm -rf /"})
    assert r.status_code == 400


def test_hitl_deny_branch_no_artifact(client):
    rid, ctl = _hitl_start(client)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    ctl = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                      json={"expected_state_version": v, "idempotency_key": "a", "selected_option_ids": ["lung"]}).json()["control"]
    apr, ah, v = ctl["pending"]["request_id"], ctl["pending"]["action_hash"], ctl["state_version"]
    rd = client.post(f"/api/runs/{rid}/approvals/{apr}/deny",
                     json={"expected_state_version": v, "idempotency_key": "d", "action_hash": ah})
    assert rd.json()["control"]["control_state"] == "stopped" and rd.json()["control"]["tool_calls"] == 0
    types = [e["event_type"] for e in client.get(f"/api/runs/{rid}/events").json()["events"]]
    assert "approval_denied" in types and "artifact_created" not in types


def test_hitl_idempotent_answer_no_duplicate(client):
    rid, ctl = _hitl_start(client)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    body = {"expected_state_version": v, "idempotency_key": "same", "selected_option_ids": ["skin"]}
    n1 = client.post(f"/api/runs/{rid}/clarifications/{req}/answer", json=body).json()["control"]["state_version"]
    before = len(client.get(f"/api/runs/{rid}/events").json()["events"])
    n2 = client.post(f"/api/runs/{rid}/clarifications/{req}/answer", json=body).json()["control"]["state_version"]
    after = len(client.get(f"/api/runs/{rid}/events").json()["events"])
    assert n1 == n2 and before == after                        # idempotent: no duplicate events


def test_hitl_stop_after_completed_conflicts(client):
    rid, ctl = _hitl_start(client)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    ctl = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                      json={"expected_state_version": v, "idempotency_key": "a", "selected_option_ids": ["both"]}).json()["control"]
    apr, ah, v = ctl["pending"]["request_id"], ctl["pending"]["action_hash"], ctl["state_version"]
    ctl = client.post(f"/api/runs/{rid}/approvals/{apr}/approve",
                      json={"expected_state_version": v, "idempotency_key": "ap", "action_hash": ah}).json()["control"]
    rs = client.post(f"/api/runs/{rid}/stop",
                     json={"idempotency_key": "s", "expected_state_version": ctl["state_version"]})
    assert rs.status_code == 409                               # completed is immutable


def test_cooperative_stop_before_tool_when_delayed():
    # 带步进延迟 → 后台线程；创建后立即 stop → 不授权任何工具
    import time
    client = TestClient(create_app(store=InMemoryEventStore()))
    run_id = client.post("/api/demo-runs", json={"step_delay_ms": 300}).json()["run_id"]
    client.post(f"/api/runs/{run_id}/stop")
    deadline = time.time() + 10
    while time.time() < deadline:
        snap = client.get(f"/api/runs/{run_id}").json()
        if snap["status"] in ("stopped", "finished", "failed"):
            break
        time.sleep(0.1)
    types = [e["event_type"] for e in client.get(f"/api/runs/{run_id}/events").json()["events"]]
    assert "run_stopped" in types and "tool_started" not in types


# ---- A.7.5.1 §2：通过 HTTP 端点验证"服务重启"后的持久化恢复（新 app + 同一持久化目录） ----
def test_hitl_api_recovers_across_restart(tmp_path):
    from pilot.event_store import JsonlEventStore
    # 进程A：起 run，答澄清 → awaiting_approval
    app1 = create_app(store=JsonlEventStore(str(tmp_path)))
    c1 = TestClient(app1)
    rid, ctl = _hitl_start(c1)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    ctl = c1.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                  json={"expected_state_version": v, "idempotency_key": "a", "selected_option_ids": ["skin"]}).json()["control"]
    apr, ah, v = ctl["pending"]["request_id"], ctl["pending"]["action_hash"], ctl["state_version"]

    # 进程B："重启"= 全新 app + 全新 RunManager，读同一持久化目录（不复用旧对象）
    c2 = TestClient(create_app(store=JsonlEventStore(str(tmp_path))))
    snap = c2.get(f"/api/runs/{rid}").json()
    assert snap["control"]["control_state"] == "awaiting_approval"          # 恢复出等待审批
    ra = c2.post(f"/api/runs/{rid}/approvals/{apr}/approve",
                 json={"expected_state_version": v, "idempotency_key": "ap", "action_hash": ah})
    assert ra.json()["control"]["control_state"] == "completed" and ra.json()["control"]["tool_calls"] == 1
    # 重启后重放旧 idempotency_key → 不重复
    n = len(c2.get(f"/api/runs/{rid}/events").json()["events"])
    c2.post(f"/api/runs/{rid}/approvals/{apr}/approve",
            json={"expected_state_version": ra.json()["control"]["state_version"],
                  "idempotency_key": "ap", "action_hash": ah})
    assert len(c2.get(f"/api/runs/{rid}/events").json()["events"]) == n


# ---- A.7.5.3 §11：research run API（共用同一控制核心，不复制状态机） ----
def _research_start(client, **body):
    body.setdefault("executor_id", "fake-research-v1")
    r = client.post("/api/research-runs", json=body)
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    return rid, client.get(f"/api/runs/{rid}").json()["control"]


def _await_terminal(client, rid, timeout=20.0):
    """research run 异步执行：轮询到终态（不使用固定 sleep 断言时序）。"""
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        c = client.get(f"/api/runs/{rid}").json()["control"]
        if c["control_state"] in ("completed", "failed", "stopped"):
            return c
        _t.sleep(0.01)
    raise AssertionError(f"run {rid} did not reach a terminal state; last={c}")


def test_research_executors_listed(client):
    ex = client.get("/api/research-executors").json()["executors"]
    assert "fake-research-v1" in ex


def test_research_run_full_flow_zero_calls_before_approval(client):
    rid, ctl = _research_start(client)
    assert ctl["run_type"] == "research" and ctl["control_state"] == "awaiting_clarification"
    assert ctl["tool_calls"] == 0
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    ctl = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                      json={"expected_state_version": v, "idempotency_key": "a",
                            "selected_option_ids": ["strict_causal"]}).json()["control"]
    assert ctl["control_state"] == "awaiting_approval" and ctl["tool_calls"] == 0   # 批准前 0 调用
    p = ctl["pending"]
    assert p["executor_id"] == "fake-research-v1" and p["evidence_count"] == 3
    assert p["policy"]["allow_network"] is False and p["policy"]["allow_planner"] is False
    ra = client.post(f"/api/runs/{rid}/approvals/{p['request_id']}/approve",
                     json={"expected_state_version": ctl["state_version"], "idempotency_key": "ap",
                           "action_hash": p["action_hash"]})
    # A.7.5.3.1：Approval 立即返回 running，不等待 8 个阶段
    assert ra.json()["control"]["control_state"] == "running"
    c = _await_terminal(client, rid)
    assert c["control_state"] == "completed"
    assert c["research"]["stages_done"] == list(c["research"]["stages"])
    assert c["research"]["verifier_verdict"] == "insufficient_evidence"
    assert c["research"]["causal_tier"] == "insufficient_for_direct_causality"
    arts = client.get(f"/api/runs/{rid}/artifacts").json()
    assert arts["count"] == 1 and arts["artifacts"][0]["fixture"] is True
    types = [e["event_type"] for e in client.get(f"/api/runs/{rid}/events").json()["events"]]
    assert types.count("research_stage_completed") == 8 and types.count("artifact_created") == 1


def test_research_unknown_executor_rejected(client):
    r = client.post("/api/research-runs", json={"executor_id": "totally-unknown"})
    assert r.status_code == 400 and "未注册" in r.json()["error"]


def test_research_unknown_field_rejected(client):
    r = client.post("/api/research-runs", json={"executor_id": "fake-research-v1",
                                                "model_client": {"provider": "anthropic"}})
    assert r.status_code == 400                       # 客户端不得注入内部对象


def test_research_oversized_question_rejected(client):
    r = client.post("/api/research-runs", json={"executor_id": "fake-research-v1",
                                                "question": "x" * 5000})
    assert r.status_code == 400


def test_research_deny_produces_no_artifact(client):
    rid, ctl = _research_start(client)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    ctl = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                      json={"expected_state_version": v, "idempotency_key": "a",
                            "selected_option_ids": ["association_only"]}).json()["control"]
    p = ctl["pending"]
    c = client.post(f"/api/runs/{rid}/approvals/{p['request_id']}/deny",
                    json={"expected_state_version": ctl["state_version"], "idempotency_key": "d",
                          "action_hash": p["action_hash"]}).json()["control"]
    assert c["control_state"] == "stopped" and c["tool_calls"] == 0
    assert client.get(f"/api/runs/{rid}/artifacts").json()["count"] == 0


def test_serve_rejects_multi_worker():
    # 单进程单 worker 边界：多 worker 显式拒绝（内存锁不跨进程，不伪装跨进程原子）
    from pilot.runtime_api import serve
    with pytest.raises(ValueError):
        serve(workers=2)


# ---- A.7.5.1.1 §7：同 idempotency_key + 不同 payload → 冲突（不静默返回旧结果） ----
def test_hitl_same_key_different_payload_conflicts(client):
    rid, ctl = _hitl_start(client)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    r1 = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                     json={"expected_state_version": v, "idempotency_key": "K", "selected_option_ids": ["skin"]})
    assert r1.status_code == 200
    n = len(client.get(f"/api/runs/{rid}/events").json()["events"])
    # 同 key、不同 payload（lung）→ 409 冲突，且不产生新事件（无副作用）
    r2 = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                     json={"expected_state_version": v, "idempotency_key": "K", "selected_option_ids": ["lung"]})
    assert r2.status_code == 409
    assert len(client.get(f"/api/runs/{rid}/events").json()["events"]) == n


# ---- A.7.5.1.1 §10：SSE 断线重连（Last-Event-ID / cursor）只补发缺失事件，不重复 ----
def test_sse_cursor_reconnect_no_duplicate(client):
    rid, ctl = _hitl_start(client)
    req, v = ctl["pending"]["request_id"], ctl["state_version"]
    ctl = client.post(f"/api/runs/{rid}/clarifications/{req}/answer",
                      json={"expected_state_version": v, "idempotency_key": "a", "selected_option_ids": ["skin"]}).json()["control"]
    apr, ah, v = ctl["pending"]["request_id"], ctl["pending"]["action_hash"], ctl["state_version"]
    client.post(f"/api/runs/{rid}/approvals/{apr}/approve",
                json={"expected_state_version": v, "idempotency_key": "ap", "action_hash": ah})
    allev = client.get(f"/api/runs/{rid}/events").json()["events"]
    mid = allev[len(allev) // 2]["sequence"]
    # 重连：带 Last-Event-ID=mid → 只补发 seq>mid，直到 terminal 关闭
    with client.stream("GET", f"/api/runs/{rid}/events/stream",
                       headers={"last-event-id": str(mid)}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    ids = [int(l[4:]) for l in body.splitlines() if l.startswith("id: ")]
    assert ids == sorted(set(ids))                             # 单调、唯一（无重复）
    assert all(i > mid for i in ids)                           # 只补发缺失事件
    assert ids[-1] == allev[-1]["sequence"]                    # terminal 收尾，之后无 running
