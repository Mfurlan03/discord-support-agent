"""Step-level tracing: every model call, tool call, guard and approval becomes one event.

Events are kept in memory per run (for evals and the trace viewer) and appended to a
JSONL file when a directory is configured.
"""

from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any


class Tracer:
    def __init__(self, directory: str | None = None, keep_runs: int = 500):
        self.directory = directory
        self.runs: OrderedDict[str, list[dict]] = OrderedDict()
        self.keep_runs = keep_runs
        if directory:
            os.makedirs(directory, exist_ok=True)

    def event(self, run_id: str, kind: str, **fields: Any) -> dict:
        ev = {"ts": datetime.now(UTC).isoformat(), "t": time.perf_counter(), "run_id": run_id, "kind": kind, **fields}
        events = self.runs.setdefault(run_id, [])
        events.append(ev)
        self.runs.move_to_end(run_id)
        while len(self.runs) > self.keep_runs:
            self.runs.popitem(last=False)
        if self.directory:
            day = ev["ts"][:10]
            with open(os.path.join(self.directory, f"{day}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        return ev

    def get(self, run_id: str) -> list[dict]:
        return list(self.runs.get(run_id, []))

    def recent(self, n: int = 50) -> list[dict]:
        out = []
        for run_id in reversed(self.runs):
            evs = self.runs[run_id]
            start = next((e for e in evs if e["kind"] == "run_start"), {})
            end = next((e for e in reversed(evs) if e["kind"] in {"run_end", "run_paused"}), {})
            out.append(
                {
                    "run_id": run_id,
                    "ts": evs[0]["ts"],
                    "user_text": start.get("text", "")[:120],
                    "status": end.get("status", end.get("kind", "running")),
                    "steps": sum(1 for e in evs if e["kind"] == "llm_call"),
                }
            )
            if len(out) >= n:
                break
        return out
