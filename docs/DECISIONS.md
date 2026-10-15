# Decisions

One short entry per non-obvious choice: what, why, alternatives.

## Plain functions, no agent framework

- **What:** The pipeline is ordinary functions: validate, screen, generate, check, write.
- **Why:** Each row is one pass with at most one retry. There is no branching graph,
  memory or tool use, so a framework would add dependencies and indirection for no gain.
- **Alternatives:** LangGraph (used in the sibling inbox-triage repo). It is overkill
  for a one-pass pipeline.

## Default model: Anthropic `claude-haiku-5-5`

- **What:** `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-haiku-5-5` by default.
- **Why:** It is cheap at this volume (about 10 rows per run, at most 2 calls each) and
  reliable with structured output.
- **Alternatives:** Any OpenAI-compatible API through `OPENAI_BASE_URL`, kept so the
  project runs on a free tier. Free tiers may use submitted data, so use only sample topics.

## Dependencies

- **What:** Runtime: `anthropic`, `openai`, `pydantic`, `python-dotenv`, `tenacity`,
  `gspread`. Dev: `pytest`, `ruff`, `mypy`.
- **Why:** The two provider SDKs give structured output. `pydantic` handles the data
  shapes and validates model output. `tenacity` retries only transient errors.
  `gspread` is the smallest maintained Sheets client with service-account auth.
  `python-dotenv` loads `.env`.
- **Alternatives:** Raw HTTP to the Sheets API or the LLM APIs, which means more code to
  maintain. The Google API client is heavier than gspread for this use.

## pip and a venv, no lock file

- **What:** Dependencies live in `pyproject.toml`; install with `pip install -e ".[dev]"`.
- **Why:** No extra tooling is needed to clone and run.
- **Alternatives:** `uv` or Poetry with a lock file. Worth adding if builds start to drift.

## Error rows are not retried automatically

- **What:** Only `New` rows are processed. `Error` rows wait for a human to reset them.
- **Why:** This keeps runs idempotent and stops a persistently failing row from using up
  LLM calls on every run.
- **Alternatives:** Auto-retry `Error` rows with a retry counter column. That adds state
  for little benefit.

## Income claims include percentages

- **What:** The income-claim pattern matches `make`/`earn` + an amount in `$`, `₱` or `%`
  + a time period ("earn 10% a week").
- **Why:** In trading content, percentage-return promises are at least as common as
  cash amounts.
- **Alternatives:** Cash amounts only. Near-miss tests ("a 10% drawdown",
  "risk 1% per trade") guard against over-flagging.

## Guardrails copied from inbox-triage, trimmed to this project

- **What:** `RateLimiter`, `AuditLogger` and `sanitize_for_prompt` were copied. `ApprovalGate`
  and the "forward all emails to" injection pattern were left out.
- **Why:** This agent has no send path to gate, and email forwarding isn't a threat in a
  topic sheet. Keeping them would be dead code. The whitespace-tolerant `\s+` patterns and
  their tests were kept.
- **Alternatives:** Copy everything verbatim. That is simpler to diff against the source
  but carries unused code.

## Typed audit log methods instead of free-form kwargs

- **What:** `AuditLogger.log_row(result)` and `log_event(event, detail)` write a fixed
  set of fields.
- **Why:** The log can only contain fields named in code, so a credential can't be passed
  in by accident, and every row line has the same shape (row id, outcome, flags, model,
  timestamp).
- **Alternatives:** A generic `log(event, **details)` as in inbox-triage. It is more
  flexible but easier to misuse.

## Testability through injection, not monkeypatching

- **What:** `load_settings(env)` takes a mapping, and `RateLimiter` takes a `clock`.
- **Why:** Tests stay free of global state: no `os.environ` edits, no poking private
  timestamps.
- **Alternatives:** `monkeypatch.setenv` and mutating `_timestamps`, which is what the
  sibling repo's tests do.

## Result invariants enforced in the model

- **What:** `RowResult` rejects impossible states: Drafted without a draft, Flagged or
  Error with draft text, Error without a message, or any human-owned status.
- **Why:** "Flagged rows get no draft text" and "the agent never sets Approved" become
  facts the type guarantees rather than conventions each caller must remember.
- **Alternatives:** Check these only in the pipeline. That is easier to bypass from new code.

## Banned words are flagged even when negated

- **What:** "Profits are not guaranteed" is flagged. Only the specific near-misses in the
  build spec ("no guarantee", "risk management") are let through.
- **Why:** Negation detection is easy to fool ("not only guaranteed, but..."). A false
  positive costs one retry with the reason in the prompt; a false negative is a
  compliance failure. The prompt will tell the model to avoid these words entirely.
- **Alternatives:** A negation-aware matcher. Revisit if evals show many false positives.

## All compliance checks are hard checks

- **What:** Phrase and structure failures both trigger the single retry, then `Flagged`.
- **Why:** A 3-slide carousel or a 400-word script is as unusable to the reviewer as a
  banned phrase. One rule is simpler than two tiers.
- **Alternatives:** Soft structure warnings that still write the draft.

## Caption limit counts the disclaimer

- **What:** The 2,200-char check measures `caption + "\n\n[ADD RISK DISCLAIMER]"`.
- **Why:** The final caption, after code appends the placeholder, must still fit the
  platform limit.

## Every real API call passes the rate limiter, including transient retries

- **What:** `RateLimiter.check_and_record()` runs inside the tenacity-retried `_send`, and
  SDK built-in retries are off (`max_retries=0`).
- **Why:** The hourly cap should count actual API calls. A 429 retry is still a call.
  With SDK retries off, tenacity is the single retry policy (3 attempts, exponential wait).
- **Alternatives:** Check once per `generate_draft` call, as inbox-triage does. That
  undercounts when retries happen.

## Rate-limit trips end the run; other LLM failures mark the row

- **What:** `LlmError` (provider errors after retries, or output that fails the `Draft`
  schema) makes that row `Error`. `RateLimitExceededError` propagates out of `process_row`.
- **Why:** One bad row must not stop the run, but a spent budget applies to every
  remaining row. Marking them all `Error` would only make work for the reviewer.

## All four text fields are screened and length-limited

- **What:** `audience` and `tone` get the injection screen and a 100-char limit too, not
  only `topic` and `notes`.
- **Why:** They are just as untrusted and go into the same prompt. The cost is negligible.
- **Alternatives:** Screen only `topic` and `notes` as the spec lists. That leaves an
  obvious bypass.

## Sheet text cannot spoof the data delimiters

- **What:** `build_user_prompt` removes the begin and end marker strings from field values
  before wrapping them.
- **Why:** Otherwise a note containing the end marker could "close" the data block and
  pose as instructions.

## `max_tokens` for the OpenAI-compatible path

- **What:** The OpenAI path sends `max_tokens`, not `max_completion_tokens`.
- **Why:** Free OpenAI-compatible APIs (Groq, Gemini, OpenRouter) widely accept
  `max_tokens`. Reasoning-only OpenAI models reject it; switch if one of those is used.

## Tests build real SDK errors with `httpx2`

- **What:** `tests/test_llm.py` imports `httpx2`, which both SDKs already depend on, to
  construct real `APIStatusError`s.
- **Why:** Testing `is_transient` against the real exception classes catches SDK changes
  that a hand-made stand-in would hide. No new dependency is added.

## Re-check each row right before writing it

- **What:** Both backends re-read the target row before writing. They refuse with
  `BackendError` unless it is still `New` with the same topic and id (or a blank id).
- **Why:** A run takes seconds per row, and the reviewer may be editing the sheet
  meanwhile. Without the check, a row approved mid-run could be overwritten, or an
  inserted row could shift the target onto the wrong topic. The sheet costs one extra read
  per row; the CSV re-reads the file anyway.
- **Alternatives:** Trust the row numbers from the initial fetch. That is cheaper but
  breaks the "never touch Approved or Rejected" rule.

## Columns by header name; write only agent columns

- **What:** Columns are found by header name, in any order, and extra columns are kept.
  The agent writes only `id`, `status` and the agent-owned columns.
- **Why:** Reviewers rearrange sheets and add their own columns. The agent must not
  clobber them.

## Flagged and Error results clear the draft columns

- **What:** Non-Drafted results write blank caption, outline and script.
- **Why:** If a human resets an old row to `New` to redo it, stale draft text must not
  sit next to a `Flagged` status as if it were the new output.

## Sheet writes are RAW; Sheets-only OAuth scope

- **What:** `batch_update(..., raw=True)`, and the service account requests only the
  `spreadsheets` scope.
- **Why:** RAW stores model text as plain text, so output starting with `=` can't become a
  formula. Dropping gspread's default Drive scope is least privilege.
- **Limitation:** The CSV backend writes text as-is. A spreadsheet app opening the CSV may
  treat a cell starting with `=` as a formula. It is a local demo file, so this is
  documented rather than escaped.

## Atomic CSV writes

- **What:** The CSV is written to a `.tmp` file and swapped in with `os.replace`.
- **Why:** A crash mid-write can't leave a half-written review file.

## Dry run still writes the audit log

- **What:** `--dry-run` writes nothing to the sheet or CSV. It still appends audit lines,
  with `run_started` marked `"dry run"`.
- **Why:** A dry run makes real LLM calls. "Audit everything" covers those calls even when
  nothing is saved.

## Exit codes

- **What:** `0` when the run completes (even if rows were Flagged or Error), `1` when the
  run stopped early (rate limit, or the sheet couldn't be read), `2` for a config error.
- **Why:** Flagged and Error rows are normal outcomes for a reviewer, not process failures.
  A scheduler only needs to alert on 1 and 2.

## Failed writes don't stop the run

- **What:** A `BackendError` while writing one row (for example, a human approved it
  mid-run) is printed, audited as `write_failed` and counted as "Not written". The run
  continues.
- **Why:** This follows "one bad row must not stop the run".

## The demo CSV covers every outcome

- **What:** `data/demo_topics.csv` has 5 ordinary topics, 1 profit-pushing topic,
  1 injection in notes and 1 blank topic.
- **Why:** One dry run shows Drafted, Flagged and Error side by side. All topics are
  non-sensitive, so the file is safe to send to free LLM tiers.
- **Note:** A non-dry run edits this file in place. Use `git checkout data/` to reset it.

## Evaluation runs the real pipeline, one pass, no repeat

- **What:** `evaluate.py` sends every case through `process_row`, the same path the CLI
  uses, with a real model and no backend. Each check applies only where it makes sense
  (for example, draft checks only when a draft exists). A case passes when all of its
  checks pass.
- **Why:** Scoring the real path means the numbers describe what the reviewer would see.
  Twelve cases at one call each (two on retry) cost little.
- **Alternatives:** A `--repeat N` option to measure model variance, as in inbox-triage.
  Worth adding if results flip between runs.

## Report rendering is its own module

- **What:** `eval_report.py` renders the Markdown; `evaluate.py` runs and scores.
- **Why:** This keeps both modules well under the ~200-line guideline, split by
  responsibility.

## Eval results go through the audit log

- **What:** Every eval case's `RowResult` is appended to the audit log.
- **Why:** Eval runs make real LLM calls; "audit everything" includes them.

## Output cap of 8,000 tokens, sized for reasoning models

- **What:** `MAX_OUTPUT_TOKENS` went from 2,000 to 8,000.
- **Why:** The first real eval run on Gemini hit the cap with only ~450 visible tokens,
  because hidden reasoning tokens count toward `max_tokens`. The cap still stops runaway
  output. The draft's own length is enforced separately by the compliance checks.
- **Alternatives:** Send `reasoning_effort="low"`. Not every OpenAI-compatible provider
  accepts it, so the provider-neutral fix is the bigger budget.

## Catch each SDK's base error, not only `APIError`

- **What:** `generate_draft` turns any `anthropic.AnthropicError` or `openai.OpenAIError`
  into `LlmError`.
- **Why:** `openai.LengthFinishReasonError` is not an `APIError`. It escaped and crashed
  the eval run instead of marking one row `Error`. A regression test covers it.

## An exhausted quota ends the eval as "incomplete", not as failed drafts

- **What:** After 2 cases in a row get provider errors (the model was called but no
  answer came back), the eval stops. The report opens with an "Incomplete run" warning
  that gives the reason and the last error.
- **Why:** A second free-tier run hit Gemini's daily quota and scored 8 of 9 model
  cases as failures, a "33%" that measured the quota, not the agent. A single provider
  error is still scored as a failure, because one bad row is a real outcome.
- **Alternatives:** Exclude errored cases from the pass rate. That would hide real
  failures, so they are still listed, only flagged as an incomplete run.

## Report shows draft attempts and HTTP requests separately

- **What:** `LlmClient.requests_sent` counts every request, including tenacity retries.
  The report shows "Draft attempts" (1, or 2 after a compliance retry) next to
  "HTTP requests".
- **Why:** A case retried 3 times on a 429 used to show "1 LLM call", which understated
  its cost against the quota.
