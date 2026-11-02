# Walkthrough: learn this codebase in five sessions

This is the order to read the code so you can explain all of it in an interview. Each
session ends with an exercise; do it without looking, then check against the code.

## Session 1: the loop (`agent/loop.py`, `agent/llm.py`)

Read `Agent._run` top to bottom. The whole agent is this cycle:

1. Finish any tool calls left from the last model turn (`pending_calls`).
2. Check budgets.
3. Call the model with the conversation and tool schemas.
4. If it asked for tools, queue them and go to 1. If it answered, verify citations and finish.

Then read `llm.py`: the loop speaks one message format, and each adapter translates it.
Compare `to_openai` with `to_anthropic`. Anthropic needs consecutive tool results merged
into one user turn; OpenAI wants one `tool` message per call.

**Exercise:** draw the message list after "When was 0.100.0 released?", step by step, with
the roles and what each message contains.

## Session 2: tools and the approval pause (`agent/tools/`, `_handle_call`)

A tool is an `Args` Pydantic model (its JSON schema is what the model sees), a `tier`, and
`run()`. In `_handle_call`, follow the three paths: invalid arguments become an error message
back to the model; read tools run; write and notify tools save the whole run and return
`awaiting_approval`. Then read `resolve_approval`: approver check, expiry, run or decline,
then back into `_run`.

**Exercise:** explain why `test_paused_run_survives_restart` passes, and what would break if
`state` weren't serialized to SQLite.

## Session 3: guardrails and state (`agent/guardrails.py`, `agent/state.py`)

`wrap_untrusted` (why strip fence tags inside the content?), `check_citations` (when may an
answer cite nothing?), and `Budgets`. In `state.py`, the reminder `idem_key` and the audit
table. Note that tools never receive a user id from the model.

**Exercise:** list every way the model could try to create a reminder for someone else, and
what stops each one.

## Session 4: retrieval (`rag/`)

`chunk_markdown` splits on headings outside code blocks and keeps each section's anchor, so a
citation links to the exact section. `BM25Retriever`, `DenseRetriever` and `HybridRetriever`
(Reciprocal Rank Fusion: why does RRF need no score calibration?). Run
`python -m evals.retrieval_eval --modes bm25 --embedder hashing`.

**Exercise:** explain why the harness test needed a rewritten query to find the CORS page,
and what that says about letting the model write its own search queries.

## Session 5: evals (`evals/harness.py`, `evals/scenarios/`)

Read one scenario of each category, then `run_trial` and `run_checks`. Task checks and safety
checks are separate, and `universal_safety` runs on every trial. Read `summarize` for pass^1
and pass^k.

**Exercise:** write a new scenario: a user asks for a reminder "next Tuesday at 8pm Vancouver
time". Work out the exact UTC time it must be stored at, and add the check.

## After that: run it for real

1. Put a model and key in `.env`, start `uvicorn api.app:app`, and try the viewer at http://127.0.0.1:8000.
2. Run `python -m evals.run_evals -k 3`. Read every failure in `evals/results/latest.md`.
3. Pick the most interesting failure, fix it (prompt, tool description or code), rerun, and
   write down the before and after. That story is your best interview answer.
