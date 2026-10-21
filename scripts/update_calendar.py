"""Offline calendar update: merge an operator-supplied official calendar CSV.

Usage (no network access performed here):
    python scripts/update_calendar.py --source-csv path/to/official-2027.csv \
        --source "SSE official calendar announcement" [--version 2027-official] \
        [--target data/fixtures/calendar_xshg.csv] [--allow-shrink]

The source file must use the local calendar shape: a `date` header plus one
ISO trading-day row per line. Only years the official source announces may
be included; this script never invents trading days.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from trademind.errors import ConfigError
from trademind.market.calendar_update import merge_official_calendar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-csv', required=True)
    parser.add_argument('--source', required=True, help='first-hand source label recorded in metadata')
    parser.add_argument('--version')
    parser.add_argument('--target', default='data/fixtures/calendar_xshg.csv')
    parser.add_argument('--allow-shrink', action='store_true')
    args = parser.parse_args()
    try:
        meta = merge_official_calendar(Path(args.source_csv), Path(args.target),
                                       source=args.source, version=args.version,
                                       allow_shrink=args.allow_shrink)
    except (ConfigError, OSError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({'status': 'updated', **meta}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
