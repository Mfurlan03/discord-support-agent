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
