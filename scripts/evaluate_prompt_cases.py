"""Saved-output evaluation only: no .env, config credentials or model calls."""
import argparse
import json
from pathlib import Path
from trademind.evaluation.prompt_eval import evaluate, compare_reports, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--responses', required=True)
    parser.add_argument('--baseline-responses')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        sources = {Path(p).resolve() for p in (args.cases, args.responses, args.baseline_responses) if p}
        targets = {Path(args.output).resolve(), Path(args.output).with_suffix('.md').resolve()}
        if sources & targets:
            raise ValueError('report must not overwrite input')
        data = json.loads(Path(args.cases).read_text(encoding='utf-8'))
        bundle = json.loads(Path(args.responses).read_text(encoding='utf-8'))
        report = evaluate(data, bundle)
        if args.baseline_responses:
            baseline = evaluate(data, json.loads(Path(args.baseline_responses).read_text(encoding='utf-8')))
            report = compare_reports(baseline, report)
        write_report(args.output, report)
        print(json.dumps({'status': report['status']}))
        return 0 if report['status'] in ('pass', 'compared') else (1 if report['status'] == 'fail' else 2)
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
