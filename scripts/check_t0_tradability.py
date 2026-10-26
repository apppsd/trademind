"""只读 T0 资金可达性诊断（TASK-20261025-04）。

输入：显式 JSON 快照（账户现金/预留、持仓、在途订单、逐标的最近价格及可见时间、冻结限额）。
行为：不读 .env、不联网、不写生产数据；逐标的给出 资金可达 / 仅观察 / 数据不足
与具体被哪条冻结限额挡住。资金可达不等于下单授权。

用法：
  python scripts/check_t0_tradability.py --snapshot data/runtime/tradability-sample.json \
      [--max-price-age 300] [--output-json 路径] [--output-md 路径]

规则来源：data/etf_whitelist.json 经 trademind.market.profiles.get_profile
（只读，未覆盖的标的按"无核验规则"报告，不猜测规则）。
"""
import argparse
import json
import sys
from pathlib import Path

from trademind.evaluation.tradability import evaluate_from_raw, render_markdown
from trademind.market.profiles import get_profile


def _resolver(instrument_id):
    try:
        return get_profile(instrument_id)
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, help='snapshot JSON path')
    parser.add_argument('--max-price-age', type=int, default=300,
                        help='max seconds between price_time and as_of (default 300)')
    parser.add_argument('--output-json', help='write JSON report to this path')
    parser.add_argument('--output-md', help='write Markdown report to this path')
    args = parser.parse_args()
    try:
        raw = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'无法读取快照: {exc}', file=sys.stderr)
        return 2
    try:
        report = evaluate_from_raw(raw, _resolver, args.max_price_age)
    except ValueError as exc:
        print(f'快照不合法: {exc}', file=sys.stderr)
        return 2
    markdown = render_markdown(report)
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding='utf-8')
    print(markdown)
    return 0


if __name__ == '__main__':
    sys.exit(main())
