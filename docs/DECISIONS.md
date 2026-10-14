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
