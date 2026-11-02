"""Deterministic fakes for tests and evals: Discord, the clock, and tiny corpora."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FakeGateway:
    """In-memory Discord: channels hold messages; posts are recorded."""

    def __init__(self, channels: dict[str, list[dict]] | None = None):
        self.channels = {k: list(v) for k, v in (channels or {}).items()}
        self.posts: list[dict] = []

    def fetch_messages(self, channel_id: str, limit: int) -> list[dict]:
        return self.channels.get(channel_id, [])[-limit:]

    def post_message(self, channel_id: str, text: str) -> str:
        self.posts.append({"channel_id": channel_id, "text": text})
        self.channels.setdefault(channel_id, []).append({"author": "agent", "content": text})
        return f"msg-{len(self.posts)}"


class FakeClock:
    def __init__(self, start: datetime | str = "2026-11-02T15:00:00+00:00"):
        self.now = datetime.fromisoformat(start) if isinstance(start, str) else start
        self.now = self.now.astimezone(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)
