import argparse
from typing import Any, Dict, Iterable, List

from ledger import TokenLedger


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def fmt_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return str(value)


def fmt_usd(value: Any) -> str:
    try:
        return f"${float(value):,.4f}"
    except Exception:
        return str(value)


def print_table(rows: List[Dict[str, Any]], columns: List[str], empty: str = "No rows.") -> None:
    if not rows:
        print(empty)
        return
    labels = {col: col.replace("_", " ").title() for col in columns}
    table = []
    for row in rows:
        rendered = []
        for col in columns:
            val = row.get(col, "")
            if col in {"input", "output", "cache_read", "reasoning", "total", "tokens", "api_calls", "sessions", "recent_tokens", "included_tokens"}:
                val = fmt_int(val)
            elif col in {"est_usd", "usd_24h", "projected_monthly_usd", "recent_est_usd", "monthly_usd_budget"}:
                val = fmt_usd(val)
            elif col in {"tokens_per_hour"}:
                val = fmt_int(round(float(val)))
            elif col in {"pct"}:
                val = fmt_float(val, 2) + "%"
            elif col in {"used", "limit"}:
                if row.get("unit") == "usd":
                    val = fmt_usd(val)
                else:
                    val = fmt_int(val)
            elif col in {"hours_left"}:
                val = fmt_float(val, 1)
            rendered.append(str(val))
        table.append(rendered)
    widths = []
    for i, col in enumerate(columns):
        widths.append(max(len(labels[col]), *(len(r[i]) for r in table)))
    header = "  ".join(labels[col].ljust(widths[i]) for i, col in enumerate(columns))
    sep = "  ".join("-" * widths[i] for i in range(len(columns)))
    print(header)
    print(sep)
    for rendered in table:
        print("  ".join(rendered[i].ljust(widths[i]) for i in range(len(columns))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes token budget reports")
    parser.add_argument("--state-db", default="~/.hermes/state.db", help="Hermes state.db path")
    parser.add_argument("--config-dir", default="~/.hermes/token_budgeting", help="Token budgeting config directory")
    parser.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="daily", help="Default report period")
    subparsers = parser.add_subparsers(dest="command")

    report = subparsers.add_parser("report", help="Default provider/model usage table")
    report.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="daily")

    status = subparsers.add_parser("status", help="Verified budget/subscription status")
    status.add_argument("--period", choices=["daily", "weekly", "monthly"], default="daily")

    finance = subparsers.add_parser("finance", help="Provider/model spend table")
    finance.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="daily")
    finance.add_argument("--by", choices=["provider", "model", "tier", "source"], default="provider")

    forecast = subparsers.add_parser("forecast", help="Burn-rate forecast")
    forecast.add_argument("--period", choices=["daily", "weekly", "monthly"], default="daily")

    providers = subparsers.add_parser("providers", help="Provider usage table")
    providers.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="daily")

    models = subparsers.add_parser("models", help="Model usage table")
    models.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="daily")

    tiers = subparsers.add_parser("tiers", help="Tier usage table")
    tiers.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="daily")

    sessions = subparsers.add_parser("sessions", help="Top token-heavy sessions")
    sessions.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="daily")
    sessions.add_argument("--limit", type=int, default=10)

    budget = subparsers.add_parser("set-budget", help="Set or update a passive budget")
    budget.add_argument("--scope", required=True, choices=["global", "provider", "model", "tier", "source"])
    budget.add_argument("--name", default="default")
    budget.add_argument("--period", required=True, choices=["daily", "weekly", "monthly"])
    budget.add_argument("--limit-tokens", type=int)
    budget.add_argument("--limit-usd", type=float)

    subparsers.add_parser("doctor", help="Check local setup")
    subparsers.add_parser("subscriptions", help="Show verified subscription/budget sources")
    subparsers.add_parser("questions", help="Show budget questions the AI should ask/verify")

    args = parser.parse_args()
    ledger = TokenLedger(state_db=args.state_db, config_dir=args.config_dir)

    command = args.command or "report"

    if command == "report":
        print_table(ledger.default_report(args.period), ["provider", "model", "sessions", "api_calls", "input", "output", "cache_read", "reasoning", "total", "est_usd"])
    elif command == "status":
        rows = ledger.budget_status(args.period)
        if rows:
            print_table(rows, ["scope", "name", "period", "used", "limit", "unit", "pct", "status"])
        else:
            print("No verified budget configured for this period. Use `questions` to see what to verify.")
    elif args.command == "finance":
        print_table(ledger.summarize(args.period, args.by), ["scope", "sessions", "api_calls", "input", "output", "cache_read", "reasoning", "total", "est_usd"])
    elif args.command == "forecast":
        fc = ledger.forecast(args.period)
        print_table(
            [
                {
                    "scope": "last_24h",
                    "tokens": fc["tokens_24h"],
                    "tokens_per_hour": fc["tokens_per_hour"],
                    "usd_24h": fc["usd_24h"],
                    "projected_monthly_usd": fc["projected_monthly_usd"],
                }
            ],
            ["scope", "tokens", "tokens_per_hour", "usd_24h", "projected_monthly_usd"],
        )
        if fc["exhaustion"]:
            print()
            print_table(fc["exhaustion"], ["scope", "name", "period", "used", "limit", "unit", "pct", "status", "hours_left"])
    elif args.command == "providers":
        print_table(ledger.summarize(args.period, "provider"), ["scope", "sessions", "api_calls", "input", "output", "cache_read", "reasoning", "total", "est_usd"])
    elif args.command == "models":
        print_table(ledger.summarize(args.period, "model"), ["scope", "sessions", "api_calls", "input", "output", "cache_read", "reasoning", "total", "est_usd"])
    elif args.command == "tiers":
        print_table(ledger.summarize(args.period, "tier"), ["scope", "sessions", "api_calls", "input", "output", "cache_read", "reasoning", "total", "est_usd"])
    elif args.command == "sessions":
        print_table(ledger.top_sessions(args.period, args.limit), ["started", "provider", "model", "source", "api_calls", "tokens", "est_usd", "session_id"])
    elif args.command == "set-budget":
        ledger.set_budget(args.scope, args.name, args.period, args.limit_tokens, args.limit_usd)
        print(f"Updated budget: scope={args.scope} name={args.name} period={args.period}")
    elif command == "doctor":
        rows = [{"check": c, "status": s, "detail": d} for c, s, d in ledger.doctor()]
        print_table(rows, ["check", "status", "detail"])
    elif command == "subscriptions":
        print_table(ledger.subscriptions(), ["provider", "plan", "reset_period", "included_tokens", "monthly_usd_budget", "verified", "source"], empty="No verified subscriptions configured.")
    elif command == "questions":
        print_table(ledger.budget_questions(), ["provider", "question", "recent_tokens", "recent_est_usd"], empty="No budget questions; subscription/budget sources are configured.")


if __name__ == "__main__":
    main()
