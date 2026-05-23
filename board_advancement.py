#!/usr/bin/env python3
"""
A股连板晋级率 · 砸盘系数
=========================
数据来源: 东方财富 (via akshare)

计算逻辑:
  - 2板晋级率 = 当日3板数量 / 前一日2板数量
  - 3板晋级率 = 当日4板数量 / 前一日3板数量
  - ...依次类推至当日最高板
  - 累加晋级率 = 所有级晋级率之和 (百分比)
  - 砸盘系数   = ceil(累加晋级率 / 40, 1位小数)

用法:
  python board_advancement.py                          # 默认近60个交易日
  python board_advancement.py --days 120               # 近120个交易日
  python board_advancement.py --start 20250101         # 指定起始日
  python board_advancement.py --start 20250101 --end 20260522
  python board_advancement.py --no-plot                # 仅输出表格

依赖: pip install akshare pandas numpy matplotlib
"""

import akshare as ak
import math
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # 无头模式，避免 macOS 后端问题
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import time
import os
import sys
import argparse
import warnings

warnings.filterwarnings('ignore')

# ===== 中文显示设置 =====
plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Arial Unicode MS', 'SimHei', 'STHeiti']
plt.rcParams['axes.unicode_minus'] = False

# ===== 缓存目录 =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache_zt')
os.makedirs(CACHE_DIR, exist_ok=True)


# ============================================================================
#  数据获取
# ============================================================================

def get_trade_dates(start_date, end_date):
    """
    获取交易日列表
    start_date / end_date: str 'YYYYMMDD'
    返回: list of datetime
    """
    try:
        df = ak.tool_trade_date_hist_sina()
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        start_dt = pd.Timestamp(datetime.strptime(start_date, '%Y%m%d'))
        end_dt = pd.Timestamp(datetime.strptime(end_date, '%Y%m%d'))
        mask = (df['trade_date'] >= start_dt) & (df['trade_date'] <= end_dt)
        dates = sorted(df[mask]['trade_date'].tolist())
        return dates
    except Exception as e:
        print(f"  [WARNING] 交易日历获取失败: {e}，使用工作日近似")
        # 回退：生成所有工作日
        dates = []
        d = datetime.strptime(start_date, '%Y%m%d')
        end = datetime.strptime(end_date, '%Y%m%d')
        while d <= end:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(days=1)
        return dates


def fetch_zt_data(date_str):
    """
    获取单日涨停板数据，带 CSV 缓存
    date_str: 'YYYYMMDD'
    返回: DataFrame | None
    """
    cache_path = os.path.join(CACHE_DIR, f'{date_str}.csv')

    if os.path.exists(cache_path):
        try:
            return pd.read_csv(cache_path)
        except Exception:
            pass

    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df is not None and len(df) > 0 and '连板数' in df.columns:
            df.to_csv(cache_path, index=False, encoding='utf-8-sig')
            return df
        return None
    except Exception:
        return None


# ============================================================================
#  指标计算
# ============================================================================

def compute_indicator(start_date='20250101', end_date=None, max_days=None):
    """
    计算连板晋级率砸盘系数

    Parameters
    ----------
    start_date : str
        起始日期 'YYYYMMDD'
    end_date : str | None
        结束日期，默认今天
    max_days : int | None
        最多取多少个交易日，None = 全部

    Returns
    -------
    DataFrame 或 None
    """
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')

    # ---- 1. 交易日历 ----
    print(f"\n{'='*60}")
    print(f"  [1/4] 获取交易日历  {start_date} → {end_date}")
    print(f"{'='*60}")
    trade_dates = get_trade_dates(start_date, end_date)
    if max_days is not None:
        trade_dates = trade_dates[-max_days:]
    print(f"  交易日: {len(trade_dates)} 天")

    if len(trade_dates) < 3:
        print("  ❌ 交易日不足 (< 3)，退出")
        return None

    # ---- 2. 获取涨停数据 ----
    print(f"\n{'='*60}")
    print(f"  [2/4] 获取涨停板连板数据 (缓存目录: {CACHE_DIR})")
    print(f"{'='*60}")

    daily_boards = {}   # { 'YYYYMMDD': {board_num: count} }
    fail_count = 0

    for i, d in enumerate(trade_dates):
        ds = d.strftime('%Y%m%d')
        df = fetch_zt_data(ds)

        if df is not None and '连板数' in df.columns:
            series = df['连板数'].dropna()
            series = series[series >= 2]               # 仅统计 ≥2板
            if len(series) > 0:
                counts = series.value_counts().to_dict()
                daily_boards[ds] = {int(k): int(v) for k, v in counts.items()}
        else:
            fail_count += 1

        # 每 10 天打印一次进度
        if (i + 1) % 10 == 0 or i == len(trade_dates) - 1:
            pct = (i + 1) / len(trade_dates) * 100
            print(f"    进度: {i+1}/{len(trade_dates)} ({pct:.0f}%)  "
                  f"有效: {len(daily_boards)}  失败: {fail_count}", end='\r')
        time.sleep(0.25)

    print(f"\n  有效交易日: {len(daily_boards)} / {len(trade_dates)}")

    if len(daily_boards) < 2:
        print("  ❌ 有效数据不足")
        return None

    # ---- 3. 计算晋级率 ----
    print(f"\n{'='*60}")
    print(f"  [3/4] 计算晋级率与砸盘系数")
    print(f"{'='*60}")

    sorted_dates = sorted(daily_boards.keys())
    results = []

    for i in range(1, len(sorted_dates)):
        prev_ds = sorted_dates[i - 1]
        curr_ds = sorted_dates[i]

        prev = daily_boards[prev_ds]
        curr = daily_boards[curr_ds]

        date_dt = pd.to_datetime(curr_ds)
        row = {'日期': date_dt}

        # 当日 ≥2板 总数
        row['涨停≥2板总数'] = sum(curr.values())

        # 当天最高板（纯当天数据，不合并前一天）
        curr_max = max(curr.keys())
        row['赚钱效应'] = curr_max

        rates = []

        # N板晋级率 = 今日 N+1板数 / 昨日 N板数
        # 遍历 N=2 到 当天最高板-1（需要今日 N+1 板存在）
        for board in range(2, curr_max):
            prev_count = prev.get(board, 0)          # 昨日 N板 数量
            next_count = curr.get(board + 1, 0)       # 今日 N+1板 数量

            col_n = f'{board}板_昨'
            col_n1 = f'{board+1}板_今'
            row[col_n] = prev_count
            row[col_n1] = next_count

            if prev_count > 0:
                rate = round(next_count / prev_count * 100, 1)
            else:
                rate = np.nan

            col_rate = f'{board}→{board+1}晋级%'
            row[col_rate] = rate

            if not np.isnan(rate):
                rates.append(rate)

        # 核心指标：累加晋级率 / 40，只入不舍保留 1 位小数
        row['累加晋级率'] = round(sum(rates), 1) if rates else 0.0
        raw = row['累加晋级率'] / 4 * 10 / 100
        row['砸盘系数'] = math.ceil(raw * 10) / 10

        results.append(row)

    df_result = pd.DataFrame(results)
    print(f"  计算完成: {len(df_result)} 行 × {len(df_result.columns)} 列")

    # ---- 4. 输出摘要 ----
    print(f"\n{'='*60}")
    print(f"  [4/4] 输出结果")
    print(f"{'='*60}")

    return df_result


# ============================================================================
#  表格显示
# ============================================================================

def print_table(df, tail=20):
    """打印最近 N 天的表格"""
    if df is None or len(df) == 0:
        return

    display = df.tail(tail).copy()

    # 选择要显示的列
    cols = ['日期']
    for c in df.columns:
        if c.endswith('晋级%') or c in [
            '涨停≥2板总数', '赚钱效应', '累加晋级率', '砸盘系数'
        ]:
            cols.append(c)

    available = [c for c in cols if c in display.columns]
    sub = display[available].copy()
    sub['日期'] = sub['日期'].dt.strftime('%m-%d')

    print(f"\n{'─' * 100}")
    print(f"  A股连板晋级率 · 砸盘系数 (最近 {len(sub)} 天)")
    print(f"{'─' * 100}")
    print(sub.to_string(index=False))
    print(f"{'─' * 100}")

    # 统计摘要
    print(f"\n  📊 统计摘要 (共 {len(df)} 个交易日):")
    print(f"     砸盘系数  ─  均值: {df['砸盘系数'].mean():.1f}  "
          f"中位数: {df['砸盘系数'].median():.1f}  "
          f"最高: {df['砸盘系数'].max():.1f}  "
          f"最低: {df['砸盘系数'].min():.1f}")
    print(f"     累加晋级率 ─  均值: {df['累加晋级率'].mean():.1f}%  "
          f"最高: {df['累加晋级率'].max():.1f}%")
    avg_max = df['赚钱效应'].mean()
    print(f"     最高连板数 ─  平均: {avg_max:.1f}板  "
          f"最高: {df['赚钱效应'].max()}板")

    # 情绪判断
    latest = df['砸盘系数'].iloc[-1]
    mean_val = df['砸盘系数'].mean()
    std_val = df['砸盘系数'].std()

    ratio = latest / mean_val if mean_val > 0 else 1.0
    if ratio > 1.5:
        tag = '🔥🔥 极度亢奋 (连板情绪过热，警惕高潮后分歧)'
    elif ratio > 1.2:
        tag = '🔥 偏亢奋 (晋级率偏高，赚钱效应好)'
    elif ratio > 0.8:
        tag = '😐 中性 (正常晋级水平)'
    elif ratio > 0.5:
        tag = '❄️ 偏冷 (晋级率偏低，连板氛围弱)'
    else:
        tag = '🧊 冰点 (连板情绪冰点，可能孕育反弹)'

    print(f"\n  🎯 最新砸盘系数: {latest:.1f} → {tag}")
    print(f"{'─' * 100}\n")


# ============================================================================
#  图表绘制
# ============================================================================

def plot_dashboard(df, output_path=None):
    """每天独立柱状图，不跨天合并"""
    if df is None or len(df) == 0:
        print("  ⚠️ 无数据，跳过绘图")
        return

    dates = df['日期']
    date_labels = dates.dt.strftime('%m-%d')
    indicator = df['砸盘系数']
    cum_rate = df['累加晋级率']
    n = len(df)

    fig = plt.figure(figsize=(max(14, n * 0.35), 11))
    gs = fig.add_gridspec(3, 1, hspace=0.45, height_ratios=[1.5, 1, 2])

    # ===== 图1: 砸盘系数（每天独立柱状图） =====
    ax1 = fig.add_subplot(gs[0])
    mean_v = indicator.mean()

    # 根据值偏离均值着色
    colors_bar = []
    for v in indicator:
        if v > mean_v * 1.3:
            colors_bar.append('#e74c3c')       # 亢奋-红
        elif v > mean_v * 1.1:
            colors_bar.append('#f39c12')       # 偏暖-橙
        elif v > mean_v * 0.7:
            colors_bar.append('#3498db')       # 中性-蓝
        elif v > mean_v * 0.5:
            colors_bar.append('#95a5a6')       # 偏冷-灰
        else:
            colors_bar.append('#7f8c8d')       # 冰点-深灰

    bars = ax1.bar(range(n), indicator, color=colors_bar, edgecolor='white',
                   linewidth=0.5, width=0.7)
    ax1.axhline(y=mean_v, color='red', linestyle='--', linewidth=1.5,
                alpha=0.7, label=f'均值 {mean_v:.1f}')

    # 柱顶标注值
    for i, (bar, val) in enumerate(zip(bars, indicator)):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(indicator) * 0.02,
                 f'{val:.0f}', ha='center', va='bottom', fontsize=7,
                 fontweight='bold', color=colors_bar[i])

    ax1.set_title('A股连板晋级率 · 砸盘系数（每天独立计算）', fontsize=16, fontweight='bold', pad=10)
    ax1.set_ylabel('砸盘系数 = ceil(累加/40, 1位)', fontsize=10)
    ax1.set_xticks(range(n))
    ax1.set_xticklabels(date_labels, rotation=90, fontsize=7)
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(True, alpha=0.2, linestyle='--', axis='y')

    # ===== 图2: 累加晋级率（每天独立柱状图） =====
    ax2 = fig.add_subplot(gs[1])
    ax2.bar(range(n), cum_rate, color='#ff7f0e', edgecolor='white',
            linewidth=0.5, width=0.7, alpha=0.85)
    ax2.axhline(y=cum_rate.mean(), color='gray', linestyle=':', linewidth=1,
                alpha=0.5, label=f'均值 {cum_rate.mean():.1f}%')
    for i, val in enumerate(cum_rate):
        ax2.text(i, val + max(cum_rate) * 0.02, f'{val:.0f}', ha='center',
                 va='bottom', fontsize=7, fontweight='bold', color='#cc6600')
    ax2.set_title('累加晋级率（每天独立）', fontsize=14, fontweight='bold')
    ax2.set_ylabel('累加晋级率 (%)', fontsize=10)
    ax2.set_xticks(range(n))
    ax2.set_xticklabels(date_labels, rotation=90, fontsize=7)
    ax2.legend(loc='upper left', fontsize=9)
    ax2.grid(True, alpha=0.2, linestyle='--', axis='y')

    # ===== 图3: 每天各级晋级率分组柱状图 =====
    ax3 = fig.add_subplot(gs[2])
    rate_cols = [c for c in df.columns if c.endswith('晋级%')]
    if rate_cols:
        x = np.arange(n)
        n_groups = len(rate_cols)
        width = 0.8 / n_groups
        colors_rate = plt.cm.Set2(np.linspace(0, 1, n_groups))

        for idx, col in enumerate(rate_cols):
            label = col.replace('晋级%', '')
            vals = df[col].fillna(0).values
            offset = (idx - n_groups / 2 + 0.5) * width
            ax3.bar(x + offset, vals, width, label=label,
                    color=colors_rate[idx], edgecolor='white', linewidth=0.3)

        ax3.set_title('每天各级晋级率分组柱状图（互不合并）', fontsize=14, fontweight='bold')
        ax3.set_ylabel('晋级率 (%)', fontsize=10)
        ax3.set_xticks(range(n))
        ax3.set_xticklabels(date_labels, rotation=90, fontsize=7)
        ax3.legend(loc='upper left', fontsize=7, ncol=n_groups)
        ax3.grid(True, alpha=0.2, linestyle='--', axis='y')

    fig.suptitle('A股连板晋级率 · 每天独立计算仪表盘',
                 fontsize=18, fontweight='bold', y=1.01)
    plt.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
        print(f"  📈 图表已保存: {output_path}")

    return fig


def plot_heatmap(df, output_path=None):
    """绘制晋级率热力图"""
    rate_cols = [c for c in df.columns if c.endswith('晋级%')]
    if not rate_cols:
        return

    # 准备热力图数据
    heat_data = df[['日期'] + rate_cols].copy()
    heat_data['日期_label'] = heat_data['日期'].dt.strftime('%m-%d')
    heat_data = heat_data.set_index('日期_label')
    heat_data = heat_data[rate_cols]
    heat_data.columns = [c.replace('晋级%', '') for c in rate_cols]

    fig, ax = plt.subplots(figsize=(16, max(6, len(heat_data) * 0.25)))

    im = ax.imshow(heat_data.T.values, aspect='auto', cmap='RdYlGn',
                   vmin=0, vmax=100, interpolation='nearest')

    # 标注
    for i in range(heat_data.shape[1]):
        for j in range(heat_data.shape[0]):
            val = heat_data.iloc[j, i]
            if not np.isnan(val):
                color = 'white' if val < 30 or val > 70 else 'black'
                ax.text(j, i, f'{val:.0f}', ha='center', va='center',
                        fontsize=7, color=color, fontweight='bold')

    ax.set_xticks(range(len(heat_data.index)))
    ax.set_xticklabels(heat_data.index, rotation=90, fontsize=7)
    ax.set_yticks(range(len(heat_data.columns)))
    ax.set_yticklabels(heat_data.columns, fontsize=9)
    ax.set_title('连板晋级率热力图 (%)', fontsize=16, fontweight='bold', pad=12)

    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label('晋级率 %', fontsize=10)

    plt.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
        print(f"  📈 热力图已保存: {output_path}")

    return fig


# ============================================================================
#  主入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='A股连板晋级率 · 砸盘系数',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python board_advancement.py                        # 默认近60个交易日
  python board_advancement.py --days 120             # 近120个交易日
  python board_advancement.py --start 20250101       # 从 2025-01-01 至今
  python board_advancement.py --start 20250101 --end 20260522
  python board_advancement.py --no-plot              # 仅输出表格
        """
    )
    parser.add_argument('--start', default=None,
                        help='开始日期 YYYYMMDD (默认: 回溯 --days 对应自然日)')
    parser.add_argument('--end', default=None,
                        help='结束日期 YYYYMMDD (默认: 今天)')
    parser.add_argument('--days', type=int, default=60,
                        help='最大交易日数 (默认: 60)')
    parser.add_argument('--output', default=None,
                        help='输出文件前缀 (默认: output/board_advancement)')
    parser.add_argument('--no-plot', action='store_true',
                        help='仅输出表格，不生成图表')
    parser.add_argument('--tail', type=int, default=25,
                        help='表格显示最近 N 行 (默认: 25)')
    args = parser.parse_args()

    # 日期处理
    if args.end is None:
        end_dt = datetime.now()
        args.end = end_dt.strftime('%Y%m%d')
    else:
        end_dt = datetime.strptime(args.end, '%Y%m%d')

    if args.start is None:
        # 默认回溯足够多的自然日以覆盖 args.days 个交易日
        start_dt = end_dt - timedelta(days=args.days * 2 + 30)
        args.start = start_dt.strftime('%Y%m%d')

    # 输出路径
    if args.output is None:
        out_dir = os.path.join(SCRIPT_DIR, 'output')
        os.makedirs(out_dir, exist_ok=True)
        prefix = os.path.join(out_dir, 'board_advancement')
    else:
        out_dir = os.path.dirname(args.output) or '.'
        os.makedirs(out_dir, exist_ok=True)
        prefix = args.output

    print(f"\n{'='*60}")
    print(f"  A股连板晋级率 · 砸盘系数")
    print(f"  数据源: 东方财富 (akshare)")
    print(f"  日期范围: {args.start} → {args.end} (最多 {args.days} 个交易日)")
    print(f"  输出目录: {out_dir}")
    print(f"{'='*60}")

    # 计算
    df = compute_indicator(start_date=args.start, end_date=args.end,
                           max_days=args.days)

    if df is None or len(df) == 0:
        print("\n❌ 计算失败或数据不足，请检查:")
        print("   1. 网络是否正常 (需要访问东方财富API)")
        print("   2. 日期范围内是否有交易日")
        print("   3. pip install akshare --upgrade")
        sys.exit(1)

    # 保存 CSV
    csv_path = f'{prefix}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n  💾 数据已保存: {csv_path}")

    # 打印表格
    print_table(df, tail=args.tail)

    # 绘图
    if not args.no_plot:
        print(f"  正在生成图表...")
        try:
            png_path = f'{prefix}_dashboard.png'
            plot_dashboard(df, output_path=png_path)

            png_heat = f'{prefix}_heatmap.png'
            plot_heatmap(df, output_path=png_heat)
        except Exception as e:
            print(f"  ⚠️ 图表生成失败: {e}")
            print(f"     (非关键错误，CSV 数据已保存)")

    print(f"\n{'='*60}")
    print(f"  完成! 输出文件:")
    print(f"    数据: {csv_path}")
    if not args.no_plot:
        print(f"    图表: {prefix}_dashboard.png")
        print(f"    热力图: {prefix}_heatmap.png")
    print(f"{'='*60}\n")

    return df


if __name__ == '__main__':
    main()
