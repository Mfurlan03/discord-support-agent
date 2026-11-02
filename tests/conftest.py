import re

import pytest

from agent.config import ROOT, load_releases
from agent.guardrails import Budgets
from agent.llm import LLMResponse, ScriptedLLM, ToolCall
from agent.loop import Agent
from agent.state import Store
from agent.testing import FakeClock, FakeGateway
from agent.tools.actions import default_tools
from agent.tracing import Tracer
from rag.chunking import chunk_markdown
from rag.retrieval import BM25Retriever

DOCS = {
    "tutorial/query-params.md": "# Query Parameters { #query-parameters }\n\nFunction parameters that are not path "
    "parameters are interpreted as query parameters.\n\n## Defaults { #defaults }\n\nQuery parameters can have "
    "default values, like skip=0 and limit=10.",
    "tutorial/cors.md": "# CORS { #cors }\n\nUse CORSMiddleware to allow cross-origin requests from a frontend.\n\n"
    "## Use CORSMiddleware { #use-corsmiddleware }\n\nImport CORSMiddleware and add it with allow_origins.",
}


def make_chunks(docs=DOCS):
    out = []
    for path, text in docs.items():
        out.extend(chunk_markdown(path, text))
    return out


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def gateway():
    return FakeGateway({"chan-1": [{"author": "ana", "content": "Is Pydantic v1 still supported?"}]})


@pytest.fixture
def make_agent(clock, gateway):
    def _make(script, budgets=None, chunks=None):
        store = Store(":memory:", clock=clock)
        services = {
            "retriever": BM25Retriever(chunks or make_chunks()),
            "releases": load_releases(str(ROOT / "data" / "releases.json")),
            "helpers_channel_id": "helpers",
        }
        llm = ScriptedLLM(script)
        agent = Agent(llm, default_tools(), store, gateway, services, Tracer(), budgets or Budgets(), clock)
        return agent

    return _make


def call(name, _id="c1", **args):
    return LLMResponse(None, [ToolCall(_id, name, args)], 10, 5)


def say(text):
    return LLMResponse(text, [], 10, 5)


def last_tool_result(messages):
    return next(m["content"] for m in reversed(messages) if m["role"] == "tool")


def first_citation_for(messages, needle):
    """Return the [n] whose passage contains `needle` in the latest search result."""
    content = last_tool_result(messages)
    for block in content.split("---"):
        m = re.search(r"\[(\d+)\]", block)
        if m and needle.lower() in block.lower():
            return int(m.group(1))
    return None
