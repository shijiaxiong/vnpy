#!/usr/bin/env python3
"""
每日涨停题材强度分析
====================
数据来源: cache_zt/ 下的 CSV 缓存文件（东方财富 via akshare）
分组维度: 所属行业

输出排序规则:
  1. 连板梯队档位数最多
  2. 最高连板高度最大
  3. 涨停总家数最多

可选: 展示东方财富行业板块涨跌幅排行（单独数据源）

用法:
  python scripts/analyze_theme_strength.py                    # 默认今天
  python scripts/analyze_theme_strength.py --date 20260526    # 指定日期
  python scripts/analyze_theme_strength.py --date 20260526 --top 10
  python scripts/analyze_theme_strength.py --date 20260526 --board
"""

import pandas as pd
import json
import os
import sys
from datetime import datetime
import argparse

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache_zt')
BOARD_CACHE = os.path.join(CACHE_DIR, 'board_cache')
BOARD_INDEX_PATH = os.path.join(BOARD_CACHE, 'board_index.json')


def load_board_data():
    """加载缓存的行业板块行情数据"""
    if os.path.exists(BOARD_INDEX_PATH):
        with open(BOARD_INDEX_PATH) as f:
            data = json.load(f)
        return pd.DataFrame(data)
    return None


def load_zt_data(date_str):
    """加载当日涨停数据"""
    path = os.path.join(CACHE_DIR, f'{date_str}.csv')
    if not os.path.exists(path):
        print(f"  ❌ 未找到 {date_str} 数据: {path}")
        return None
    df = pd.read_csv(path)
    df['连板数'] = pd.to_numeric(df['连板数'], errors='coerce').fillna(0).astype(int)
    return df


def analyze_theme_strength(df):
    """按 所属行业 分析题材强度"""

    def calc_metrics(group):
        boards = group['连板数'].values
        board_counts = {}
        for b in boards:
            board_counts[b] = board_counts.get(b, 0) + 1

        tier_count = len(board_counts)
        max_board = max(boards)
        total = len(group)

        tier_detail = sorted(board_counts.items(), key=lambda x: -x[0])
        tier_str = ' | '.join([f'{b}板×{c}' for b, c in tier_detail])

        stocks = group.apply(
            lambda r: f"{r['名称']}({r['代码']})", axis=1
        ).tolist()

        return pd.Series({
            '涨停总家数': total,
            '连板梯队': tier_str,
            '梯队档位数': tier_count,
            '最高连板高度': max_board,
            '成分股': '、'.join(stocks),
        })

    result = df.groupby('所属行业').apply(calc_metrics).reset_index()
    result = result.rename(columns={'所属行业': '题材(行业)'})

    # 排序
    result = result.sort_values(
        by=['梯队档位数', '最高连板高度', '涨停总家数'],
        ascending=[False, False, False]
    ).reset_index(drop=True)

    result.index = result.index + 1
    result.index.name = '排名'

    return result


def print_summary(result, date_str, total_stocks):
    """打印涨停分析结果"""
    width = 110
    print(f"\n{'=' * width}")
    print(f"  {date_str} 涨停题材强度分析")
    print(f"  涨停总数: {total_stocks} 只 | 涉及题材数: {len(result)} 个")
    print(f"{'=' * width}")

    line = '─' * width
    header = f"{'排名':>4} | {'题材(行业)':14s} | {'涨停':>4} | {'梯队':>4} | {'最高板':>6} | 连板梯队详情"
    print(line)
    print(header)
    print(line)

    for rank, row in result.iterrows():
        print(f"{rank:>4} | {row['题材(行业)']:14s} | {row['涨停总家数']:>4} | "
              f"{row['梯队档位数']:>4} | {row['最高连板高度']:>6}板 | {row['连板梯队']}")

    print(line)

    strong = result[result['梯队档位数'] >= 2]
    if len(strong) > 0:
        print(f"\n  ⭐ 强势题材 (梯队档位数≥2):")
        for _, row in strong.iterrows():
            print(f"    · {row['题材(行业)']}: {row['连板梯队']} | "
                  f"最高{row['最高连板高度']}板 | 共{row['涨停总家数']}只")
            print(f"      成分股: {row['成分股']}")

    print(f"\n{'=' * width}\n")


def print_board_summary(board_df, top_n=10):
    """打印行业板块涨跌幅排行"""
    if board_df is None or board_df.empty:
        return

    print(f"\n{'=' * 80}")
    print(f"  行业板块涨跌幅 Top {top_n}")
    print(f"{'=' * 80}")

    # 按涨跌幅降序
    df = board_df.sort_values('涨跌幅', ascending=False).head(top_n)

    print(f"{'─' * 80}")
    print(f"{'排名':>4} | {'板块代码':10s} | {'板块名称':14s} | {'涨跌幅':>8} | {'成交额':>12} | {'换手率':>6}")
    print(f"{'─' * 80}")

    for i, (_, row) in enumerate(df.iterrows(), 1):
        pct = row['涨跌幅']
        pct_str = f"{pct:+.2f}%" if pct != 0 else f"{pct:.2f}%"
        amt = row.get('成交额', 0)
        amt_str = f"{amt/1e8:.1f}亿" if amt >= 1e8 else f"{amt/1e4:.0f}万"
        turnover = row.get('换手率', 0)
        print(f"{i:>4} | {row['code']:10s} | {row['name']:14s} | {pct_str:>8} | {amt_str:>12} | {turnover:>5.2f}%")

    print(f"{'─' * 80}\n")

    # 跌幅榜
    df_tail = board_df.sort_values('涨跌幅', ascending=True).head(5)
    print(f"  行业板块跌幅 Top 5:")
    for _, row in df_tail.iterrows():
        pct = row['涨跌幅']
        pct_str = f"{pct:+.2f}%" if pct != 0 else f"{pct:.2f}%"
        print(f"    {row['name']:12s} {row['code']:8s}  {pct_str}")


def main():
    parser = argparse.ArgumentParser(description='每日涨停题材强度分析')
    parser.add_argument('--date', default=None, help='日期 YYYYMMDD，默认今天')
    parser.add_argument('--top', type=int, default=0, help='只显示前 N 个题材，0=全部')
    parser.add_argument('--board', action='store_true', help='同时展示行业板块涨跌幅排行')
    args = parser.parse_args()

    if args.date is None:
        args.date = datetime.now().strftime('%Y%m%d')

    # 涨停数据
    df = load_zt_data(args.date)
    if df is None:
        sys.exit(1)
    print(f"\n  加载 {args.date} 涨停数据: {len(df)} 只股票")

    # 分析
    result = analyze_theme_strength(df)

    if args.top > 0:
        result = result.head(args.top)

    print_summary(result, args.date, len(df))

    # 板块行情（可选）
    if args.board:
        board_df = load_board_data()
        print_board_summary(board_df)

    # 保存 CSV
    out_dir = os.path.join(SCRIPT_DIR, 'output')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'theme_strength_{args.date}.csv')
    result.to_csv(out_path, encoding='utf-8-sig', index=True)
    print(f"  CSV 已保存: {out_path}\n")


if __name__ == '__main__':
    main()
