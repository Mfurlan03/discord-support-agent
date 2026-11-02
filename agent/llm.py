"""LLM interface and provider adapters.

The agent loop speaks one provider-neutral message format:

    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "..." | None, "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}

Each adapter translates that format to its provider's API and back, so the loop never
changes when the model or provider does.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(RuntimeError):
    """Provider-side failure (quota, rate limit, network). Not retried by the loop."""


class LLM(Protocol):
    model: str

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


def assistant_message(resp: LLMResponse) -> dict:
    return {"role": "assistant", "content": resp.text, "tool_calls": resp.tool_calls}


# ---------------------------------------------------------------- OpenAI-compatible


class OpenAICompatLLM:
    """OpenAI Chat Completions tool calling. Works with OpenAI, OpenRouter, vLLM, Ollama..."""

    def __init__(self, model: str, api_key: str, base_url: str | None = None, max_tokens: int = 800):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.model = model
        self.max_tokens = max_tokens

    @staticmethod
    def to_openai(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content"),
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                            }
                            for c in m["tool_calls"]
                        ],
                    }
                )
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": m["role"], "content": m.get("content") or ""})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        from openai import APIConnectionError, APIStatusError

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=self.to_openai(system, messages),
                tools=[{"type": "function", "function": t} for t in tools] or None,
                temperature=0.2,
                max_tokens=self.max_tokens,
            )
        except (APIStatusError, APIConnectionError) as e:
            raise LLMError(str(e)[:300]) from e
        msg = resp.choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"__invalid_json__": c.function.arguments}
            calls.append(ToolCall(c.id, c.function.name, args))
        usage = resp.usage
        return LLMResponse(
            msg.content, calls, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
        )


# ---------------------------------------------------------------- Anthropic


class AnthropicLLM:
    """Anthropic Messages API tool use (tool_use / tool_result content blocks)."""

    def __init__(self, model: str, api_key: str, max_tokens: int = 800):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    @staticmethod
    def to_anthropic(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                # consecutive tool results must share one user turn
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m.get("content") or ""})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        import anthropic

        try:
            resp = self.client.messages.create(
                model=self.model,
                system=system,
                max_tokens=self.max_tokens,
                temperature=0.2,
                messages=self.to_anthropic(messages),
                tools=[
                    {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools
                ],
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            raise LLMError(str(e)[:300]) from e
        text = "".join(b.text for b in resp.content if b.type == "text") or None
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return LLMResponse(text, calls, resp.usage.input_tokens, resp.usage.output_tokens)


# ---------------------------------------------------------------- scripted (tests)


class ScriptedLLM:
    """Deterministic stand-in for tests: replays responses, or calls a policy function.

    A policy receives (system, messages, tools) and returns an LLMResponse, which lets a
    test react to tool results (e.g. cite whatever search_docs returned).
    """

    def __init__(self, script: list[LLMResponse] | Callable[[str, list[dict], list[dict]], LLMResponse]):
        self.model = "scripted"
        self.script = script
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if callable(self.script):
            return self.script(system, messages, tools)
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of responses")
        return self.script.pop(0)


def build_llm(provider: str, model: str, api_key: str, base_url: str | None = None) -> LLM:
    if provider == "anthropic":
        return AnthropicLLM(model, api_key)
    if provider in {"openai", "openrouter", "openai-compatible"}:
        if provider == "openrouter" and not base_url:
            base_url = "https://openrouter.ai/api/v1"
        return OpenAICompatLLM(model, api_key, base_url)
    raise ValueError(f"unknown LLM provider {provider!r}")
