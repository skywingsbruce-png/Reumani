"""A.7.4.5 —— 事件存储测试（in-memory + JSONL；单调、唯一、cursor 重放、run 隔离、路径安全）。"""
import pytest

from pilot.runtime_events import make_event
from pilot.event_store import InMemoryEventStore, JsonlEventStore

pytestmark = pytest.mark.unit


def _ev(run_id, seq, etype="run_created"):
    return make_event(run_id=run_id, sequence=seq, event_type=etype, event_id=f"{run_id}-{seq}")


@pytest.fixture(params=["mem", "jsonl"])
def store(request, tmp_path):
    return InMemoryEventStore() if request.param == "mem" else JsonlEventStore(str(tmp_path / "runs"))


def test_append_and_list(store):
    store.append(_ev("r1", 0)); store.append(_ev("r1", 1, "plan_ready"))
    assert [e.sequence for e in store.list("r1")] == [0, 1]
    assert store.exists("r1") and not store.exists("r2")


def test_monotonic_sequence_enforced(store):
    store.append(_ev("r1", 0))
    with pytest.raises(ValueError):
        store.append(_ev("r1", 5))              # 非 +1


def test_duplicate_event_id_rejected(store):
    store.append(_ev("r1", 0))
    dup = make_event(run_id="r1", sequence=1, event_type="plan_ready", event_id="r1-0")
    with pytest.raises(ValueError):
        store.append(dup)


def test_cursor_replay(store):
    for i, t in enumerate(("run_created", "plan_ready", "step_started")):
        store.append(_ev("r1", i, t))
    assert [e.sequence for e in store.list("r1", after_sequence=0)] == [1, 2]
    assert [e.sequence for e in store.list("r1", after_sequence=2)] == []


def test_runs_isolated(store):
    store.append(_ev("r1", 0)); store.append(_ev("r2", 0))
    assert [e.run_id for e in store.list("r1")] == ["r1"]
    assert [e.run_id for e in store.list("r2")] == ["r2"]


def test_path_traversal_run_id_rejected(store):
    with pytest.raises(ValueError):
        store.list("../etc/passwd")


def test_jsonl_is_append_only(tmp_path):
    root = tmp_path / "runs"
    s = JsonlEventStore(str(root))
    s.append(_ev("r1", 0)); s.append(_ev("r1", 1, "plan_ready"))
    p = root / "r1.jsonl"
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2
    # 再开一个 store 指向同目录 → 续读已有事件
    s2 = JsonlEventStore(str(root))
    assert [e.sequence for e in s2.list("r1")] == [0, 1]
