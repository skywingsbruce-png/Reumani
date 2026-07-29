"""Reumani 运行时本地 API + SSE（A.7.4.5）。基于已安装的 Starlette（不引入 FastAPI 重依赖）。

只服务离线 DEMO run：`POST /api/demo-runs` 只能启动内置、明确标记 demo 的固件，不接受任意代码/路径/Shell。
默认绑定 127.0.0.1（见 serve()）；CORS 仅允许本地 UI origin；不返回密钥/.env/完整模型正文。
导入时零副作用：不建线程/网络/客户端；应用与后台线程只在显式调用时创建。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import uuid

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from pilot.event_store import InMemoryEventStore
from pilot.demo_fixtures import run_demo
from pilot.real_runtime import run_real_demo
from pilot.runtime_events import EVENT_SCHEMA

_TERMINAL = frozenset({"run_completed", "run_failed", "run_stopped"})
# 仅允许本地 UI origin（不默认开放公网）
_LOCAL_ORIGINS = [f"http://{h}:{p}" for h in ("127.0.0.1", "localhost")
                  for p in (5173, 5174, 5175, 5176, 5177, 4173)]


class _Handle:
    def __init__(self):
        self.stop_event = threading.Event()
        self.status = "running"
        self.thread = None


class RunManager:
    """管理离线 demo run 的执行与协作式停止（后台线程仅在 step_delay>0 时创建）。"""

    def __init__(self, store=None):
        self.store = store or InMemoryEventStore()
        self._handles: dict[str, _Handle] = {}
        self._meta: dict[str, dict] = {}          # run_id -> desensitized canary meta
        self._hitl: dict[str, object] = {}        # run_id -> HitlRun (human-in-the-loop)
        self._lock = threading.Lock()

    def start_hitl(self, exec_delay_ms: int = 0) -> str:
        """创建一个确定性 fake 人机协作运行（跑到第一个澄清点）。

        exec_delay_ms>0 → 审批后的 fake 动作分阶段、每段间可协作暂停（供"执行中 Pause"演示）；
        默认 0 → 同步执行（确定性）。
        """
        from pilot.hitl import HitlRun
        run_id = "hitl-" + uuid.uuid4().hex[:10]
        with self._lock:
            self._handles[run_id] = _Handle()
            run = HitlRun(run_id, self.store, exec_delay_ms=exec_delay_ms)
            self._hitl[run_id] = run
        run.start()
        return run_id

    def hitl(self, run_id: str):
        """返回内存中的 HITL run；若不在内存但持久化日志存在 → 用新对象**恢复**（新进程/新 RunManager）。"""
        with self._lock:
            hr = self._hitl.get(run_id)
            if hr is not None:
                return hr
        if str(run_id).startswith("hitl-") and self.store.exists(run_id):
            from pilot.hitl import HitlRun
            recovered = HitlRun.recover(run_id, self.store)   # 损坏/断裂 → RecoveryError（调用方转 409）
            with self._lock:
                self._hitl.setdefault(run_id, recovered)
                return self._hitl[run_id]
        return None

    def start_canary_fake(self, step_delay_ms: int = 0) -> str:
        """零付费 fake 金丝雀（含 UI SSE）。真实付费金丝雀永不经 HTTP 触发。"""
        from pilot.canary_a747 import run_fake_canary
        run_id = "canary-fake-" + uuid.uuid4().hex[:10]
        handle = _Handle()
        with self._lock:
            self._handles[run_id] = handle
        delay = max(0, int(step_delay_ms)) / 1000.0
        ledger = os.path.join(tempfile.gettempdir(), f"reumani_canary_{run_id}.ledger.jsonl")

        def sink(ev):
            self.store.append(ev)
            if delay:
                time.sleep(delay)

        def work():
            try:
                res = run_fake_canary(sink, run_id=run_id, ledger_path=ledger,
                                      should_stop=handle.stop_event.is_set)
                with self._lock:
                    self._meta[run_id] = res.get("meta", {})
                handle.status = "stopped" if res["stopped"] else ("failed" if res["failed"] else "finished")
            except Exception:                     # noqa: BLE001
                handle.status = "failed"
        if delay:
            handle.thread = threading.Thread(target=work, daemon=True)
            handle.thread.start()
        else:
            work()
        return run_id

    def load_prepared(self, run_id: str, events, meta: dict, status: str = "finished") -> None:
        """把一次已完成的（真实金丝雀）运行注入为只读可服务的 run。"""
        for ev in events:
            self.store.append(ev)
        h = _Handle()
        h.status = status
        with self._lock:
            self._handles[run_id] = h
            self._meta[run_id] = dict(meta or {})

    def meta(self, run_id: str) -> dict:
        with self._lock:
            return dict(self._meta.get(run_id, {}))

    def start_demo(self, step_delay_ms: int = 0, real: bool = False) -> str:
        run_id = ("real-" if real else "demo-") + uuid.uuid4().hex[:12]
        handle = _Handle()
        with self._lock:
            self._handles[run_id] = handle
        delay = max(0, int(step_delay_ms)) / 1000.0
        runner = run_real_demo if real else run_demo   # real: 接入真实确定性组件（离线冻结真实记录）

        def sink(ev):
            self.store.append(ev)
            if delay:
                time.sleep(delay)

        def work():
            try:
                res = runner(sink, run_id=run_id, should_stop=handle.stop_event.is_set)
                handle.status = "stopped" if res["stopped"] else ("failed" if res["failed"] else "finished")
            except Exception:                       # noqa: BLE001 — 后台运行失败不崩溃进程
                handle.status = "failed"

        if delay:
            handle.thread = threading.Thread(target=work, daemon=True)
            handle.thread.start()
        else:
            work()                                  # 同步执行（测试确定性）
        return run_id

    def stop(self, run_id: str) -> bool:
        with self._lock:
            h = self._handles.get(run_id)
        if h is None:
            return False
        h.stop_event.set()
        return True

    def status(self, run_id: str) -> str:
        with self._lock:
            h = self._handles.get(run_id)
        return h.status if h else "unknown"

    def exists(self, run_id: str) -> bool:
        return self.store.exists(run_id)


def _bad(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


def create_app(store=None, manager=None) -> Starlette:
    manager = manager or RunManager(store)
    store = manager.store

    async def health(request):
        return JSONResponse({"status": "ok", "schema_version": EVENT_SCHEMA})

    async def create_demo_run(request):
        # 只启动内置 demo（fake 或接入真实组件的 real）；忽略任意其它 body（不接受代码/路径/shell）
        step_delay_ms, real = 0, False
        try:
            body = await request.json()
            if isinstance(body, dict):
                step_delay_ms = int(body.get("step_delay_ms", 0) or 0)
                real = bool(body.get("real"))
        except Exception:                            # noqa: BLE001 — 无 body 也可
            step_delay_ms, real = 0, False
        step_delay_ms = max(0, min(step_delay_ms, 2000))   # 上限，避免滥用
        run_id = manager.start_demo(step_delay_ms, real=real)
        return JSONResponse({"run_id": run_id, "demo": True, "real": real}, status_code=201)

    async def create_canary_run(request):
        # 只启动**零付费 fake** 金丝雀；真实付费金丝雀永不经 HTTP 触发。
        step_delay_ms = 0
        try:
            body = await request.json()
            if isinstance(body, dict):
                step_delay_ms = int(body.get("step_delay_ms", 0) or 0)
        except Exception:                            # noqa: BLE001
            step_delay_ms = 0
        step_delay_ms = max(0, min(step_delay_ms, 2000))
        run_id = manager.start_canary_fake(step_delay_ms)
        return JSONResponse({"run_id": run_id, "canary": "fake"}, status_code=201)

    async def create_hitl_run(request):
        exec_delay_ms = 0
        try:
            body = await request.json()
            if isinstance(body, dict):
                exec_delay_ms = int(body.get("exec_delay_ms", 0) or 0)
        except Exception:                            # noqa: BLE001 — 无 body 也可
            exec_delay_ms = 0
        exec_delay_ms = max(0, min(exec_delay_ms, 5000))   # 上限，防滥用
        run_id = manager.start_hitl(exec_delay_ms=exec_delay_ms)
        return JSONResponse({"run_id": run_id, "hitl": True}, status_code=201)

    async def get_run(request):
        from pilot.hitl import RecoveryError
        run_id = request.path_params["run_id"]
        if not store.exists(run_id):
            return _bad("run not found", 404)
        events = store.list(run_id)
        try:
            hr = manager.hitl(run_id)
        except RecoveryError as e:
            return JSONResponse({"error": f"恢复失败，需人工审查：{e}", "conflict": True,
                                 "human_review": True}, status_code=409)
        status = hr.state if hr is not None else manager.status(run_id)
        return JSONResponse({"run_id": run_id, "status": status,
                             "event_count": len(events),
                             "last_sequence": events[-1].sequence if events else -1,
                             "schema_version": EVENT_SCHEMA,
                             "canary": manager.meta(run_id) or None,
                             "control": hr.snapshot() if hr is not None else None})

    # ---- human-in-the-loop write endpoints (strict bodies; 409 on conflict; idempotent) ----
    async def _hitl_call(request, fn):
        from pilot.hitl_contracts import (StaleState, IllegalTransition, ContractViolation)
        from pilot.hitl import RecoveryError
        from pydantic import ValidationError
        run_id = request.path_params["run_id"]
        try:
            hr = manager.hitl(run_id)                # 不在内存则从持久化日志恢复
        except RecoveryError as e:
            return JSONResponse({"error": f"恢复失败，需人工审查：{e}", "conflict": True,
                                 "human_review": True}, status_code=409)
        if hr is None:
            return _bad("hitl run not found", 404)
        try:
            body = await request.json()
        except Exception:                            # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            return _bad("body must be an object")
        try:
            snap = fn(hr, request.path_params, body)
        except ValidationError:
            return _bad("invalid request body (unknown/invalid fields)", 400)
        except (StaleState, IllegalTransition) as e:
            return JSONResponse({"error": str(e), "conflict": True,
                                 "control": hr.snapshot()}, status_code=409)
        except ContractViolation as e:
            return _bad(str(e), 409)
        return JSONResponse({"ok": True, "control": snap})

    def _answer(hr, params, body):
        from pilot.hitl_contracts import ClarificationAnswer
        ans = ClarificationAnswer.model_validate({**body, "request_id": params["request_id"]})
        return hr.answer_clarification(ans)

    def _approve(hr, params, body):
        from pilot.hitl_contracts import ApprovalDecision
        return hr.approve(ApprovalDecision.model_validate({**body, "request_id": params["request_id"]}))

    def _deny(hr, params, body):
        from pilot.hitl_contracts import ApprovalDecision
        return hr.deny(ApprovalDecision.model_validate({**body, "request_id": params["request_id"]}))

    def _control(method):
        def go(hr, params, body):
            from pilot.hitl_contracts import ControlAction
            a = ControlAction.model_validate(body)
            return getattr(hr, method)(a.idempotency_key, a.expected_state_version)
        return go

    async def answer_clarification(request):
        return await _hitl_call(request, _answer)

    async def approve(request):
        return await _hitl_call(request, _approve)

    async def deny(request):
        return await _hitl_call(request, _deny)

    async def pause_run(request):
        return await _hitl_call(request, _control("pause"))

    async def resume_run(request):
        return await _hitl_call(request, _control("resume"))

    async def get_events(request):
        run_id = request.path_params["run_id"]
        if not store.exists(run_id):
            return _bad("run not found", 404)
        try:
            after = int(request.query_params.get("after", "-1"))
        except ValueError:
            return _bad("invalid after cursor")
        events = store.list(run_id, after_sequence=after)
        return JSONResponse({"run_id": run_id, "events": [e.model_dump() for e in events]})

    async def stream_events(request):
        run_id = request.path_params["run_id"]
        if not store.exists(run_id):
            return _bad("run not found", 404)
        # Last-Event-ID（重连）或 cursor（首连）
        last = request.headers.get("last-event-id")
        try:
            cursor = int(last) if last is not None else int(request.query_params.get("cursor", "-1"))
        except ValueError:
            cursor = -1

        async def gen():
            nonlocal cursor
            polls = 0
            while polls < 600:                       # 安全上限（~60s）
                for e in store.list(run_id, after_sequence=cursor):
                    payload = e.model_dump_json()
                    # 只发 id + data（event_type 在 data JSON 内）→ 浏览器 EventSource.onmessage 收到每条事件；
                    # 若发 `event: <type>` 则会被路由到具名监听器，onmessage 不触发。
                    yield f"id: {e.sequence}\ndata: {payload}\n\n".encode()
                    cursor = e.sequence
                    if e.event_type in _TERMINAL:
                        return
                await asyncio.sleep(0.1)
                polls += 1

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def stop_run(request):
        from pilot.hitl import RecoveryError
        run_id = request.path_params["run_id"]
        if not store.exists(run_id):
            return _bad("run not found", 404)
        try:
            is_hitl = manager.hitl(run_id) is not None
        except RecoveryError as e:
            return JSONResponse({"error": f"恢复失败，需人工审查：{e}", "conflict": True,
                                 "human_review": True}, status_code=409)
        if is_hitl:                                  # HITL run → 契约化控制停止（幂等/版本校验）
            return await _hitl_call(request, _control("stop"))
        ok = manager.stop(run_id)                    # demo cooperative stop
        return JSONResponse({"run_id": run_id, "stop_requested": ok})

    routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/demo-runs", create_demo_run, methods=["POST"]),
        Route("/api/canary-runs", create_canary_run, methods=["POST"]),
        Route("/api/hitl-runs", create_hitl_run, methods=["POST"]),
        Route("/api/runs/{run_id}", get_run, methods=["GET"]),
        Route("/api/runs/{run_id}/events", get_events, methods=["GET"]),
        Route("/api/runs/{run_id}/events/stream", stream_events, methods=["GET"]),
        Route("/api/runs/{run_id}/stop", stop_run, methods=["POST"]),
        Route("/api/runs/{run_id}/pause", pause_run, methods=["POST"]),
        Route("/api/runs/{run_id}/resume", resume_run, methods=["POST"]),
        Route("/api/runs/{run_id}/clarifications/{request_id}/answer", answer_clarification, methods=["POST"]),
        Route("/api/runs/{run_id}/approvals/{request_id}/approve", approve, methods=["POST"]),
        Route("/api/runs/{run_id}/approvals/{request_id}/deny", deny, methods=["POST"]),
    ]
    middleware = [Middleware(CORSMiddleware, allow_origins=_LOCAL_ORIGINS,
                             allow_methods=["GET", "POST"], allow_headers=["*"])]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.manager = manager
    return app


def _durable_store():
    """持久化事件存储（append-only JSONL）；服务重启后可从同一目录恢复 HITL 控制状态。"""
    from pilot.event_store import JsonlEventStore
    root = os.environ.get("REUMANI_RUNTIME_DIR") or os.path.join(tempfile.gettempdir(),
                                                                  "reumani_runtime")
    return JsonlEventStore(root)


def serve(host: str = "127.0.0.1", port: int = 8799, workers: int = 1):   # pragma: no cover - 本地手动启动
    """本地启动（仅 127.0.0.1；不默认公网）。

    **单进程、单 worker** 边界：内存锁只在进程内原子。多 worker 会各持独立内存态，
    无法跨进程原子 → 显式拒绝（不伪装跨进程原子）。持久化用 JSONL 支持"重启恢复"。
    """
    if int(workers) != 1:
        raise ValueError("HITL 控制仅支持单进程单 worker（内存锁不跨进程）；拒绝 workers != 1")
    import uvicorn
    app = create_app(store=_durable_store())
    uvicorn.run(app, host=host, port=port, workers=1, log_level="info")


__all__ = ["create_app", "RunManager", "serve"]
