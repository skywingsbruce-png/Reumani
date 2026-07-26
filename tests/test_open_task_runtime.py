"""A.7.4.5 —— 有界开放任务运行时编排器测试（离线 fake 全链；无网络/LLM/账本）。"""
import pathlib

import pytest

from pilot.event_store import InMemoryEventStore
from pilot.open_task_runtime import OpenTaskRuntime, RuntimeDeps, ToolExecution, StepSpec
from pilot import demo_fixtures as demo
from pilot.demo_fixtures import run_demo, build_demo_deps

pytestmark = pytest.mark.unit
_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _types(store, run_id):
    return [e.event_type for e in store.list(run_id)]


def test_full_fake_chain_event_sequence():
    store = InMemoryEventStore()
    res = run_demo(store.append, run_id="r1")
    t = _types(store, "r1")
    assert t[:3] == ["run_created", "plan_ready", "step_started"]
    assert t[-1] == "run_completed"
    for et in ("attempt_authorized", "tool_started", "tool_returned", "observation_recorded",
               "evidence_accumulated", "step_satisfied", "step_insufficient", "synthesis_completed",
               "verification_completed", "claims_extracted", "claim_graph_completed",
               "shadow_completed"):
        assert et in t, et
    assert t.count("artifact_created") == 4
    assert res["open_reservations"] == 0 and res["conclusion"].causal_strength == "association"


def test_authorize_strictly_before_tool():
    store = InMemoryEventStore()
    run_demo(store.append, run_id="r1")
    evs = store.list("r1")
    auth = next(i for i, e in enumerate(evs) if e.event_type == "attempt_authorized")
    tool = next(i for i, e in enumerate(evs) if e.event_type == "tool_started")
    assert auth < tool


def test_criteria_met_means_no_second_tool_call_in_step():
    store = InMemoryEventStore()
    run_demo(store.append, run_id="r1")
    step1 = [e for e in store.list("r1") if e.step_id == 1]
    assert sum(1 for e in step1 if e.event_type == "tool_started") == 1   # 第一次即满足
    assert sum(1 for e in step1 if e.event_type == "attempt_authorized") == 1


def test_result_accumulate_settle_ordering():
    store = InMemoryEventStore()
    run_demo(store.append, run_id="r1")
    evs = store.list("r1")
    ret = next(i for i, e in enumerate(evs) if e.event_type == "tool_returned")
    acc = next(i for i, e in enumerate(evs) if e.event_type == "evidence_accumulated")
    sat = next(i for i, e in enumerate(evs) if e.event_type == "step_satisfied")
    assert ret < acc < sat


def test_zero_hits_step_insufficient():
    store = InMemoryEventStore()
    run_demo(store.append, run_id="r1")
    dl = [e for e in store.list("r1") if e.step_id == 2]
    assert any(e.event_type == "step_insufficient" for e in dl)
    assert any(e.event_type == "tool_returned" and e.status == "zero_hits" for e in dl)


def test_error_path_fails_step():
    store = InMemoryEventStore()

    def err_exec(step_id, tool, request_id):
        return ToolExecution(status="source_error", error_type="http",
                             accum_input={"retrieval_status": "source_error", "records": []})

    deps = RuntimeDeps(planner=lambda q: [StepSpec(1, "lit", "search_literature", 1)],
                       tool_executor=err_exec, synthesizer=demo.demo_synthesizer,
                       verifier=demo.demo_verifier, claim_extractor=demo.demo_claim_extractor,
                       claim_graph=demo.demo_claim_graph, shadow=demo.demo_shadow,
                       artifact_producer=demo.demo_artifact_producer, event_sink=store.append)
    res = OpenTaskRuntime(deps, run_id="rerr", question="q").run()
    t = _types(store, "rerr")
    assert "step_failed" in t and res["open_reservations"] == 0


def test_tool_exception_still_settles_reservation():
    store = InMemoryEventStore()

    def raising_exec(step_id, tool, request_id):
        raise RuntimeError("provider boom")

    deps = RuntimeDeps(planner=lambda q: [StepSpec(1, "lit", "search_literature", 1)],
                       tool_executor=raising_exec, synthesizer=demo.demo_synthesizer,
                       verifier=demo.demo_verifier, claim_extractor=demo.demo_claim_extractor,
                       claim_graph=demo.demo_claim_graph, shadow=demo.demo_shadow,
                       artifact_producer=demo.demo_artifact_producer, event_sink=store.append)
    res = OpenTaskRuntime(deps, run_id="rexc", question="q").run()
    assert res["open_reservations"] == 0
    assert any(e.event_type == "tool_returned" and e.status == "tool_error" for e in store.list("rexc"))


def test_cooperative_stop_before_any_tool():
    store = InMemoryEventStore()
    res = run_demo(store.append, run_id="rs", should_stop=lambda: True)
    t = _types(store, "rs")
    assert "run_stopped" in t and "tool_started" not in t and res["open_reservations"] == 0


def test_cooperative_stop_after_first_step():
    store = InMemoryEventStore()
    run_id = "rmid"

    def should_stop():
        return any(e.event_type == "step_satisfied" for e in store.list(run_id))

    res = run_demo(store.append, run_id=run_id, should_stop=should_stop)
    t = _types(store, run_id)
    assert t.count("tool_started") == 1                # 只文献步骤执行了工具
    assert "step_satisfied" in t and "run_stopped" in t
    assert "synthesis_completed" not in t and res["open_reservations"] == 0


def test_sequence_monotonic_and_unique_ids():
    store = InMemoryEventStore()
    run_demo(store.append, run_id="r1")
    evs = store.list("r1")
    assert [e.sequence for e in evs] == list(range(len(evs)))
    assert len({e.event_id for e in evs}) == len(evs)


def test_concurrent_runs_isolated():
    store = InMemoryEventStore()
    run_demo(store.append, run_id="rA")
    run_demo(store.append, run_id="rB")
    assert all(e.run_id == "rA" for e in store.list("rA"))
    assert all(e.run_id == "rB" for e in store.list("rB"))


def test_no_network_llm_or_ledger_imports():
    for mod in ("open_task_runtime.py", "runtime_events.py", "event_store.py", "demo_fixtures.py"):
        src = (_ROOT / "pilot" / mod).read_text(encoding="utf-8")
        for bad in ("import requests", "import httpx", "import urllib", "anthropic", "openai",
                    "ledger_integrity", "langchain"):
            assert bad not in src, (mod, bad)


def test_safe_payload_never_leaks_sensitive():
    store = InMemoryEventStore()
    run_demo(store.append, run_id="r1")
    for e in store.list("r1"):
        blob = e.model_dump_json().lower()
        for bad in ("prompt", "api_key", "authorization", "cookie", "\\.env", "patient"):
            assert bad not in e.safe_payload
