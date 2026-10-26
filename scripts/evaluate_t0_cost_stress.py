"""离线成本与撮合压力评估（TASK-20261025-05）。

输入：显式 JSON 档案包（绑定时点的意图、后续可见分钟bar、隔离账户配置、
模拟费率、模型成本及unknown标记）。只读档案，不读凭据、不补发模型请求、
不写生产数据、不联网。

输出：A=往返盈亏平衡成本表（无需成交）；B=冻结场景下的隔离回放
（基线5bps/边、10/20bps/边，最低佣金0.1/1/5，延迟0/1/2根bar，参与率10%）。
数据不足如实报告 insufficient_data / incomplete，不以工具完成宣称策略盈利。

用法：
  python scripts/evaluate_t0_cost_stress.py --bundle data/runtime/cost-bundle.json \
      [--output-json 路径] [--output-md 路径]
"""
import argparse
import json
import sys
from pathlib import Path

from trademind.evaluation.cost_stress import evaluate_bundle, render_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, help='archive bundle JSON path')
    parser.add_argument('--output-json', help='write JSON report to this path')
    parser.add_argument('--output-md', help='write Markdown report to this path')
    args = parser.parse_args()
    try:
        raw = json.loads(Path(args.bundle).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'无法读取档案包: {exc}', file=sys.stderr)
        return 2
    try:
        report = evaluate_bundle(raw)
    except ValueError as exc:
        print(f'档案包不合法: {exc}', file=sys.stderr)
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
