# Design

## What the agent may and may not do

The agent answers FastAPI questions from the docs, looks up release history, reads the
current channel when asked, sets reminders for the person talking to it, and hands
conversations to human helpers. It may not act for anyone other than the sender, may not
change state or contact anyone without that person's explicit approval, and must not
present an answer as coming from the docs unless it cites passages it actually retrieved.

## Decisions and where they come from

**1. A hand-written loop, not a framework.** Anthropic's [*Building effective agents*](https://www.anthropic.com/research/building-effective-agents)
recommends starting from the LLM API directly, because frameworks add abstraction that makes
prompts and responses harder to see. The loop here is under 400 lines, and every step is
traceable. Porting it to LangGraph or the OpenAI Agents SDK is a planned comparison, not a
dependency.

**2. Agent, not workflow, but only where it pays.** The same post distinguishes *workflows*
(fixed code paths) from *agents* (the model chooses tools). Retrieval was a fixed first step
in the original RAG bot. Making it a tool lets the model search twice with better words, skip
search for small talk, and chain it with other tools ("when was X released, and remind me...").
Everything that must be deterministic stays in code: dates are parsed by Pydantic, releases
come from a structured file, and permissions are enforced outside the model.

**3. Approval as a resumable interruption.** The OpenAI Agents SDK and LangGraph both model
human approval the same way: the run records an interruption instead of executing the tool,
saves its state, and resumes with the decision. Here the full run state (messages, remaining
tool calls, sources, budget counters) is stored in SQLite, so an approval works even after a
restart (`test_paused_run_survives_restart`). Declines go back to the model as a tool result,
so it can tell the user honestly that nothing happened.

**4. Permission tiers instead of per-tool rules.** Read tools run automatically; write and
notify tools always pause. Adding a tool means choosing its tier, not writing new safety code.

**5. External text is data.** Retrieved passages, release notes and Discord messages are
wrapped in `<untrusted>` fences, with any fence tags inside the content stripped. The prompt
tells the model to ignore instructions inside fences, but **the defence does not depend on the
model obeying**: even a successful injection can only *propose* an action, and the user sees it
before anything happens. The eval suite plants injections to measure how often the model is fooled.

**6. Citations are verified, not trusted.** Search results are numbered per run. The final
answer must cite only numbers that exist, and must cite something if it used the docs. A
failing answer gets one automatic retry with the reason; if it still fails, it is delivered
marked unverified (and counted in evals) rather than silently dropped.

**7. Evaluate end state and reliability.** [τ-bench](https://arxiv.org/abs/2406.12045) grades
tool-using agents by comparing the database state after a conversation with the expected
state, and introduced **pass^k**, the chance an agent succeeds on *all* k repeats of the same
task. Both ideas are used here: scenarios check stored reminders (to the minute, in UTC),
escalation reasons and the audit log, and the suite reports pass^1 and pass^k. Wording is
checked only loosely (a key term), so a correct answer in different words still passes.

**8. Exact tools for exact facts.** Release dates are a lookup in `releases.json`, not a
retrieval-and-hope. The retrieval baseline shows why: semantic search finds the right *page*
reliably but isn't the right tool for "which version and on what date".

## What's deliberately not here (yet)

- **No LLM judge.** All current checks are deterministic. A faithfulness judge is planned,
  calibrated against hand labels before its numbers are reported.
- **No long-term user memory.** History is per thread (last 8 turns). Cross-session preferences
  belong to a separate project (an email assistant that learns from edits) and would need the
  same leakage tests.
- **No input classifier.** Sensitive-topic routing is a prompt policy measured by evals, not a
  separate model.
- **Single process.** SQLite and an in-memory trace buffer suit a demo-scale bot. Scaling out
  would mean Postgres, a queue between the bot and agent workers, and an OpenTelemetry exporter
  for traces.
