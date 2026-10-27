"""Scan the A-share universe into the read-only observation pool.

TASK-20261027-03 / ARCH-028 observation layer. Zero model calls, zero
orders, zero cash operations by construction: imports are limited to the
market-data modules and the stdlib — pipeline/models/ledger/OMS are never
touched. Output is one JSON file per scan date (same-day reruns overwrite
atomically, so the scan is idempotent).

Usage:
    .venv\\Scripts\\python.exe scripts\\scan_stock_candidates.py [--output-dir data/candidates]

Exit codes: 0 = scan written; 1 = fetch or discovery failed.
The real akshare endpoints are unverified until a run happens on the N150
runtime (local F: copy is development-only and does not run scans).
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from trademind.market.adapters.stock_list_ak import fetch_universe_entries_v1  # noqa: E402
from trademind.market.calendar import TradingCalendar  # noqa: E402
from trademind.market.stock_discovery import discover_stock_pool, result_to_dict  # noqa: E402


def atomic_write_json(target: Path, payload: dict) -> None:
    """Write payload to target atomically (temp file + os.replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def scan(output_dir: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    entries = fetch_universe_entries_v1()
    calendar = TradingCalendar.load()
    result = discover_stock_pool(entries, now=now, calendar=calendar)
    payload = result_to_dict(result)
    payload["scan_date"] = now.date().isoformat()
    payload["universe_source"] = "akshare stock_info_sh_name_code / stock_info_sz_name_code"
    target = output_dir / f"stock_pool_{now.date().isoformat()}.json"
    atomic_write_json(target, payload)
    counts = result.counts
    print(
        f"[OK] pool written: {target}\n"
        f"     scanned={counts['scanned']} candidates={counts['candidates']} "
        f"excluded={counts['excluded']}"
    )
    print(f"     excluded_by_reason={counts['excluded_by_reason']}")
    if result.missing_checks:
        print(f"     missing_checks={list(result.missing_checks)} (criteria not evaluated)")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A股广域观察池只读扫描（零模型调用、零下单）")
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "data" / "candidates"),
        help="落盘目录（默认 data/candidates）",
    )
    args = parser.parse_args(argv)
    try:
        scan(Path(args.output_dir))
    except Exception as exc:
        print(f"[XX] scan failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
