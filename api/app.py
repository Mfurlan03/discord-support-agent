"""HTTP API + trace viewer.

Runs inside the Discord bot process (sharing its agent), or standalone without Discord:

    uvicorn api.app:app            # standalone: chat over HTTP, local fake Discord
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.loop import Agent, AgentResult

STATIC = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    thread_id: str = "demo-thread"
    user_id: str = "demo-user"
    channel_id: str = "demo-channel"


class ApprovalDecision(BaseModel):
    approved: bool
    user_id: str = "demo-user"


def _result(res: AgentResult) -> dict:
    return {
        "status": res.status,
        "text": res.text,
        "run_id": res.run_id,
        "verified": res.verified,
        "cited": res.cited,
        "sources": [{"n": i + 1, "chunk_id": s.chunk_id, "url": s.url} for i, s in enumerate(res.sources)],
        "approval": asdict(res.approval) if res.approval else None,
    }


def create_app(get_agent: Callable[[], Agent | None], record_user_messages: bool = False) -> FastAPI:
    app = FastAPI(title="Discord Support Agent", version="0.1.0")

    def agent() -> Agent:
        a = get_agent()
        if a is None:
            raise HTTPException(503, "agent is starting")
        return a

    @app.get("/healthz")
    def healthz():
        a = get_agent()
        return {"status": "ok" if a else "starting", "model": a.llm.model if a else None}

    @app.post("/chat")
    def chat(req: ChatRequest):
        a = agent()
        if record_user_messages:  # standalone mode: give read_thread something to read
            a.gateway.channels.setdefault(req.channel_id, []).append({"author": req.user_id, "content": req.text})
        return _result(a.handle_message(req.thread_id, req.user_id, req.channel_id, req.text))

    @app.post("/approvals/{run_id}")
    def approve(run_id: str, d: ApprovalDecision):
        try:
            return _result(agent().resolve_approval(run_id, d.approved, d.user_id))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e

    @app.get("/traces")
    def traces(n: int = 50):
        return agent().tracer.recent(n)

    @app.get("/traces/{run_id}")
    def trace(run_id: str):
        events = agent().tracer.get(run_id)
        if not events:
            raise HTTPException(404, "unknown run")
        return {"run_id": run_id, "events": events, "audit": agent().store.audit_log(run_id)}

    @app.get("/")
    def viewer():
        return FileResponse(STATIC / "traces.html")

    return app


def _standalone() -> FastAPI:
    from agent.config import Settings, build_agent
    from agent.testing import FakeGateway

    holder: dict[str, Agent] = {}

    def get():
        if "agent" not in holder:
            holder["agent"] = build_agent(Settings.from_env(), FakeGateway())
        return holder["agent"]

    return create_app(get, record_user_messages=True)


app = _standalone()
