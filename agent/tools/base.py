"""Tool interface.

A tool is a name, a description the model reads, a Pydantic model for its arguments
(which doubles as the JSON schema sent to the model), a permission tier, and `run()`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ValidationError

from rag.retrieval import Hit


class Tier(StrEnum):
    READ = "read"  # runs automatically
    WRITE = "write"  # changes stored state; needs the user's approval
    NOTIFY = "notify"  # contacts other people; needs the user's approval


class ToolError(Exception):
    """An expected failure the model should see and can recover from (bad date, rate limit)."""


class DiscordGateway(Protocol):
    def fetch_messages(self, channel_id: str, limit: int) -> list[dict]: ...

    def post_message(self, channel_id: str, text: str) -> str: ...


@dataclass
class ToolContext:
    """Everything a tool may touch. Tools never receive a user id from the model:
    they act only for `user_id`, the person who sent the message."""

    user_id: str
    thread_id: str
    channel_id: str
    now: Callable[[], datetime]
    store: Any
    gateway: DiscordGateway
    services: dict[str, Any] = field(default_factory=dict)
    sources: list[Hit] = field(default_factory=list)  # citable chunks retrieved this run


@dataclass
class ToolResult:
    content: str  # what the model sees
    data: dict[str, Any] = field(default_factory=dict)  # structured, for traces and evals


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]
    tier: ClassVar[Tier] = Tier.READ

    def schema(self) -> dict:
        params = self.Args.model_json_schema()
        params.pop("title", None)
        return {"name": self.name, "description": self.description, "parameters": params}

    def parse(self, raw: dict) -> BaseModel:
        return self.Args.model_validate(raw)

    def describe(self, args: BaseModel, ctx: ToolContext) -> str:
        """One line shown to the user when asking for approval."""
        return f"{self.name}({args.model_dump_json()})"

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


def format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "arguments"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
