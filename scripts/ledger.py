import csv
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency guard for doctor
    yaml = None


SECONDS = {
    "daily": 24 * 60 * 60,
    "weekly": 7 * 24 * 60 * 60,
    "monthly": 30 * 24 * 60 * 60,
}


class TokenLedger:
    """Analytics and budget layer over Hermes' built-in SQLite usage ledger.

    Hermes already captures usage in ~/.hermes/state.db. This class treats that
    database as the source of truth and adds budgets, pricing, model tiers,
    forecasting, and clean tabular reports.
    """

    def __init__(
        self,
        state_db: str = "~/.hermes/state.db",
        config_dir: str = "~/.hermes/token_budgeting",
    ):
        self.state_db = Path(state_db).expanduser()
        self.config_dir = Path(config_dir).expanduser()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.budgets_path = self.config_dir / "budgets.yaml"
        self.pricing_path = self.config_dir / "pricing.yaml"
        self.model_tiers_path = self.config_dir / "model_tiers.yaml"
        self._init_config()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def _init_config(self) -> None:
        if not self.budgets_path.exists():
            self._write_yaml(
                self.budgets_path,
                {
                    "budgets": [
                        {
                            "scope": "global",
                            "name": "default",
                            "period": "daily",
                            "limit_tokens": 1_000_000,
                            "limit_usd": None,
                        }
                    ],
                    "thresholds": {"warning_pct": 80, "critical_pct": 95},
                },
            )
        if not self.pricing_path.exists():
            self._write_yaml(
                self.pricing_path,
                {
                    "defaults": {
                        "input_per_million": 1.0,
                        "output_per_million": 1.0,
                        "cache_read_per_million": 0.1,
                        "reasoning_per_million": 1.0,
                    },
                    "providers": {
                        "custom": {"input_per_million": 0.0, "output_per_million": 0.0, "cache_read_per_million": 0.0, "reasoning_per_million": 0.0},
                        "ollama": {"input_per_million": 0.0, "output_per_million": 0.0, "cache_read_per_million": 0.0, "reasoning_per_million": 0.0},
                    },
                    "models": {},
                },
            )
        if not self.model_tiers_path.exists():
            self._write_yaml(
                self.model_tiers_path,
                {
                    "tiers": {
                        "elite": ["gpt-5", "gpt-4", "claude-opus", "claude-sonnet"],
                        "balanced": ["gemini-pro", "llama-405b", "gpt-4o-mini"],
                        "utility": ["gemma", "llama", "ollama", "mini", "local"],
                    },
                    "default_tier": "balanced",
                },
            )

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        if yaml is None:
            return {}
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write_yaml(self, path: Path, data: Dict[str, Any]) -> None:
        if yaml is None:
            # Minimal fallback so first run can still create readable config.
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(data))
            return
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)

    # ------------------------------------------------------------------
    # Hermes state access
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if not self.state_db.exists():
            raise FileNotFoundError(f"Hermes state DB not found: {self.state_db}")
        conn = sqlite3.connect(str(self.state_db))
        conn.row_factory = sqlite3.Row
        return conn

    def _window_start(self, period: str) -> float:
        now = time.time()
        if period == "daily":
            today = datetime.fromtimestamp(now).date()
            return datetime.combine(today, datetime.min.time()).timestamp()
        if period in SECONDS:
            return now - SECONDS[period]
        if period == "all":
            return 0.0
        raise ValueError(f"Unsupported period: {period}")

    def _usage_rows(self, start_ts: float = 0.0) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT
                    id,
                    source,
                    model,
                    COALESCE(billing_provider, 'unknown') AS provider,
                    COALESCE(input_tokens, 0) AS input_tokens,
                    COALESCE(output_tokens, 0) AS output_tokens,
                    COALESCE(cache_read_tokens, 0) AS cache_read_tokens,
                    COALESCE(cache_write_tokens, 0) AS cache_write_tokens,
                    COALESCE(reasoning_tokens, 0) AS reasoning_tokens,
                    COALESCE(api_call_count, 0) AS api_call_count,
                    COALESCE(estimated_cost_usd, 0.0) AS estimated_cost_usd,
                    COALESCE(actual_cost_usd, 0.0) AS actual_cost_usd,
                    started_at,
                    ended_at,
                    title
                FROM sessions
                WHERE started_at >= ?
                ORDER BY started_at DESC
                """,
                (start_ts,),
            ).fetchall()

    # ------------------------------------------------------------------
    # Normalization / pricing
    # ------------------------------------------------------------------
    def tier_for_model(self, model: Optional[str]) -> str:
        model_l = (model or "unknown").lower()
        cfg = self._read_yaml(self.model_tiers_path)
        tiers = cfg.get("tiers", {})
        for tier, patterns in tiers.items():
            for pattern in patterns or []:
                if str(pattern).lower() in model_l:
                    return tier
        return cfg.get("default_tier", "balanced")

    def _rates_for(self, provider: str, model: Optional[str]) -> Dict[str, float]:
        cfg = self._read_yaml(self.pricing_path)
        rates = dict(cfg.get("defaults", {}))
        provider_rates = (cfg.get("providers", {}) or {}).get(provider, {})
        rates.update(provider_rates or {})
        model_rates = (cfg.get("models", {}) or {}).get(model or "", {})
        rates.update(model_rates or {})
        return {
            "input_per_million": float(rates.get("input_per_million", 1.0) or 0.0),
            "output_per_million": float(rates.get("output_per_million", 1.0) or 0.0),
            "cache_read_per_million": float(rates.get("cache_read_per_million", 0.1) or 0.0),
            "reasoning_per_million": float(rates.get("reasoning_per_million", 1.0) or 0.0),
        }

    def estimated_cost(self, row: sqlite3.Row) -> float:
        recorded = float(row["estimated_cost_usd"] or row["actual_cost_usd"] or 0.0)
        if recorded > 0:
            return recorded
        rates = self._rates_for(row["provider"], row["model"])
        return (
            row["input_tokens"] / 1_000_000 * rates["input_per_million"]
            + row["output_tokens"] / 1_000_000 * rates["output_per_million"]
            + row["cache_read_tokens"] / 1_000_000 * rates["cache_read_per_million"]
            + row["reasoning_tokens"] / 1_000_000 * rates["reasoning_per_million"]
        )

    @staticmethod
    def total_tokens(row: sqlite3.Row) -> int:
        return int(
            (row["input_tokens"] or 0)
            + (row["output_tokens"] or 0)
            + (row["cache_read_tokens"] or 0)
            + (row["cache_write_tokens"] or 0)
            + (row["reasoning_tokens"] or 0)
        )

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def summarize(self, period: str = "daily", group_by: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._usage_rows(self._window_start(period))
        groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for row in rows:
            tier = self.tier_for_model(row["model"])
            key: Tuple[Any, ...]
            if group_by == "provider":
                key = (row["provider"],)
            elif group_by == "model":
                key = (row["provider"], row["model"] or "unknown")
            elif group_by == "tier":
                key = (tier,)
            elif group_by == "source":
                key = (row["source"] or "unknown",)
            else:
                key = ("all",)
            item = groups.setdefault(
                key,
                {
                    "scope": " / ".join(str(x) for x in key),
                    "sessions": 0,
                    "api_calls": 0,
                    "input": 0,
                    "output": 0,
                    "cache_read": 0,
                    "reasoning": 0,
                    "total": 0,
                    "est_usd": 0.0,
                },
            )
            item["sessions"] += 1
            item["api_calls"] += int(row["api_call_count"] or 0)
            item["input"] += int(row["input_tokens"] or 0)
            item["output"] += int(row["output_tokens"] or 0)
            item["cache_read"] += int(row["cache_read_tokens"] or 0)
            item["reasoning"] += int(row["reasoning_tokens"] or 0)
            item["total"] += self.total_tokens(row)
            item["est_usd"] += self.estimated_cost(row)
        return sorted(groups.values(), key=lambda x: x["total"], reverse=True)

    def top_sessions(self, period: str = "daily", limit: int = 10) -> List[Dict[str, Any]]:
        out = []
        for row in self._usage_rows(self._window_start(period))[:5000]:
            out.append(
                {
                    "started": datetime.fromtimestamp(row["started_at"]).strftime("%Y-%m-%d %H:%M"),
                    "provider": row["provider"],
                    "model": row["model"] or "unknown",
                    "source": row["source"] or "unknown",
                    "api_calls": int(row["api_call_count"] or 0),
                    "tokens": self.total_tokens(row),
                    "est_usd": self.estimated_cost(row),
                    "session_id": row["id"],
                }
            )
        return sorted(out, key=lambda x: x["tokens"], reverse=True)[:limit]

    def budget_status(self, period: str = "daily") -> List[Dict[str, Any]]:
        cfg = self._read_yaml(self.budgets_path)
        thresholds = cfg.get("thresholds", {})
        warning = float(thresholds.get("warning_pct", 80))
        critical = float(thresholds.get("critical_pct", 95))
        rows = self._usage_rows(self._window_start(period))
        budgets = [b for b in cfg.get("budgets", []) if b.get("period") == period]
        result: List[Dict[str, Any]] = []
        for budget in budgets:
            scope = budget.get("scope", "global")
            name = budget.get("name", "default")
            matched = []
            for row in rows:
                if scope == "global":
                    matched.append(row)
                elif scope == "provider" and row["provider"] == name:
                    matched.append(row)
                elif scope == "model" and row["model"] == name:
                    matched.append(row)
                elif scope == "tier" and self.tier_for_model(row["model"]) == name:
                    matched.append(row)
                elif scope == "source" and (row["source"] or "unknown") == name:
                    matched.append(row)
            tokens = sum(self.total_tokens(r) for r in matched)
            usd = sum(self.estimated_cost(r) for r in matched)
            limit_tokens = budget.get("limit_tokens")
            limit_usd = budget.get("limit_usd")
            if limit_tokens:
                used = tokens
                limit = float(limit_tokens)
                unit = "tokens"
            elif limit_usd:
                used = usd
                limit = float(limit_usd)
                unit = "usd"
            else:
                continue
            pct = (used / limit * 100.0) if limit else 0.0
            state = "CRITICAL" if pct >= critical else "WARNING" if pct >= warning else "OK"
            result.append(
                {
                    "scope": scope,
                    "name": name,
                    "period": period,
                    "used": used,
                    "limit": limit,
                    "unit": unit,
                    "pct": pct,
                    "status": state,
                }
            )
        return result

    def forecast(self, period: str = "daily") -> Dict[str, Any]:
        rows = self._usage_rows(time.time() - 24 * 60 * 60)
        tokens_24h = sum(self.total_tokens(r) for r in rows)
        usd_24h = sum(self.estimated_cost(r) for r in rows)
        tokens_per_hour = tokens_24h / 24.0
        usd_per_day = usd_24h
        monthly_usd = usd_per_day * 30.0
        budgets = self.budget_status(period)
        exhaustion = []
        for b in budgets:
            remaining = b["limit"] - b["used"]
            if b["unit"] == "tokens":
                rate = tokens_per_hour
            else:
                rate = usd_24h / 24.0
            hours_left = remaining / rate if rate > 0 and remaining > 0 else 0.0
            exhaustion.append({**b, "hours_left": hours_left})
        return {
            "tokens_24h": tokens_24h,
            "tokens_per_hour": tokens_per_hour,
            "usd_24h": usd_24h,
            "projected_monthly_usd": monthly_usd,
            "exhaustion": exhaustion,
        }

    def set_budget(
        self,
        scope: str,
        name: str,
        period: str,
        limit_tokens: Optional[int] = None,
        limit_usd: Optional[float] = None,
    ) -> None:
        if period not in SECONDS:
            raise ValueError("period must be daily, weekly, or monthly")
        if scope not in {"global", "provider", "model", "tier", "source"}:
            raise ValueError("scope must be global, provider, model, tier, or source")
        if not limit_tokens and not limit_usd:
            raise ValueError("provide --limit-tokens or --limit-usd")
        cfg = self._read_yaml(self.budgets_path)
        budgets = cfg.setdefault("budgets", [])
        new_budget = {
            "scope": scope,
            "name": name,
            "period": period,
            "limit_tokens": limit_tokens,
            "limit_usd": limit_usd,
        }
        replaced = False
        for i, budget in enumerate(budgets):
            if budget.get("scope") == scope and budget.get("name") == name and budget.get("period") == period:
                budgets[i] = new_budget
                replaced = True
                break
        if not replaced:
            budgets.append(new_budget)
        self._write_yaml(self.budgets_path, cfg)

    def doctor(self) -> List[Tuple[str, str, str]]:
        checks: List[Tuple[str, str, str]] = []
        checks.append(("state_db", "OK" if self.state_db.exists() else "FAIL", str(self.state_db)))
        checks.append(("PyYAML", "OK" if yaml is not None else "FAIL", "python3 -m pip install PyYAML"))
        try:
            with self._connect() as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
            required = {"input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens", "billing_provider", "model"}
            missing = sorted(required - set(cols))
            checks.append(("sessions_schema", "OK" if not missing else "FAIL", "missing: " + ", ".join(missing) if missing else "required usage columns present"))
        except Exception as exc:
            checks.append(("sessions_schema", "FAIL", str(exc)))
        for name, path in [("budgets", self.budgets_path), ("pricing", self.pricing_path), ("model_tiers", self.model_tiers_path)]:
            checks.append((name, "OK" if path.exists() else "FAIL", str(path)))
        return checks


# Backward-compatible no-op-ish method for old adapter users. Hermes core is now
# the source of truth; external callers should not duplicate writes into CSV.
def log_usage(*args: Any, **kwargs: Any) -> None:
    return None
