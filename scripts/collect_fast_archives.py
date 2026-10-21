"""Shared live facts and isolated paper archives; default is zero model calls."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from trademind.evaluation.fast_archives import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/t0-fast.local.toml')
    parser.add_argument('--output')
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--interval-seconds', type=int, default=300)
    parser.add_argument('--max-seconds', type=int, default=300)
    parser.add_argument('--probe-models', action='store_true')
    args = parser.parse_args()
    output = args.output or str(Path('data/runtime/fast-archives')/(uuid4().hex+'.json'))
    try:
        bundle = collect(args.config, output, args.samples, args.interval_seconds,
                         args.max_seconds, args.probe_models)
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__}))
        return 1
    print(json.dumps({'status': bundle['status'], 'output': output, 'contexts': len(bundle['contexts']),
                      'model_calls': bundle['current_model_calls'], 'gaps': bundle['gaps']}))
    return 0 if bundle['status'] == 'collected' else 2


if __name__ == '__main__':
    raise SystemExit(main())
