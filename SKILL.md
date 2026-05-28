---
name: token-budgeting
category: devops
description: Passive token finance and budget reports over Hermes' built-in state.db usage ledger.
version: 2.1.0
author: User & Hermes
---

# Token Budgeting Skill

Token-budgeting is the finance, governance, and reporting layer for Hermes LLM usage.

Hermes already records raw usage in:

```bash
~/.hermes/state.db
```

This skill treats that SQLite database as the source of truth. It does not duplicate every request into a separate CSV ledger.

## Core Rule: No Arbitrary Budgets

Do not invent a default token or dollar budget. Budgets must come from one of:

- provider subscription limits
- provider dashboard / invoice data
- user-confirmed monthly spend cap
- user-confirmed token allowance

If budget/subscription information is missing, report usage only and ask/verify the needed budget facts before claiming OK/WARNING/CRITICAL.

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

## What This Skill Adds

- Clean default provider/model usage report.
- Budget policy by global/provider/model/tier/source, but only when verified.
- Subscription-aware budget verification prompts.
- Pricing normalization for providers and models.
- Specialist tiering: elite / balanced / utility / local.
- Burn-rate forecasting: tokens/hour and projected monthly spend.
- Passive alert state: OK / WARNING / CRITICAL only for verified budgets.
- Setup diagnostics via `doctor`.

## Default View

The default command should show a simple terminal-friendly table grouped by provider and model:

```bash
python3 scripts/cli.py
```

Default columns:

- provider
- model
- sessions
- api_calls
- input
- output
- cache_read
- reasoning
- total
- est_usd

This is the preferred quick answer when the user asks: "what does token budget usage look like?"

## Commands

```bash
python3 scripts/cli.py                         # default provider/model report
python3 scripts/cli.py report --period daily
python3 scripts/cli.py status                  # verified budgets only
python3 scripts/cli.py questions               # what budget facts to ask/verify
python3 scripts/cli.py subscriptions           # verified subscription sources
python3 scripts/cli.py finance --by provider --period daily
python3 scripts/cli.py finance --by model --period daily
python3 scripts/cli.py providers --period daily
python3 scripts/cli.py models --period daily
python3 scripts/cli.py tiers --period daily
python3 scripts/cli.py sessions --period daily --limit 10
python3 scripts/cli.py forecast --period daily
python3 scripts/cli.py doctor
```

Set verified budgets only after asking/checking source data:

```bash
python3 scripts/cli.py set-budget --scope provider --name openai-codex --period daily --limit-usd 5
python3 scripts/cli.py set-budget --scope tier --name elite --period daily --limit-tokens 500000
```

## Config Files

Runtime config is created under:

```bash
~/.hermes/token_budgeting/
```

Files:

- `budgets.yaml`: verified budget rules and thresholds. Starts empty; no arbitrary default.
- `subscriptions.yaml`: verified provider plan/reset/allowance/spend-cap facts.
- `pricing.yaml`: provider/model price estimates per 1M tokens.
- `model_tiers.yaml`: substring mappings from model names to tiers.

Templates live in this skill repo under `templates/`.

## Budget Verification Workflow

When no verified budget exists:

1. Run `python3 scripts/cli.py` to report actual usage.
2. Run `python3 scripts/cli.py questions` to list providers that need verification.
3. Ask the user or check provider dashboards/invoices for:
   - provider/subscription plan
   - reset period
   - included token allowance, if any
   - monthly USD cap or expected spend
   - whether local/custom providers should be treated as zero-cost, fixed-cost, or capped
4. Record only verified values in `subscriptions.yaml` or with `set-budget`.
5. Then use `status` for OK/WARNING/CRITICAL.

## Architecture

```text
Hermes core
  -> ~/.hermes/state.db
      sessions table with raw token/cost/provider/model data

Token-budgeting skill
  -> reads state.db
  -> applies verified budgets/subscriptions + pricing + model tiers
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

## Deprecated

- `usage.csv` is not the primary ledger.
- CSV append logging is deprecated.
- The adapter is retained only as a backward-compatible no-op for old Hermes soft-hook imports.
- CSV locking concerns are no longer central because Hermes state is SQLite.
