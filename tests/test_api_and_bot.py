from datetime import timedelta

import pytest
from conftest import call, say
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture
def client_for(make_agent):
    def _c(script):
        agent = make_agent(script)
        return TestClient(create_app(lambda: agent)), agent

    return _c


def test_chat_and_trace_endpoints(client_for):
    c, _ = client_for([call("search_docs", query="cors"), say("Use CORSMiddleware [1].")])
    res = c.post("/chat", json={"text": "cors?"}).json()
    assert res["status"] == "answered" and res["verified"] and res["cited"] == [1]
    assert res["sources"][0]["url"].startswith("https://fastapi.tiangolo.com/")
    runs = c.get("/traces").json()
    assert runs[0]["run_id"] == res["run_id"] and runs[0]["status"] == "answered"
    detail = c.get(f"/traces/{res['run_id']}").json()
    assert detail["events"][0]["kind"] == "run_start" and detail["audit"][0]["tool"] == "search_docs"


def test_approval_flow_over_http(client_for, clock):
    when = (clock() + timedelta(days=1)).isoformat()
    c, agent = client_for([call("create_reminder", when=when, text="ship it"), say("Reminder set.")])
    res = c.post("/chat", json={"text": "remind me", "user_id": "u9"}).json()
    assert res["status"] == "awaiting_approval" and res["approval"]["tool"] == "create_reminder"
    assert c.post(f"/approvals/{res['run_id']}", json={"approved": True, "user_id": "intruder"}).status_code == 403
    done = c.post(f"/approvals/{res['run_id']}", json={"approved": True, "user_id": "u9"}).json()
    assert done["status"] == "answered" and len(agent.store.reminders("u9")) == 1
    assert c.post(f"/approvals/{res['run_id']}", json={"approved": True, "user_id": "u9"}).status_code == 404


def test_viewer_and_health(client_for):
    c, _ = client_for([])
    assert c.get("/healthz").json()["status"] == "ok"
    assert "Support agent" in c.get("/").text


def test_split_message_respects_limit():
    pytest.importorskip("discord")
    from bot.discord_app import split_message

    text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    parts = split_message(text, 500)
    assert all(len(p) <= 500 for p in parts) and "".join(parts) == text
    assert split_message("y" * 1200, 500) == ["y" * 500, "y" * 500, "y" * 200]
