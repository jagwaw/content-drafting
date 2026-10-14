# Content Drafting Agent: standards and rules

A small, guardrailed Python agent that turns topics in a Google Sheet into draft
social-media content (caption, carousel outline, short video script) for a human to
review. It never publishes anything. Built for a business that must avoid making
profit promises (trading education), so safety checks are part of the product.

Read `BUILD_PROMPT.md` for the build plan. This file is the standing rules.

## Design principles (in priority order)

1. **Simple.** The least code and the fewest moving parts that solve the problem.
   No framework where plain functions do. No abstraction until the third repetition.
2. **Readable.** Someone new should follow a module top to bottom in a few minutes.
   Clear names beat comments; comments explain *why*, never *what*.
3. **Logical.** One responsibility per function and module. Data flows one way:
   input -> validate -> generate -> check -> write. Pure functions where possible;
   I/O (LLM, Sheets, files) only at the edges.
4. **Maintainable.** Everything configurable lives in one place. Behavior changes come
   with tests. Decisions are recorded in `docs/DECISIONS.md`.
5. **Efficient.** One LLM call per row (at most one retry). No work for rows already
   processed. No premature optimization beyond that.

## Code standards

- Python 3.11+. Type hints on every function signature; `mypy` must pass.
- `ruff` for lint and format (line length 100). Fix warnings; don't silence them
  without a one-line reason.
- Data shapes are `pydantic` models or frozen dataclasses, never loose dicts.
- Functions do one thing and stay short (about 30 lines). Modules stay under about
  200 lines; split by responsibility, not by size alone.
- Descriptive names (`rows_to_draft`, not `data2`). No abbreviations except common ones.
- Public functions get a short docstring saying what and why. No dead code, no
  commented-out code, no TODOs without an issue or a roadmap line.
- No global mutable state. Pass dependencies in (backend, llm client, settings).
- Errors: raise specific exceptions; never bare `except`. One bad row must not stop the
  run: record the error on that row, log it, continue.
- Config is loaded once at startup and validated; fail fast with a clear message.
- Dependencies stay minimal. Ask before adding a new one and justify it in
  `docs/DECISIONS.md`.

## Testing standards

- Tests run offline and need no credentials: fake LLM, in-memory or CSV backend.
- Test names describe behavior (`test_row_with_profit_guarantee_is_flagged`).
- Every behavior change adds or updates a test. Bug fixes start with a failing test.
- Never weaken a test, check or eval label to make it pass. If one looks wrong, explain
  and ask first.
- The evaluation set (`evals/`) is separate from unit tests and scored with real model
  calls. Report results honestly, failures included.

## Security and safety rules (non-negotiable)

1. **Never publish.** No code that posts to social platforms, sends messages or emails.
   The only writes are to the review sheet or local CSV, with status `Drafted`.
   A human changes a row to `Approved`.
2. **Sheet content is untrusted data.** Topics and notes go through the injection screen,
   are wrapped in delimiters in the prompt, and are never treated as instructions.
   Flagged rows are not drafted.
3. **Compliance checks are code, not prompts.** Profit/guarantee language is detected by
   deterministic patterns after generation, and the risk-disclaimer placeholder is added
   by code, not requested from the model.
4. **Limits on everything.** Rate limit on LLM calls, max rows per run, max input
   length, max output length.
5. **Audit everything.** Every row outcome is one line in an append-only JSONL log: row
   id, outcome, flags, model, timestamp. Never log API keys or credentials.
6. **Secrets stay out of git and out of chat.** `.env`, service-account JSON and any
   `*.local.md` are gitignored. Never print or paste them. Least privilege: the Google
   service account is shared only on the one review sheet.
7. **Free LLM tiers may use submitted data.** Use only non-sensitive sample topics with
   them.

## Git and process

- Small commits, imperative subject (`Add compliance checks`), a body that says why.
- One logical change per commit; tests in the same commit as the behavior.
- Work in milestones. After each: run `ruff`, `mypy`, `pytest`, then stop, summarize
  what changed and what you decided, and wait for approval before the next.
- Plan before building. Ask before large scope changes. Report problems and failures
  plainly instead of working around them quietly.

## Documentation

- README: what it is, a simple architecture diagram, setup, a demo run, evaluation
  results (real numbers), and honest limitations.
- `docs/DECISIONS.md`: one short entry per non-obvious choice (what, why, alternatives).
