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
    assert snap["status"] == "finished" and snap["event_count"] == 27


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
    assert min(ids) == 11 and ids[-1] == 26                   # 重连不重复已应用事件
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
