"""Reumani 运行时本地 API + SSE（A.7.4.5）。基于已安装的 Starlette（不引入 FastAPI 重依赖）。

只服务离线 DEMO run：`POST /api/demo-runs` 只能启动内置、明确标记 demo 的固件，不接受任意代码/路径/Shell。
默认绑定 127.0.0.1（见 serve()）；CORS 仅允许本地 UI origin；不返回密钥/.env/完整模型正文。
导入时零副作用：不建线程/网络/客户端；应用与后台线程只在显式调用时创建。
"""

from __future__ import annotations

import asyncio
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
        self._lock = threading.Lock()

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

    async def get_run(request):
        run_id = request.path_params["run_id"]
        if not store.exists(run_id):
            return _bad("run not found", 404)
        events = store.list(run_id)
        return JSONResponse({"run_id": run_id, "status": manager.status(run_id),
                             "event_count": len(events),
                             "last_sequence": events[-1].sequence if events else -1,
                             "schema_version": EVENT_SCHEMA})

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
        run_id = request.path_params["run_id"]
        if not store.exists(run_id):
            return _bad("run not found", 404)
        ok = manager.stop(run_id)
        return JSONResponse({"run_id": run_id, "stop_requested": ok})

    routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/demo-runs", create_demo_run, methods=["POST"]),
        Route("/api/runs/{run_id}", get_run, methods=["GET"]),
        Route("/api/runs/{run_id}/events", get_events, methods=["GET"]),
        Route("/api/runs/{run_id}/events/stream", stream_events, methods=["GET"]),
        Route("/api/runs/{run_id}/stop", stop_run, methods=["POST"]),
    ]
    middleware = [Middleware(CORSMiddleware, allow_origins=_LOCAL_ORIGINS,
                             allow_methods=["GET", "POST"], allow_headers=["*"])]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.manager = manager
    return app


def serve(host: str = "127.0.0.1", port: int = 8799):   # pragma: no cover - 本地手动启动
    """本地启动（仅 127.0.0.1；不默认公网）。"""
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


__all__ = ["create_app", "RunManager", "serve"]
