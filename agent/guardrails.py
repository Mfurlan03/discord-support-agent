"""Guardrails that live in code, not in the prompt.

* Untrusted content (retrieved docs, Discord messages) is fenced as data.
* Budgets bound every run: steps, tool calls, tokens, wall-clock time.
* Citations in the final answer must point at sources actually retrieved this run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rag.retrieval import Hit

_FENCE_RE = re.compile(r"</?untrusted[^>]*>", re.I)


def wrap_untrusted(source: str, text: str) -> str:
    """Fence external text so the model treats it as data.

    Any fence tags inside the text are stripped first, so content can't close the
    fence early and smuggle text outside it.
    """
    return f'<untrusted source="{source}">\n{_FENCE_RE.sub("", text)}\n</untrusted>'


@dataclass(frozen=True)
class Budgets:
    max_steps: int = 6  # LLM calls per run
    max_tool_calls: int = 8
    max_total_tokens: int = 60_000
    max_seconds: float = 60.0
    approval_ttl_seconds: int = 15 * 60


_CITE_RE = re.compile(r"\[(\d{1,2})\]")
_DONT_KNOW_RE = re.compile(
    r"\b(i (?:don't|do not|couldn't|could not) (?:know|find)|not (?:covered|in the docs|mentioned)|no information)\b",
    re.I,
)


@dataclass
class CitationCheck:
    ok: bool
    cited: list[int]
    problem: str = ""


def check_citations(answer: str, sources: list[Hit]) -> CitationCheck:
    """Every [n] must exist; an answer built on retrieved docs must cite at least one.

    Answers that say the docs don't cover something are allowed to cite nothing.
    """
    cited = sorted({int(n) for n in _CITE_RE.findall(answer or "")})
    bad = [n for n in cited if n < 1 or n > len(sources)]
    if bad:
        return CitationCheck(
            False, cited, f"citation(s) {bad} do not match any retrieved source (valid: 1..{len(sources)})"
        )
    if sources and not cited and not _DONT_KNOW_RE.search(answer or ""):
        return CitationCheck(False, cited, "the answer uses the docs but cites no sources")
    return CitationCheck(True, cited)


def render_citations(answer: str, sources: list[Hit], cited: list[int]) -> str:
    """Append a Sources footer with deep links, numbered as the model cited them."""
    if not cited:
        return answer
    lines = [
        f"[{n}] <{sources[n - 1].url}>" if sources[n - 1].url else f"[{n}] {sources[n - 1].chunk_id}" for n in cited
    ]
    return answer.rstrip() + "\n\nSources:\n" + "\n".join(lines)


# ---------------------------------------------------------------- action checker

ACTION_CHECK_PROMPT = """You review actions an assistant wants to take for a Discord user.
You see ONLY what the user typed (never documents or other people's messages), and the proposed action.

Approve only if the user explicitly asked for this kind of action, AND every important detail
(such as a reminder's time) was stated by the user or clearly follows from what they said.
Mentioning a plan ("I'll read it tomorrow") is not a request. Asking for a human, reporting a
security problem, or reporting abusive behaviour IS a request to escalate.

Reply with JSON only: {"allowed": true|false, "reason": "<one short sentence>"}"""


@dataclass
class ActionVerdict:
    allowed: bool
    reason: str


def check_action(llm, user_messages: list[str], tool_name: str, description: str, now_iso: str) -> ActionVerdict:
    """Independent check that the user actually asked for a write/notify action.

    The checker never sees retrieved documents or other people's messages, so text planted
    there cannot influence it. Fails closed: any error or unparseable reply blocks the action.
    """
    import json

    transcript = "\n".join(f"- {m}" for m in user_messages[-6:])
    prompt = (
        f"Current time: {now_iso} (UTC)\n\nWhat the user typed, oldest first:\n{transcript}\n\n"
        f"Proposed action: {tool_name}: {description}"
    )
    try:
        resp = llm.complete(ACTION_CHECK_PROMPT, [{"role": "user", "content": prompt}], [])
        text = (resp.text or "").strip()
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start : end + 1])
        return ActionVerdict(bool(data.get("allowed")), str(data.get("reason", ""))[:200])
    except Exception as e:  # fail closed
        return ActionVerdict(False, f"action check failed: {type(e).__name__}")
