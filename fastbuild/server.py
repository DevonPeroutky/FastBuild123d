"""FastAPI server: static page, /ws endpoint, file watcher, build scheduling."""

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from watchfiles import awatch

from fastbuild.builder import BuildFailure, BuildOutput, build_part
from fastbuild.buildloop import BuildQueue, BuildRequest, TrailingDebounce
from fastbuild.errors import Error
from fastbuild.harness import load_model
from fastbuild.protocol import (
    BuildError,
    BuildOk,
    Building,
    Init,
    ModelUpdate,
    SelectPart,
    ServerMessage,
    SetParam,
    encode,
    parse_client,
)
from fastbuild.session import (
    SessionState,
    init_message,
    initial_state,
    migrate,
    select_part,
    set_value,
)

log = logging.getLogger("fastbuild")

STATIC_DIR = Path(__file__).parent / "static"


class ViewerServer:
    def __init__(
        self,
        model_path: Path,
        *,
        drag_debounce_s: float = 0.2,
        watch_debounce_ms: int = 100,
    ) -> None:
        self.model_path = model_path
        self.watch_debounce_ms = watch_debounce_ms
        self.state: SessionState | None = None
        self.load_error: str | None = None
        self.last_good: dict[str, Mapping[str, Any]] = {}
        self.last_status: ServerMessage | None = None
        self.clients: set[WebSocket] = set()
        self.queue = BuildQueue(self._run_build)
        self.drag = TrailingDebounce(drag_debounce_s, self.submit_selected)
        self._stop = asyncio.Event()
        self._watcher: asyncio.Task | None = None

    async def startup(self, watch: bool) -> None:
        loaded = await asyncio.to_thread(load_model, self.model_path)
        if isinstance(loaded, Error):
            self.load_error = loaded.message
            self.last_status = BuildError(error=loaded.message)
        else:
            self.state = initial_state(loaded.app)
            self.submit_selected()
        if watch:
            self._watcher = asyncio.get_running_loop().create_task(self._watch())

    async def shutdown(self) -> None:
        self._stop.set()
        self.drag.cancel()
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher

    def submit_selected(self) -> None:
        if self.state is None:
            return
        self.queue.submit(BuildRequest(
            part=self.state.selected,
            values=self.state.values[self.state.selected],
        ))

    async def _run_build(self, request: BuildRequest) -> None:
        if self.state is None or request.part not in self.state.parts:
            return
        part_def = self.state.parts[request.part]
        await self.broadcast(Building())
        result = await asyncio.to_thread(build_part, part_def, request.values)
        match result:
            case BuildOutput(part=part, shapes=shapes, timings=timings):
                self.last_good[part] = shapes
                await self.broadcast(ModelUpdate(part=part, shapes=shapes))
                self.last_status = BuildOk(timings=timings)
                await self.broadcast(self.last_status)
            case BuildFailure(traceback=tb):
                self.last_status = BuildError(error=tb)
                await self.broadcast(self.last_status)

    async def reload(self) -> None:
        loaded = await asyncio.to_thread(load_model, self.model_path)
        if isinstance(loaded, Error):
            self.last_status = BuildError(error=loaded.message)
            await self.broadcast(self.last_status)
            return
        self.load_error = None
        self.state = (
            initial_state(loaded.app)
            if self.state is None
            else migrate(self.state, loaded.app)
        )
        await self.broadcast(init_message(self.state))
        self.submit_selected()

    async def _watch(self) -> None:
        async for _changes in awatch(
            self.model_path,
            debounce=self.watch_debounce_ms,
            step=50,
            stop_event=self._stop,
        ):
            await self.reload()

    async def broadcast(self, msg: ServerMessage) -> None:
        text = json.dumps(encode(msg))
        dead = []
        for client in self.clients:
            try:
                await client.send_text(text)
            except Exception:
                dead.append(client)
        for client in dead:
            self.clients.discard(client)

    def current_init(self) -> Init:
        if self.state is None:
            return Init(parts=(), selected="")
        return init_message(self.state)

    async def handle_client(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        try:
            await ws.send_text(json.dumps(encode(self.current_init())))
            if self.state is not None and self.state.selected in self.last_good:
                update = ModelUpdate(
                    part=self.state.selected,
                    shapes=self.last_good[self.state.selected],
                )
                await ws.send_text(json.dumps(encode(update)))
            if self.last_status is not None:
                await ws.send_text(json.dumps(encode(self.last_status)))
            while True:
                data = await ws.receive_json()
                await self._handle_message(ws, data)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.clients.discard(ws)

    async def _handle_message(self, ws: WebSocket, data: Any) -> None:
        msg = parse_client(data)
        if isinstance(msg, Error):
            log.warning("bad client message: %s", msg.message)
            return
        if self.state is None:
            return
        match msg:
            case SetParam(part=part, name=name, value=value, final=final):
                new_state = set_value(self.state, part, name, value)
                if isinstance(new_state, Error):
                    log.warning("rejected set_param: %s", new_state.message)
                    return
                self.state = new_state
                if part != self.state.selected:
                    return
                if final:
                    self.drag.flush()
                else:
                    self.drag.touch()
            case SelectPart(part=part):
                new_state = select_part(self.state, part)
                if isinstance(new_state, Error):
                    log.warning("rejected select_part: %s", new_state.message)
                    return
                self.state = new_state
                await self.broadcast(init_message(self.state))
                if part in self.last_good:
                    await self.broadcast(ModelUpdate(part=part, shapes=self.last_good[part]))
                else:
                    self.submit_selected()


def create_app(
    model_path: Path,
    *,
    watch: bool = True,
    drag_debounce_s: float = 0.2,
    watch_debounce_ms: int = 100,
) -> FastAPI:
    server = ViewerServer(
        model_path,
        drag_debounce_s=drag_debounce_s,
        watch_debounce_ms=watch_debounce_ms,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        await server.startup(watch)
        yield
        await server.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.server = server
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await server.handle_client(ws)

    return app
