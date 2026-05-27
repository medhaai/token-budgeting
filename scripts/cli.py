import argparse
import sys
from ledger import TokenLedger

def main():
    parser = argparse.ArgumentParser(description="Hermes Token Treasury CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status")
    subparsers.add_parser("forecast")
    subparsers.add_parser("finance")
    
    budget_parser = subparsers.add_parser("set-budget")
    budget_parser.add_argument("--model", default="default")
    budget_parser.add_argument("--period", required=True, choices=["daily", "weekly", "monthly"])
    budget_parser.add_argument("--limit", type=int, required=True)

    args = parser.parse_args()
    ledger = TokenLedger()

    if args.command == "status":
        print(ledger.get_status())
    elif args.command == "forecast":
        print(ledger.forecast())
    elif args.command == "finance":
        print(ledger.get_financial_summary())
    elif args.command == "set-budget":
        ledger.set_budget(args.model, args.period, args.limit)
        print(f"Budget updated for {args.model} ({args.period}): {args.limit} tokens")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
