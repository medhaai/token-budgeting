---
name: token-budgeting
category: devops
description: Professional token usage tracking, budgeting, and forecasting. Treats LLM tokens as a corporate currency with daily/weekly/monthly quotas.
version: 1.0.0
author: User & Hermes
---

# Token Budgeting Skill

This skill provides a "Token Ledger" for LLM operations. It enables real-time tracking of token consumption, budget enforcement, and burn-rate forecasting using DuckDB and CSV storage.

## 🎯 Capabilities
- **Real-time Tracking**: Automatically logs prompt and completion tokens via the `adapter.py` hook.
- **Budgeting**: Set and monitor limits across different time windows (Daily, Weekly, Monthly).
- **Forecasting**: Predicts budget exhaustion based on historical burn rates.
- **Analytics**: High-performance querying of usage patterns using DuckDB.

## 🛠️ Components

### 1. Core Ledger (`scripts/ledger.py`)
The engine that manages `usage.csv` and `budget.csv`. It uses DuckDB to provide analytical views of spending.

### 2. Management CLI (`scripts/cli.py`)
A standalone tool to manage the treasury.
- `status`: See actual vs budgeted usage.
- `forecast`: See tokens/hour burn rate.
- `set-budget --period <daily|weekly|monthly> --limit <N>`: Update quotas.

### 3. Hermes Adapter (`scripts/adapter.py`)
The integration layer. It should be called in the `AIAgent` loop to record usage metadata from every LLM response.

## 🚀 Integration Guide

To activate real-time tracking in a Hermes Agent:
1. Import the adapter: `from skills.token_budgeting.scripts.adapter import token_adapter`
2. In the main `AIAgent` loop, after the API response:
   ```python
   token_adapter.record_response(response, model_name=self.model, session_id=self.session_id)
   ```

## ⚠️ Pitfalls
- **CSV Locking**: Because it uses standard CSV appends, extreme high-concurrency (100+ parallel sub-agents) might require a transition to a proper SQLite/DuckDB file. For standard agent use, CSV is optimal for transparency.
- **Budget Resolution**: Budget is checked against `model` name. If using a generic `default` budget, ensure model names are normalized.

## ✅ Verification
Run the following to verify the installation:
```bash
python ~/.hermes/skills/token-budgeting/scripts/cli.py status
```
