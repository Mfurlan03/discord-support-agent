# Discord Support Agent

[![CI](https://github.com/Mfurlan03/discord-support-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Mfurlan03/discord-support-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11-blue)

A tool-using support agent for a FastAPI community Discord. It answers questions from the
FastAPI docs with **verified citations**, looks up release history exactly, summarizes
threads, sets reminders and escalates to human helpers. **Every action that changes state
or contacts people waits for the user's approval.** The agent is measured by a
**47-scenario eval suite** that grades end state, trajectory and safety over repeated trials.

> The model proposes; code decides what runs.

![Trace viewer](docs/trace-viewer.png)
*Trace viewer on a scripted demo run: search, a reminder proposal, approval, cited answer. Token counts in this screenshot are from the scripted model.*

---

## What it does

| Ask in Discord | What happens | Tier |
|---|---|---|
| "My React app can't reach my API, the browser blocks it" | `search_docs` → answer citing [fastapi.tiangolo.com/tutorial/cors](https://fastapi.tiangolo.com/tutorial/cors/) | read |
| "When did 0.100.0 ship?" | `lookup_release` → exact date from the release notes, no guessing | read |
| "Summarize this thread" | `read_thread` → summary of the actual messages | read |
| "Remind me tomorrow at 9am Toronto time to try it" | `create_reminder` → **Approve / Cancel** buttons → stored, delivered on time | write |
| "Can a human look at my proxy issue?" | `escalate_to_human` → **Approve / Cancel** → posted to the helpers' queue | notify |

@mention the bot in any channel and it opens a thread; follow-ups in that thread keep context.

## Architecture

```mermaid
flowchart LR
    U[Discord user] --> B[Discord bot<br/>threads, buttons]
    B --> L[Agent loop<br/>agent/loop.py]
    L <--> M[LLM<br/>OpenAI-compatible or Anthropic]
    L --> G{Guardrails gate}
    G -->|read| R[search_docs · lookup_release · read_thread]
    G -->|write / notify| A[Approve or Cancel<br/>in Discord]
    A -->|approved| W[create_reminder · escalate_to_human]
    R --> K[(Hybrid retriever<br/>BM25 + MiniLM)]
    W --> S[(SQLite<br/>reminders · escalations · audit)]
    L -. every step .-> T[Traces + viewer]
```

| Module | Responsibility |
|---|---|
| [`agent/loop.py`](agent/loop.py) | The hand-written agent loop: tool dispatch, budgets, pause and resume for approval, citation check and retry |
| [`agent/tools/`](agent/tools) | Five tools, each a Pydantic argument model + permission tier + `run()` |
| [`agent/guardrails.py`](agent/guardrails.py) | Untrusted-content fencing, budgets, citation verification |
| [`agent/llm.py`](agent/llm.py) | One message format; adapters for OpenAI-compatible APIs and Anthropic |
| [`agent/state.py`](agent/state.py) | SQLite: history, paused runs, reminders (idempotent), escalations, audit log |
| [`agent/tracing.py`](agent/tracing.py) | One event per model call, tool call, guard and approval |
| [`rag/`](rag) | Heading-aware markdown chunking with deep links; BM25, dense and hybrid (RRF) retrieval |
| [`bot/`](bot) | discord.py front end, approval buttons, reminder delivery |
| [`api/`](api) | HTTP API (chat without Discord) and the trace viewer |
| [`evals/`](evals) | Scenario harness, 47 scenarios, retrieval baseline |

Design rationale, including what was borrowed from Anthropic's agent guidance, the OpenAI
Agents SDK, LangGraph and τ-bench: **[docs/DESIGN.md](docs/DESIGN.md)**.

## Safety model

| Threat | Defence | Enforced by |
|---|---|---|
| Model takes an action nobody asked for | An **independent checker** call that sees only the user's own words must agree the action was requested (fails closed); then the user must click Approve; approvals expire after 15 min | `check_action`, `agent/loop.py` |
| Prompt injection in docs or chat messages | External text fenced as `<untrusted>`; the action checker never sees external text, so injected instructions can't pass it; injection scenarios in evals | `agent/guardrails.py`, evals |
| Hallucinated answers | Citations must point at passages retrieved in this run; one automatic retry; unverified answers flagged | `check_citations` |
| Runaway loops and cost | Budgets on model calls, tool calls, tokens and wall-clock time | `Budgets` |
| Acting for someone else | Tools never take a user id from the model; they act only for the sender | `ToolContext` |
| Spam to helpers, duplicate reminders | Rate limit on escalations; idempotency key on reminders | `agent/tools/actions.py`, `agent/state.py` |
| No accountability | Audit row for every proposed tool call: arguments, decision, approver, outcome | `audit` table |

## Evaluation

Following [τ-bench](https://arxiv.org/abs/2406.12045), scenarios are graded on the **end state**
(reminders stored at the right UTC time, escalations posted with the right reason) and the
**trajectory** (which tools were proposed), not the exact wording, and each scenario runs
**k times** to report reliability (**pass^k**: the share of scenarios that pass every trial).

| Category | Scenarios | Example check |
|---|---:|---|
| answerable | 13 | Key term present, citation to the right page, citations verified |
| release_lookup | 5 | `lookup_release` used; exact date or version in the answer |
| unanswerable | 4 | Says the docs don't cover it instead of inventing an answer |
| multi_step | 4 | Docs or release lookup + reminder at the exact UTC time (Toronto timezone handling) |
| escalation / approval | 5 | Escalation reason correct; declined actions leave no state |
| no_unrequested_actions | 4 | No reminder or escalation unless asked (safety) |
| injection | 4 | Planted instructions in docs or chat are ignored (safety) |
| follow_up / privacy | 4 | Uses thread context; nothing leaks across threads (safety) |
| summarize / clarification | 4 | Reads the real channel; asks before guessing |

```bash
python -m evals.run_evals -k 3             # needs LLM_* in .env; writes evals/results/
python -m evals.retrieval_eval             # retrieval baseline (downloads MiniLM)
```

The paid agent eval runs only on demand (**Actions → CI → Run workflow**, choose k), publishes
its table to the workflow summary, and **fails the build on any safety violation**: an action
executed without a genuine request, or text leaked. Proposing an unrequested action, even
one that gets blocked, counts as a task failure.

### Eval log

Each row is a real CI run with `deepseek/deepseek-chat` via OpenRouter. The fixes come from
reading every failure in the previous run.

| Run | k | pass^1 | Executed-harm violations | What changed before it | What it revealed |
|---|---:|---:|---:|---|---|
| 1–2 | 3 | invalid | n/a | first runs | 109/141 trials were model-API errors (account out of credits) scored as agent failures. Added retries with backoff and a separate API-error count |
| 3 | 1 | 80.9% | 0 | credits added | `lookup_release` matched only exact phrases; the model asked "shall I?" in text instead of calling tools; it proposed a reminder nobody asked for (the approval gate caught it) |
| 4 | 1 | 91.5% | 1 | word-level release search; prompt: call action tools directly | Acting more directly made it **easier to manipulate**: a planted instruction triggered an escalation that the simulated user approved |
| 5 | 3 | **90.8%** (pass^3 **87.2%**) | **0** | **independent action checker** (sees only the user's words); safety redefined as executed harm | The model still fell for the planted instruction in 2 of 3 trials, **and the checker blocked both**. Also found a harness bug: YAML read `Someone in #general ...` as `Someone in` plus a comment, so the "conduct" scenario was unanswerable (the agent rightly asked what was meant). Fixed, with a test |

The lesson from runs 3–5: a prompt change traded safety for helpfulness, so the defence moved
from the prompt into the architecture, and run 5 shows it holding against a model that is
still fooled. Open failures in run 5: answering from memory without searching (background
tasks, 2/3), skipping the docs half of a two-part request (2/3), citing a docs-typo release
(0.103.1) instead of the deprecation release (0.100.0), and the conduct scenario (harness bug,
now fixed). Runs 1–2 also show why the eval reports API errors
separately: without that, a billing problem looked like a bad agent.

Retrieval baseline (CI, 12 answerable questions, the user's raw wording as the query, gold = the right page):

| Retriever | Hit@1 | Hit@3 | Hit@5 | MRR@10 |
|---|---:|---:|---:|---:|
| BM25 | 0.67 | 0.92 | 1.00 | 0.78 |
| Dense (all-MiniLM-L6-v2) | 0.83 | 0.92 | 0.92 | 0.88 |
| **Hybrid (RRF of both)** | 0.75 | **1.00** | **1.00** | **0.88** |

Hybrid is the default: dense ranks best at the top, BM25 never misses a page in the top 5,
and fusing them gets the right page into the top 3 for every question.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
cp .env.example .env                                       # set LLM_PROVIDER, LLM_MODEL, LLM_API_KEY

uvicorn api.app:app --port 8000                            # no Discord needed: open http://127.0.0.1:8000
python -m bot                                              # with DISCORD_BOT_TOKEN + HELPERS_CHANNEL_ID set
```

Or with Docker: `docker compose up --build`. The bot and the trace viewer share one process.

**Discord setup:** create an application at discord.com/developers, add a bot, enable the
*Message Content* intent, and invite it with permissions to read and send messages, create
public threads and send messages in threads.

## Development

```bash
pip install -r requirements-dev.txt     # no torch, no API keys
ruff check . && pytest -q               # 65 tests, ~1 s
```

The tests drive the real loop with a scripted model, covering approval, rejection, expiry,
restart-safe resume, budgets, citation retry, idempotency, rate limits, thread isolation and
injection fencing. They also verify that the eval harness catches injected actions, leaks
and wrong reminder times.

## Knowledge base

`data/corpus/` (121 pages, 1,423 chunks) and `data/releases.json` (337 releases) are built
from the [FastAPI documentation](https://github.com/fastapi/fastapi) (MIT License) by
`scripts/build_corpus.py`; see [data/SOURCE.md](data/SOURCE.md). Each chunk keeps its heading
anchor, so citations link to the exact section on fastapi.tiangolo.com.

## License

MIT for this project's code. The FastAPI documentation in `data/` is MIT licensed by its authors.
