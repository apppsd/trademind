"""Verify candidate stocks' minute data and tradability (read-only).

TASK-20261027-04 / ARCH-028. Reads the observation pool produced by
scan_stock_candidates, draws a deterministic stratified sample, fetches
sina 1-minute bars per sampled stock (closed bars only), checks the
share-unit evidence, and writes one tradability record per candidate.

Zero model calls, zero orders, zero cash operations by construction:
imports are limited to market modules + stdlib + akshare.

Important scope note: closed-bar data verifies coverage/units/timestamps.
Intraday freshness (latency) needs a live-session run and stays
unverified until one happens (next trading day, on the N150 runtime).

Usage:
    .venv\\Scripts\\python.exe scripts\\verify_stock_tradability.py [--pool data/candidates/stock_pool_YYYY-MM-DD.json] [--sample-size 60]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from trademind.market.adapters.stock_minute_sina import (  # noqa: E402
    check_share_unit,
    parse_stock_minutes,
    sina_symbol,
    to_records,
)
from trademind.market.profiles import a_share_profile  # noqa: E402
from trademind.market.stock_tradability import (  # noqa: E402
    StockCapParams,
    assess_tradability,
)


def latest_pool(output_dir: Path) -> Path | None:
    pools = sorted(output_dir.glob("stock_pool_*.json"))
    return pools[-1] if pools else None


def stratified_sample(candidates: list[dict], per_board: int) -> list[dict]:
    """Deterministic: sort by code within board, even stride. Reproducible."""
    out: list[dict] = []
    boards: dict[str, list[dict]] = {}
    for cand in sorted(candidates, key=lambda c: c["code"]):
        boards.setdefault(cand["board"], []).append(cand)
    for board in sorted(boards):
        pool = boards[board]
        n = min(per_board, len(pool))
        if n == 0:
            continue
        step = len(pool) / n
        out.extend(pool[int(i * step)] for i in range(n))
    return out


def fetch_minute_rows(code: str, board: str) -> list[dict]:
    """Real akshare/sina call; kept as a named hook for test injection."""
    import akshare as ak  # noqa: PLC0415 — deferred, script-only

    return ak.stock_zh_a_minute(symbol=sina_symbol(code, board), period="1", adjust="")


def _profile_for(board: str):
    if board == "sh_main":
        return a_share_profile(board="main", market="XSHG")
    if board == "sz_main":
        return a_share_profile(board="main", market="XSHE")
    if board == "chinext":
        return a_share_profile(board="chinext", market="XSHE")
    raise ValueError(f"no profile mapping for board {board!r}")


def _day_close(rows: Any, scan_date: str) -> tuple[Decimal | None, str]:
    """Last close among bars stamped on scan_date; explains misses."""
    day_rows = [r for r in to_records(rows) if str(r.get("day", "")).startswith(scan_date)]
    if not day_rows:
        return None, f"no_bars_on_{scan_date}"
    try:
        bars = parse_stock_minutes(day_rows)
    except ValueError as exc:
        return None, f"invalid_bars: {exc}"
    if not check_share_unit(bars):
        return None, "share_unit_evidence_failed"
    last = bars[-1]
    return last.close, f"bar@{last.time}"


def verify(
    output_dir: Path,
    sample_size: int,
    now: datetime | None = None,
    pool_path: Path | None = None,
    fetch_fn=None,
    sleep_fn=time.sleep,
) -> Path:
    now = now or datetime.now(timezone.utc)
    scan_date = now.date().isoformat()
    pool_path = pool_path or latest_pool(output_dir)
    if pool_path is None:
        raise RuntimeError(f"no stock_pool_*.json under {output_dir}; run scan_stock_candidates first")
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    candidates = pool.get("candidates", [])
    per_board = max(1, sample_size // 3)
    sample = stratified_sample(candidates, per_board)

    fetch = fetch_fn or fetch_minute_rows
    caps = StockCapParams.proposal()
    records: list[dict] = []
    for cand in sample:
        code, board = cand["code"], cand["board"]
        try:
            result = fetch(code, board)  # no `or []`: DataFrame truth is ambiguous
            rows = [] if result is None else result
            close, as_of = _day_close(rows, scan_date)
        except Exception as exc:
            close, as_of = None, f"fetch_error: {type(exc).__name__}: {exc}"
        profile = _profile_for(board)
        record = assess_tradability(code, board, profile, close, as_of, caps)
        records.append(record.to_dict())
        print(f"[--] {code} {board}: {record.reason} close={record.last_close} as_of={as_of}")
        sleep_fn(0.3)

    affordable = sum(1 for r in records if r["affordable"])
    payload = {
        "generated_at": now.isoformat(),
        "scan_date": scan_date,
        "pool_path": str(pool_path),
        "sample_size": len(records),
        "affordable_count": affordable,
        "caps_source": caps.source,
        "intraday_freshness": "NOT VERIFIED (closed-bar run; live-session check pending)",
        "records": records,
    }
    target = output_dir / f"stock_tradability_{scan_date}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = None, None
    import os
    import tempfile

    fd, temp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp, target)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    print(f"[OK] tradability records written: {target}")
    print(f"     sampled={len(records)} affordable={affordable} (caps: {caps.source})")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="候选股可交易性验证（只读，零模型零下单）")
    parser.add_argument("--pool", default=None, help="候选池 JSON 路径（默认取最新）")
    parser.add_argument("--output-dir", default=str(REPO / "data" / "candidates"))
    parser.add_argument("--sample-size", type=int, default=60, help="抽样总数（板块分层）")
    args = parser.parse_args(argv)
    try:
        verify(
            Path(args.output_dir),
            args.sample_size,
            pool_path=Path(args.pool) if args.pool else None,
        )
    except Exception as exc:
        print(f"[XX] verification failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
