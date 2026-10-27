"""Bootstrap the isolated stock paper account (ARCH-028 / Task27-05).

Creates ``paper-stock-1`` in its OWN sqlite database (default
``data/runtime/trademind-stock.db``) with the ARCH-028 proposed caps
(1000/450/900/100 — user-authorized 2026-10-27; frozen by Task27-05) and
funds it once. Idempotent: an existing funded account is detected and
left untouched (never double-deposits, never rewrites history).

Zero model calls, zero orders: imports are limited to storage/portfolio/
domain modules. The ETF experiment database (``paper-t0-local-1k``) is
not opened at all — isolation is by separate file, per ARCH-017 per-db
writer leases.

Usage:
    .venv\\Scripts\\python.exe scripts\\init_stock_account.py [--db data/runtime/trademind-stock.db] [--initial-cash 1000]
"""
import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from trademind.config import load_config  # noqa: E402
from trademind.domain.enums import OrderSide  # noqa: E402  (fees need the enum loaded)
from trademind.market.profiles import a_share_profile  # noqa: E402
from trademind.portfolio.ledger import Ledger  # noqa: E402
from trademind.storage.db import Database  # noqa: E402

ACCOUNT_ID = "paper-stock-1"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="建股票独立模拟账户（幂等，零模型零下单）")
    parser.add_argument("--db", default=str(REPO / "data" / "runtime" / "trademind-stock.db"))
    parser.add_argument("--initial-cash", default="1000")
    parser.add_argument("--config", default=None, help="校验用配置（只读 caps，不写库）")
    args = parser.parse_args(argv)

    if args.config:
        cfg = load_config(args.config)
        if not getattr(cfg.t0, "stock_chain", False):
            print("[XX] config is not a stock-chain config (t0.stock_chain=false)")
            return 1
        print(f"[--] config caps: single={cfg.t0.max_single_exposure} "
              f"total={cfg.t0.max_total_exposure} reserve={cfg.t0.min_cash_reserve}")

    fees = a_share_profile(board="main", market="XSHE").fees
    db = Database(f"sqlite:///{Path(args.db)}")
    ledger = Ledger(db, ACCOUNT_ID, fees)
    now = datetime.now(timezone.utc)
    snapshot = ledger.snapshot(now)
    if snapshot.cash > 0 or snapshot.positions:
        print(f"[OK] {ACCOUNT_ID} already funded at {args.db}: "
              f"cash={snapshot.cash} positions={len(snapshot.positions)} — untouched")
        db.close()
        return 0
    ledger.deposit(Decimal(args.initial_cash), at=now)
    after = ledger.snapshot(now)
    print(f"[OK] created {ACCOUNT_ID} @ {args.db}: cash={after.cash} "
          f"version={after.version} (stock caps: ARCH-028 proposal)")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
