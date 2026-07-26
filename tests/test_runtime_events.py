"""A.7.4.5 —— 版本化运行时事件契约测试 + 跨语言一致性（Python ↔ TS 共享 JSON）。"""
import json
import pathlib

import pytest
from pydantic import ValidationError

from pilot.runtime_events import (EVENT_SCHEMA, EVENT_TYPES, SAFE_PAYLOAD_KEYS,
                                  RuntimeEvent, make_event, event_content_hash)

pytestmark = pytest.mark.unit
_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CONTRACT = _ROOT / "reumani_lab_ui" / "src" / "contracts" / "reumani-event-v1.json"


def test_make_event_valid_and_hashed():
    ev = make_event(run_id="r1", sequence=0, event_type="run_created", event_id="r1-0",
                    summary="x", clock=lambda: "2026-01-01T00:00:00")
    assert ev.schema_version == EVENT_SCHEMA and ev.content_hash == event_content_hash(ev)


def test_sequence_and_ids_required():
    with pytest.raises(ValidationError):
        make_event(run_id="r1", sequence=-1, event_type="run_created", event_id="e")
    with pytest.raises(ValidationError):
        make_event(run_id="", sequence=0, event_type="run_created", event_id="e")


def test_unknown_event_type_rejected():
    with pytest.raises(ValueError):
        make_event(run_id="r1", sequence=0, event_type="not_a_type", event_id="e")


def test_content_hash_tamper_detected():
    ev = make_event(run_id="r1", sequence=0, event_type="run_created", event_id="e")
    with pytest.raises(ValidationError):
        RuntimeEvent(**{**ev.model_dump(), "summary": "tampered"})   # hash 不再匹配


def test_safe_payload_whitelist_enforced():
    make_event(run_id="r1", sequence=0, event_type="tool_returned", event_id="e",
               safe_payload={"tool_name": "search_literature", "structured": True})
    for bad in ({"prompt": "..."}, {"api_key": "x"}, {"authorization": "Bearer x"},
                {"cookie": "s"}, {"patient": "n"}, {"raw_response": "..."}, {"unknown_key": 1}):
        with pytest.raises(ValidationError):
            make_event(run_id="r1", sequence=0, event_type="tool_returned", event_id="e",
                       safe_payload=bad)


def test_unknown_schema_version_rejected():
    ev = make_event(run_id="r1", sequence=0, event_type="run_created", event_id="e")
    with pytest.raises(ValidationError):
        RuntimeEvent(**{**ev.model_dump(), "schema_version": "reumani-event-v2"})


def test_json_round_trip():
    ev = make_event(run_id="r1", sequence=3, event_type="evidence_accumulated", event_id="e",
                    step_id=1, evidence_ids=["a", "b"], safe_payload={"evidence_count": 2})
    assert RuntimeEvent.model_validate_json(ev.model_dump_json()).content_hash == ev.content_hash


def test_python_ts_contract_consistency():
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert contract["schema_version"] == EVENT_SCHEMA
    assert set(contract["event_types"]) == set(EVENT_TYPES)
    assert set(contract["safe_payload_keys"]) == set(SAFE_PAYLOAD_KEYS)
    assert set(contract["terminal_event_types"]) == {"run_completed", "run_failed", "run_stopped"}
