---
name: token-budgeting
category: devops
description: Passive token finance and budget reports over Hermes' built-in state.db usage ledger.
version: 2.0.0
author: User & Hermes
---

# Token Budgeting Skill

Token-budgeting is the finance, governance, and reporting layer for Hermes LLM usage.

Hermes already records raw usage in:

```bash
~/.hermes/state.db
```

This skill treats that SQLite database as the source of truth. It does not duplicate every request into a separate CSV ledger. Its job is to turn Hermes' raw session data into simple, clean tabular reports: budgets, spend, burn rate, provider/model breakdowns, and passive alerts.

## What Hermes Core Owns

Hermes core captures raw session usage in the `sessions` table:

- `model`
- `billing_provider`
- `billing_base_url`
- `billing_mode`
- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `api_call_count`
- `estimated_cost_usd`
- `actual_cost_usd`
- `source`
- `started_at`
- `ended_at`

Do not reimplement basic token capture in this skill unless Hermes core is unavailable.

## What This Skill Adds

- Budget policy by global/provider/model/tier/source.
- Clean tabular reports for status, finance, providers, models, tiers, sessions, and forecasts.
- Pricing normalization for providers and models.
- Specialist tiering: elite / balanced / utility / local.
- Burn-rate forecasting: tokens/hour, projected monthly spend, and time-to-budget exhaustion.
- Passive alert state: OK / WARNING / CRITICAL. The skill reports; it does not hard-block usage.
- Setup diagnostics via `doctor`.

## Commands

Run from the skill repo or installed skill directory:

```bash
python3 scripts/cli.py doctor
python3 scripts/cli.py status
python3 scripts/cli.py finance --by provider --period daily
python3 scripts/cli.py finance --by model --period daily
python3 scripts/cli.py providers --period daily
python3 scripts/cli.py models --period daily
python3 scripts/cli.py tiers --period daily
python3 scripts/cli.py sessions --period daily --limit 10
python3 scripts/cli.py forecast --period daily
```

Set budgets:

```bash
python3 scripts/cli.py set-budget --scope global --name default --period daily --limit-tokens 1000000
python3 scripts/cli.py set-budget --scope provider --name openai-codex --period daily --limit-usd 5
python3 scripts/cli.py set-budget --scope tier --name elite --period daily --limit-tokens 500000
```

Use a non-default Hermes state database:

```bash
python3 scripts/cli.py --state-db /path/to/state.db status
```

## Config Files

Runtime config is created under:

```bash
~/.hermes/token_budgeting/
```

Files:

- `budgets.yaml`: budget rules and thresholds.
- `pricing.yaml`: provider/model price estimates per 1M tokens.
- `model_tiers.yaml`: substring mappings from model names to tiers.

Templates live in this skill repo under `templates/`.

## Report Style

Reports should be terminal-friendly tables: no verbose prose, no charts required, easy to paste into WhatsApp or a terminal.

Important columns:

- scope
- sessions
- api_calls
- input
- output
- cache_read
- reasoning
- total
- est_usd
- pct
- status

## Architecture

```text
Hermes core
  -> ~/.hermes/state.db
      sessions table with raw token/cost/provider/model data

Token-budgeting skill
  -> reads state.db
  -> applies budgets.yaml + pricing.yaml + model_tiers.yaml
  -> prints clean passive reports
```

DuckDB is optional for future high-volume analytics. SQLite is enough for the current source-of-truth path.

## Dependencies

Minimum runtime:

```bash
python3 -m pip install PyYAML
```

Optional/future analytics:

```bash
python3 -m pip install duckdb PyYAML
```

## Redundant Pieces Removed / Deprecated

- `usage.csv` is not the primary ledger.
- CSV append logging is deprecated.
- The adapter is retained only as a backward-compatible no-op for old Hermes soft-hook imports.
- CSV locking concerns are no longer central because Hermes state is SQLite.

## Verification

```bash
python3 scripts/cli.py doctor
python3 scripts/cli.py status
python3 scripts/cli.py providers --period daily
python3 scripts/cli.py sessions --period daily --limit 5
```

Expected: simple tables, no tracebacks.
