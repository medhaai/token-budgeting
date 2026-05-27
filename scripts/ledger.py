import duckdb
import csv
import os
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

class TokenLedger:
    def __init__(self, storage_dir: str = "~/.hermes/token_ledger"):
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.usage_path = self.storage_dir / "usage.csv"
        self.budget_path = self.storage_dir / "budget.csv"
        self.pricing_path = Path("~/.hermes/skills/token-budgeting/templates/pricing.yaml").expanduser()
        self._init_csvs()

    def _init_csvs(self):
        if not self.usage_path.exists():
            with open(self.usage_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "model", "provider", "prompt_tokens", "completion_tokens", "total_tokens", "session_id"])
        
        if not self.budget_path.exists():
            with open(self.budget_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["model", "period", "limit"])
                writer.writerow(["default", "daily", 1000000])

    def get_model_cost(self, model: str, provider: str) -> float:
        """Zero-config cost estimation."""
        try:
            with open(self.pricing_path, 'r') as f:
                pricing = yaml.safe_load(f)
                # Try to find the specific model price
                prov_data = pricing.get('Provider_Defaults', {}).get(provider, {})
                # Simple fallback: 1.0 per 1M tokens if not specified
                return 1.0 
        except Exception:
            return 1.0

    def log_usage(self, model: str, prompt_tokens: int, completion_tokens: int, session_id: str = "unknown"):
        timestamp = datetime.now().isoformat()
        total = prompt_tokens + completion_tokens
        # Passive provider detection (simplified for a productized version)
        provider = "unknown"
        if "ollama" in model: provider = "ollama"
        elif "claude" in model: provider = "anthropic"
        elif "gpt" in model: provider = "openai"
        elif "gemini" in model: provider = "google"
        
        with open(self.usage_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, model, provider, prompt_tokens, completion_tokens, total, session_id])

    def get_financial_summary(self) -> str:
        conn = duckdb.connect(database=':memory:')
        conn.execute(f"CREATE VIEW usage AS SELECT * FROM read_csv_auto('{self.usage_path}')")
        
        # Use a default price of $1/1M tokens for all unknown models
        # result = conn.execute("SELECT provider, SUM(total_tokens) / 1000000.0 * 1.0 as cost FROM usage GROUP BY 1").fetchall()
        rows = conn.execute("SELECT provider, SUM(total_tokens) as sum_tokens FROM usage GROUP BY 1").fetchall()
        
        total_spend = 0.0
        summary = "Estimated Spend (using $1/1M tokens default):\n"
        for prov, tokens in rows:
            cost = (tokens / 1_000_000) * 1.0
            total_spend += cost
            summary += f"{prov}: ${cost:.4f}\n"
        
        summary += f"\nTotal: ${total_spend:.4f}"
        return summary

    def get_status(self) -> str:
        conn = duckdb.connect(database=':memory:')
        try:
            conn.execute(f"CREATE VIEW usage AS SELECT * FROM read_csv_auto('{self.usage_path}')")
            conn.execute(f"CREATE VIEW budget AS SELECT * FROM read_csv_auto('{self.budget_path}')")
            today = datetime.now().date().isoformat()
            sql = "SELECT b.model, b.period, b.limit, COALESCE(SUM(u.total_tokens), 0) as actual FROM budget b LEFT JOIN usage u ON (u.model = b.model OR b.model = 'default') WHERE u.timestamp LIKE '" + today + "%' OR u.timestamp IS NULL GROUP BY 1, 2, 3"
            result = conn.execute(sql).df()
            if result.empty: return "No usage recorded yet."
            result['consumed_pct'] = (result['actual'] / result['limit'] * 100).round(2)
            def get_flag(pct):
                if pct >= 95: return "🔴 CRITICAL"
                if pct >= 80: return "🟡 WARNING"
                return "🟢 OK"
            result['status'] = result['consumed_pct'].apply(get_flag)
            return result.to_string()
        except Exception as e:
            return f"Status error: {e}"

    def forecast(self) -> str:
        conn = duckdb.connect(database=':memory:')
        try:
            conn.execute(f"CREATE VIEW usage AS SELECT * FROM read_csv_auto('{self.usage_path}')")
            sql = "SELECT SUM(total_tokens) / 24.0 as tokens_per_hour FROM usage WHERE timestamp >= (now() - INTERVAL '24 hours')"
            res = conn.execute(sql).fetchone()
            burn_rate = res[0] if res and res[0] else 0
            return f"Current burn rate: {burn_rate:.2f} tokens/hour." if burn_rate > 0 else "Insufficient data to forecast."
        except Exception as e:
            return f"Forecast error: {e}"

    def set_budget(self, model: str, period: str, limit: int):
        rows = []
        updated = False
        if self.budget_path.exists():
            with open(self.budget_path, 'r') as f:
                reader = csv.reader(f)
                header = next(reader)
                for row in reader:
                    if row[0] == model and row[1] == period:
                        rows.append([model, period, limit])
                        updated = True
                    else:
                        rows.append(row)
        if not updated:
            rows.append([model, period, limit])
        with open(self.budget_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["model", "period", "limit"])
            writer.writerows(rows)
