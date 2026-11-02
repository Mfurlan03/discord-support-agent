"""Tools that touch Discord or stored state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..guardrails import wrap_untrusted
from .base import Tier, Tool, ToolContext, ToolError, ToolResult


class ReadThreadArgs(BaseModel):
    limit: int = Field(40, ge=5, le=100, description="How many recent messages to read")


class ReadThread(Tool):
    name = "read_thread"
    description = (
        "Read the most recent messages in the current Discord channel or thread, oldest first. "
        "Use when the user asks to summarize or catch up on a discussion."
    )
    Args = ReadThreadArgs
    tier = Tier.READ

    def run(self, args: ReadThreadArgs, ctx: ToolContext) -> ToolResult:
        msgs = ctx.gateway.fetch_messages(ctx.channel_id, args.limit)
        if not msgs:
            return ToolResult("The channel has no messages.", {"count": 0})
        body = "\n".join(f"{m['author']}: {m['content']}" for m in msgs)
        return ToolResult(wrap_untrusted("discord-messages", body), {"count": len(msgs)})


MAX_REMINDER_DAYS = 90


class CreateReminderArgs(BaseModel):
    when: datetime = Field(..., description="When to remind, ISO 8601 with timezone, e.g. 2026-11-05T09:00:00-05:00")
    text: str = Field(..., min_length=3, max_length=200, description="What to remind the user about")

    @field_validator("when")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("must include a timezone offset")
        return v.astimezone(UTC)


class CreateReminder(Tool):
    name = "create_reminder"
    description = (
        "Set a reminder for the user who is talking to you. The bot will mention them in this "
        "channel at that time. Requires the user's approval. Only use when the user asks for a reminder."
    )
    Args = CreateReminderArgs
    tier = Tier.WRITE

    def describe(self, args: CreateReminderArgs, ctx: ToolContext) -> str:
        return f'Remind you at {args.when.strftime("%Y-%m-%d %H:%M UTC")}: "{args.text}"'

    def run(self, args: CreateReminderArgs, ctx: ToolContext) -> ToolResult:
        now = ctx.now()
        if args.when <= now:
            raise ToolError("that time is in the past")
        if args.when > now + timedelta(days=MAX_REMINDER_DAYS):
            raise ToolError(f"reminders can be at most {MAX_REMINDER_DAYS} days ahead")
        rid, created = ctx.store.create_reminder(ctx.user_id, ctx.channel_id, args.when, args.text)
        verb = "Reminder set" if created else "That reminder already exists"
        return ToolResult(f"{verb} (id {rid}) for {args.when.isoformat()}.", {"reminder_id": rid, "created": created})


ESCALATIONS_PER_DAY = 3


class EscalateArgs(BaseModel):
    reason: Literal["unanswered_question", "bug_report", "security", "conduct", "other"]
    summary: str = Field(..., min_length=10, max_length=800, description="What the helpers need to know")


class EscalateToHuman(Tool):
    name = "escalate_to_human"
    description = (
        "Hand the conversation to the human helpers by posting a summary in their queue channel. "
        "Use when the docs don't answer the question, for suspected bugs, security issues, or "
        "conduct problems. Requires the user's approval."
    )
    Args = EscalateArgs
    tier = Tier.NOTIFY

    def describe(self, args: EscalateArgs, ctx: ToolContext) -> str:
        return f"Post to the helpers' queue ({args.reason}): {args.summary[:160]}"

    def run(self, args: EscalateArgs, ctx: ToolContext) -> ToolResult:
        since = ctx.now() - timedelta(days=1)
        if ctx.store.count_escalations(ctx.user_id, since) >= ESCALATIONS_PER_DAY:
            raise ToolError(f"limit of {ESCALATIONS_PER_DAY} escalations per day reached; ask them to wait")
        queue = ctx.services["helpers_channel_id"]
        text = f"**Escalation ({args.reason})** from <@{ctx.user_id}> in <#{ctx.channel_id}>\n{args.summary}"
        msg_id = ctx.gateway.post_message(queue, text)
        eid = ctx.store.create_escalation(ctx.user_id, ctx.channel_id, args.reason, args.summary, msg_id)
        return ToolResult(f"Escalated to the helpers (ticket {eid}).", {"escalation_id": eid, "reason": args.reason})


def default_tools() -> list[Tool]:
    from .docs import LookupRelease, SearchDocs

    return [SearchDocs(), LookupRelease(), ReadThread(), CreateReminder(), EscalateToHuman()]
