import csv
import json
import os
import re
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
        self.subscriptions_path = self.config_dir / "subscriptions.yaml"
        self.pricing_path = self.config_dir / "pricing.yaml"
        self.model_tiers_path = self.config_dir / "model_tiers.yaml"
        self.model_aliases_path = self.config_dir / "model_aliases.yaml"
        self.hermes_home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        self.hermes_config_path = self.hermes_home / "config.yaml"
        self.hermes_auth_path = self.hermes_home / "auth.json"
        self.hermes_env_path = self.hermes_home / ".env"
        self._init_config()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def _init_config(self) -> None:
        if not self.budgets_path.exists():
            self._write_yaml(
                self.budgets_path,
                {
                    "budgets": [],
                    "thresholds": {"warning_pct": 80, "critical_pct": 95},
                    "notes": "No arbitrary default budget. Add verified provider/model/tier budgets only after checking subscription limits, invoices, or user-confirmed spend caps.",
                },
            )
        else:
            self._remove_placeholder_default_budget()
        if not self.subscriptions_path.exists():
            self._write_yaml(
                self.subscriptions_path,
                {
                    "subscriptions": [],
                    "notes": "Add verified subscription details here. Unknown limits should remain null, not guessed.",
                    "example": {
                        "provider": "openai-codex",
                        "plan": "Team/Pro/API/etc",
                        "reset_period": "monthly",
                        "included_tokens": None,
                        "monthly_usd_budget": None,
                        "verified": False,
                        "source": "user/provider dashboard/invoice",
                    },
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
        if not self.model_aliases_path.exists():
            self._write_yaml(
                self.model_aliases_path,
                {
                    "provider_aliases": {
                        "custom|http://127.0.0.1:11434/v1": "ollama",
                        "ollama-launch": "ollama",
                        "gemini": "google",
                    },
                    "model_aliases": {
                        "openai-codex|gemma4:31b-cloud": "gpt-5.5",
                    },
                    "notes": "Aliases correct Hermes state rows when provider/model labels are stale after a runtime model switch. Keep evidence in git history or session notes.",
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

    def _remove_placeholder_default_budget(self) -> None:
        """Remove the old generated 1M/day default if it is the only budget.

        This avoids presenting an arbitrary number as a real budget. User- or
        subscription-verified budgets are preserved.
        """
        cfg = self._read_yaml(self.budgets_path)
        budgets = cfg.get("budgets", []) or []
        if len(budgets) != 1:
            return
        budget = budgets[0]
        is_placeholder = (
            budget.get("scope") == "global"
            and budget.get("name") == "default"
            and budget.get("period") == "daily"
            and int(budget.get("limit_tokens") or 0) == 1_000_000
            and not budget.get("limit_usd")
        )
        if is_placeholder:
            cfg["budgets"] = []
            cfg["notes"] = "Removed old generated 1M/day placeholder. Add only verified subscription/user-confirmed budgets."
            self._write_yaml(self.budgets_path, cfg)

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
                    COALESCE(billing_base_url, '') AS billing_base_url,
                    COALESCE(billing_mode, '') AS billing_mode,
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
    def display_provider(self, row_or_provider: Any, base_url: str = "") -> str:
        cfg = self._read_yaml(self.model_aliases_path)
        if isinstance(row_or_provider, sqlite3.Row):
            provider = row_or_provider["provider"] or "unknown"
            base_url = row_or_provider["billing_base_url"] or ""
        else:
            provider = str(row_or_provider or "unknown")
        aliases = cfg.get("provider_aliases", {}) or {}
        if f"{provider}|{base_url}" in aliases:
            return aliases[f"{provider}|{base_url}"]
        if provider in aliases:
            return aliases[provider]
        if provider == "custom" and "127.0.0.1:11434" in base_url:
            return "ollama"
        return provider

    def display_model(self, row_or_model: Any, provider: Optional[str] = None) -> str:
        cfg = self._read_yaml(self.model_aliases_path)
        if isinstance(row_or_model, sqlite3.Row):
            raw_model = row_or_model["model"] or "unknown"
            raw_provider = row_or_model["provider"] or provider or "unknown"
        else:
            raw_model = str(row_or_model or "unknown")
            raw_provider = provider or "unknown"
        aliases = cfg.get("model_aliases", {}) or {}
        return aliases.get(f"{raw_provider}|{raw_model}", aliases.get(raw_model, raw_model))

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
            display_provider = self.display_provider(row)
            display_model = self.display_model(row)
            tier = self.tier_for_model(display_model)
            key: Tuple[Any, ...]
            if group_by == "provider":
                key = (display_provider,)
            elif group_by == "model":
                key = (display_provider, display_model)
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

    def enabled_providers(self) -> List[Dict[str, Any]]:
        """Configured providers, including providers with zero usage."""
        found: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def add(provider: str, model: str = "(configured)", source: str = "config") -> None:
            provider = self.display_provider(provider)
            key = (provider, model or "(configured)")
            found.setdefault(key, {"provider": provider, "model": model or "(configured)", "enabled": "yes", "source": source})

        if self.hermes_config_path.exists():
            cfg = self._read_yaml(self.hermes_config_path)
            model_cfg = cfg.get("model", {}) or {}
            if model_cfg.get("provider"):
                add(str(model_cfg.get("provider")), str(model_cfg.get("default") or "(configured)"), "config.model")
            providers = cfg.get("providers", {}) or {}
            for name, pdata in providers.items():
                models = pdata.get("models") or [pdata.get("default_model") or "(configured)"] if isinstance(pdata, dict) else ["(configured)"]
                for model in models:
                    add(str(name), str(model or "(configured)"), "config.providers")

        if self.hermes_auth_path.exists():
            try:
                auth = json.loads(self.hermes_auth_path.read_text())
                providers = auth.get("providers", {}) or {}
                for provider in providers.keys():
                    add(str(provider), "(configured)", "auth")
                pool = auth.get("credential_pool", {}) or {}
                for provider, creds in pool.items():
                    if creds:
                        add(str(provider), "(configured)", "auth_pool")
            except Exception:
                pass

        env_map = {
            "OPENROUTER_API_KEY": "openrouter",
            "ANTHROPIC_API_KEY": "anthropic",
            "OPENAI_API_KEY": "openai",
            "GOOGLE_API_KEY": "google",
            "GEMINI_API_KEY": "google",
            "DEEPSEEK_API_KEY": "deepseek",
            "XAI_API_KEY": "xai",
            "HF_TOKEN": "huggingface",
            "GLM_API_KEY": "zai/glm",
            "MINIMAX_API_KEY": "minimax",
            "KIMI_API_KEY": "kimi",
            "DASHSCOPE_API_KEY": "dashscope",
            "GROQ_API_KEY": "groq",
            "MISTRAL_API_KEY": "mistral",
        }
        if self.hermes_env_path.exists():
            for line in self.hermes_env_path.read_text().splitlines():
                match = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.+)", line)
                if match and match.group(1) in env_map and match.group(2).strip().strip("'\""):
                    add(env_map[match.group(1)], "(configured)", ".env")
        return sorted(found.values(), key=lambda r: (r["provider"], r["model"]))

    def default_report(self, period: str = "daily", include_unused: bool = True) -> List[Dict[str, Any]]:
        """Default concise view: provider, model, sessions, calls, tokens, cost."""
        rows = self.summarize(period, "model")
        out: List[Dict[str, Any]] = []
        for row in rows:
            parts = str(row["scope"]).split(" / ", 1)
            provider = parts[0]
            model = parts[1] if len(parts) > 1 else "unknown"
            out.append(
                {
                    "provider": provider,
                    "model": model,
                    "enabled": "yes",
                    "sessions": row["sessions"],
                    "api_calls": row["api_calls"],
                    "input": row["input"],
                    "output": row["output"],
                    "cache_read": row["cache_read"],
                    "reasoning": row["reasoning"],
                    "total": row["total"],
                    "est_usd": row["est_usd"],
                }
            )
        if include_unused:
            used = {(row["provider"], row["model"]) for row in out}
            for provider in self.enabled_providers():
                key = (provider["provider"], provider["model"])
                if key in used:
                    continue
                out.append(
                    {
                        "provider": provider["provider"],
                        "model": provider["model"],
                        "enabled": provider["enabled"],
                        "sessions": 0,
                        "api_calls": 0,
                        "input": 0,
                        "output": 0,
                        "cache_read": 0,
                        "reasoning": 0,
                        "total": 0,
                        "est_usd": 0.0,
                    }
                )
        return sorted(out, key=lambda x: (x["total"] == 0, x["provider"], x["model"]))

    def top_sessions(self, period: str = "daily", limit: int = 10) -> List[Dict[str, Any]]:
        out = []
        for row in self._usage_rows(self._window_start(period))[:5000]:
            out.append(
                {
                    "started": datetime.fromtimestamp(row["started_at"]).strftime("%Y-%m-%d %H:%M"),
                    "provider": self.display_provider(row),
                    "model": self.display_model(row),
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
                elif scope == "provider" and self.display_provider(row) == name:
                    matched.append(row)
                elif scope == "model" and self.display_model(row) == name:
                    matched.append(row)
                elif scope == "tier" and self.tier_for_model(self.display_model(row)) == name:
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

    def subscriptions(self) -> List[Dict[str, Any]]:
        cfg = self._read_yaml(self.subscriptions_path)
        rows = []
        for sub in cfg.get("subscriptions", []) or []:
            rows.append(
                {
                    "provider": sub.get("provider", "unknown"),
                    "plan": sub.get("plan", "unknown"),
                    "reset_period": sub.get("reset_period", "unknown"),
                    "included_tokens": sub.get("included_tokens") or "unknown",
                    "monthly_usd_budget": sub.get("monthly_usd_budget") or "unknown",
                    "verified": bool(sub.get("verified", False)),
                    "source": sub.get("source", "unknown"),
                }
            )
        return rows

    def budget_questions(self) -> List[Dict[str, Any]]:
        providers = self.summarize("monthly", "provider")
        configured = {row.get("provider") for row in self.subscriptions()}
        questions = []
        for row in providers:
            provider = row["scope"]
            if row["total"] <= 0:
                continue
            if provider not in configured:
                questions.append(
                    {
                        "provider": provider,
                        "question": "Verify subscription/plan, reset period, included tokens or monthly USD cap.",
                        "recent_tokens": row["total"],
                        "recent_est_usd": row["est_usd"],
                    }
                )
        return questions

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
        budget_cfg = self._read_yaml(self.budgets_path)
        sub_cfg = self._read_yaml(self.subscriptions_path)
        has_budget = bool(budget_cfg.get("budgets"))
        has_verified_sub = any(s.get("verified") and (s.get("included_tokens") or s.get("monthly_usd_budget")) for s in (sub_cfg.get("subscriptions", []) or []))
        checks.append(("budget_config", "OK" if has_budget or has_verified_sub else "VERIFY", "No verified budget/subscription limits configured" if not (has_budget or has_verified_sub) else "verified limits present"))
        for name, path in [("budgets", self.budgets_path), ("subscriptions", self.subscriptions_path), ("pricing", self.pricing_path), ("model_tiers", self.model_tiers_path), ("model_aliases", self.model_aliases_path)]:
            checks.append((name, "OK" if path.exists() else "FAIL", str(path)))
        return checks


# Backward-compatible no-op-ish method for old adapter users. Hermes core is now
# the source of truth; external callers should not duplicate writes into CSV.
def log_usage(*args: Any, **kwargs: Any) -> None:
    return None
