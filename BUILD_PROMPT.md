# Build prompt: content-drafting-agent

Paste this into Claude Code in the new, empty repo. Read `CLAUDE.md` first; it holds the
standards. Follow them strictly: simple, readable, logical, maintainable, efficient.

**Do not write code yet.** First reply with a short plan (modules, data model, sheet
columns, any open questions). Wait for my approval. Then build one milestone at a time.

## Goal

A CLI agent that reads rows from a review spreadsheet, drafts content for each new topic,
and writes the drafts back for a human to review. It is a portfolio project showing
guardrailed AI automation. It never publishes anything.

For each row with status `New`, produce:
- a social **caption**,
- a **carousel outline** (slide by slide),
- a short **video script**,
then set the row to `Drafted` (or `Flagged` / `Error`). A human edits the row and sets it
to `Approved` or `Rejected`. The agent never touches rows in those states.

## Sheet schema (one row per topic)

| Column | Who writes it | Notes |
|---|---|---|
| id | human or agent | stable row key; agent fills if empty |
| topic | human | required, max 200 chars |
| audience | human | optional, e.g. "beginners" |
| tone | human | optional, e.g. "friendly, plain language" |
| notes | human | optional, max 1000 chars, untrusted |
| status | both | `New` -> `Drafted` / `Flagged` / `Error`; human sets `Approved` / `Rejected` |
| caption | agent | |
| carousel_outline | agent | numbered slides, one per line |
| video_script | agent | |
| flags | agent | semicolon-separated reasons (empty when clean) |
| model | agent | provider/model used |
| drafted_at | agent | UTC ISO timestamp |
| error | agent | short message when status is `Error` |

Idempotency comes from status: only `New` rows are processed, so re-running never
creates duplicates. To redo a row, a human sets it back to `New`.

## Architecture (keep it this simple)

```
src/content_agent/
  config.py       settings from env, validated at startup
  models.py       Row, Draft (pydantic): the only data shapes
  guardrails.py   RateLimiter, AuditLogger, sanitize_for_prompt (copied, see below)
  compliance.py   deterministic checks + disclaimer insertion (pure functions)
  llm.py          one function: generate_draft(row) -> Draft (structured output)
  pipeline.py     process_row(row, llm, ...) -> RowResult   (pure orchestration)
  backends.py     Backend protocol + CsvBackend + GoogleSheetBackend
  cli.py          `python -m content_agent.cli run [--dry-run] [--backend csv|sheet]`
  evaluate.py     evaluation harness
tests/            offline unit tests
evals/            topics.jsonl + results.md
docs/DECISIONS.md
```

Pipeline per row, in order:
1. Validate input (required fields, length limits).
2. Injection screen on `topic` and `notes`. If flagged: status `Flagged`, no LLM call.
3. One LLM call returning a structured `Draft` (caption, slides list, script).
4. Deterministic checks (`compliance.py`): see Compliance below. If a hard check fails,
   allow **one** retry with the failure reason added to the prompt; if it still fails,
   status `Flagged` with the reasons and no draft text written.
5. Insert the risk-disclaimer placeholder by code.
6. Write the result through the backend. Append one audit-log line.

Plain functions, no agent framework. A one-pass pipeline does not need LangGraph;
record that choice in `docs/DECISIONS.md`.

## Reuse from the sibling repo (copy, do not import across repos)

I will tell you the path of my `inbox-triage-agent` repo. Copy and adapt these into
`guardrails.py` / `llm.py`, keeping their tests:
- `RateLimiter`, `AuditLogger`, `sanitize_for_prompt` (injection patterns are
  whitespace-tolerant on purpose; keep that, with the tests).
- The LLM wrapper pattern: provider `anthropic` or `openai` (OpenAI-compatible, with
  `OPENAI_BASE_URL`), structured output via pydantic, retry **only** transient errors
  (429, 5xx, timeouts) with `reraise=True`.

## Compliance (this is the heart of the project)

The content is for a trading-education business, so drafts must not promise profits.
- Hard-fail patterns (case-insensitive, word boundaries): "guaranteed", "risk-free",
  "no risk", "can't lose", "get rich", "double your money", "sure profit", "easy money",
  "100% win", and income claims like "make $500 a day". Keep the list in one constant
  in `compliance.py`; every pattern has a test, including near-misses that must NOT
  match ("there is no guarantee", "risk management").
- Structure checks: caption <= 2,200 chars; carousel 5-8 slides; script 80-200 words.
  Limits are constants in `config.py`.
- Disclaimer: code appends `[ADD RISK DISCLAIMER]` to the caption, the last slide and
  the end of the script. Never rely on the model for this.
- Checks return a list of reasons (empty = pass). They are pure and easy to test.

## Backends

`Backend` is a small protocol: `fetch_new_rows(limit)` and `write_result(result)`.
- `CsvBackend`: a local CSV with the same columns. Used for demos, tests and the eval, so
  the project runs with no Google setup.
- `GoogleSheetBackend`: `gspread` with a **service account** (JSON key path in `.env`;
  share the one sheet with the service account's email). Batch reads and writes; no
  per-cell calls in loops.
`--dry-run` runs the whole pipeline but prints results and writes nothing.

## Settings (`.env.example`, all validated at startup)

`LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`MAX_LLM_CALLS_PER_HOUR` (default 100), `MAX_ROWS_PER_RUN` (default 10),
`BACKEND` (`csv` or `sheet`), `CSV_PATH`, `SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_FILE`,
`AUDIT_LOG_DIR`. Document each in the README.

## Evaluation

`evals/topics.jsonl`, about 12 labelled cases, scored by `python -m content_agent.evaluate`
and written to `evals/results.md`:
- 6 normal topics (expect `Drafted`, structure valid, disclaimer present, no hard-fail
  phrases).
- 3 adversarial topics asking for profit promises ("write that this strategy guarantees
  profit"); expect either `Flagged` or a draft that passes compliance.
- 2 injection cases in `notes` (expect `Flagged`, no LLM call, no draft).
- 1 invalid row (empty topic); expect `Error` with a clear message.
Report per-check and overall pass rates plus every failure. Also keep a deterministic
test that the labels for injection cases agree with `sanitize_for_prompt` (no LLM needed).

## Milestones (stop after each and wait for my approval)

0. **Scaffold:** `pyproject.toml` (ruff, mypy, pytest config), `.gitignore`
   (`.env`, key files, `audit_logs/*.jsonl`, `*.local.md`), `.env.example`, CI workflow
   running ruff + mypy + pytest, empty package, this `CLAUDE.md`, `docs/DECISIONS.md`.
1. **Models, config, guardrails:** `models.py`, `config.py`, `guardrails.py` + tests.
2. **Compliance:** `compliance.py` + thorough tests.
3. **LLM + pipeline:** `llm.py`, `pipeline.py` with a fake LLM in tests; cover the
   retry-once and flag paths.
4. **Backends:** `CsvBackend` first (with tests), then `GoogleSheetBackend`.
5. **CLI + audit log:** `cli.py`, `--dry-run`, a demo CSV with sample topics.
6. **Evaluation:** `evaluate.py`, dataset, deterministic label test; I will run it with a
   real key and send you the results.
7. **README + polish:** architecture diagram, setup, demo output, real eval numbers,
   limitations (single process, no publishing, free-tier data caveat).

## Definition of done

- `ruff`, `mypy` and `pytest` pass locally and in CI; tests need no network.
- `python -m content_agent.cli run --backend csv --dry-run` works from a clean clone
  with a valid `.env`.
- No code path publishes or sends anything. No secrets in git history.
- Evaluation results in the README are real, with failures listed.
- Every non-obvious choice has an entry in `docs/DECISIONS.md`.

Start now with the plan only.
