#!/usr/bin/env python
"""
龙头援军 模块1 近一月全量筛选
从 database_2026.db 中加载 5 分钟 K 线，聚合成日线，逐日运行 BrokenBoardSelector。
"""

import sys
import os
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vnpy.alpha.yuanjun.selector import BrokenBoardSelector, BrokenBoardConfig

# ---------------------------------------------------------------------------
# 1. 配置
# ---------------------------------------------------------------------------
DB_PATH = os.path.expanduser("~/.vntrader/database_2026.db")
# 数据截止 2026-06-08，取近一月：2026-05-09 ~ 2026-06-08
START_DATE = "2026-05-08"   # 多取 1 天做 prev_close
END_DATE = "2026-06-08"

# ---------------------------------------------------------------------------
# 2. 从数据库聚日线
# ---------------------------------------------------------------------------
def load_daily_bars(db_path: str, start: str, end: str):
    """返回 {symbol: DataFrame(columns=[date,open,high,low,close,volume,amount])}"""
    print(f"[1/3] 从 {start} 到 {end} 加载 5m K 线并聚合成日线 ...", flush=True)

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    # 拉取所有股票的 5m 数据（按 symbol 分批太慢，直接全量拉）
    query = """
        SELECT symbol, exchange, datetime, volume, turnover, open_price, high_price, low_price, close_price
        FROM dbbardata
        WHERE datetime >= ? AND datetime <= ?
        ORDER BY symbol, datetime
    """
    rows = db.execute(query, (f"{start} 00:00:00", f"{end} 23:59:59")).fetchall()
    db.close()

    print(f"    原始 5m bar 共 {len(rows):,} 条", flush=True)

    # 聚合成日线
    daily = defaultdict(list)
    for symbol, exchange, dt_str, vol, amt, op, hi, lo, cl in rows:
        date_str = dt_str[:10]
        daily[(symbol, date_str)].append((float(op), float(hi), float(lo), float(cl), float(vol), float(amt)))

    result: dict[str, pd.DataFrame] = {}
    for (symbol, date_str), bars in daily.items():
        arr = np.array(bars)
        result.setdefault(symbol, []).append({
            "date": date_str,
            "open": arr[0, 0],
            "high": arr[:, 1].max(),
            "low": arr[:, 2].min(),
            "close": arr[-1, 3],
            "volume": arr[:, 4].sum(),
            "amount": arr[:, 5].sum(),
        })

    dfs = {}
    for sym, records in result.items():
        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
        df["pct_change"] = df["close"].pct_change()
        # 量比：当日量 / 5日均量
        df["volume_ratio"] = df["volume"] / df["volume"].rolling(5).mean().shift(1)
        df["volume_ratio"] = df["volume_ratio"].fillna(1.0)
        # 换手率：无流通股本数据无法精确计算，用占位值10
        # 不影响过滤（条件5已关闭），评分对所有候选统一，实际分化来自其他 85% 权重
        df["turnover"] = 10.0
        dfs[sym] = df

    print(f"    聚合完成: {len(dfs)} 只股票, 每日约 {len(next(iter(dfs.values()))) if dfs else 0} 行", flush=True)
    return dfs


# ---------------------------------------------------------------------------
# 3. 逐日运行筛选
# ---------------------------------------------------------------------------
def run_scan(stock_daily: dict[str, pd.DataFrame], name_map: dict[str, str] | None = None):
    """逐日运行 BrokenBoardSelector，收集结果"""
    if name_map is None:
        name_map = {}

    # 汇总所有交易日
    all_dates = set()
    for df in stock_daily.values():
        all_dates.update(df["date"].tolist())
    all_dates = sorted(all_dates)

    # 去掉第一天（做 prev_close 用）
    trade_dates = all_dates[1:]
    print(f"\n[2/3] 交易日共 {len(trade_dates)} 天，逐日扫描 ...", flush=True)

    selector = BrokenBoardSelector(BrokenBoardConfig(), name_map=name_map)
    daily_results: dict[str, list[dict]] = {}
    total_hits = 0

    for idx, date in enumerate(trade_dates):
        # 构建当日 stock_data：取到今天为止的所有历史日线
        stock_data: dict[str, pd.DataFrame] = {}
        for sym, df in stock_daily.items():
            sub = df[df["date"] <= date].copy()
            # 需要有足够的历史数据（至少 5 天 + 用于连板回查）
            if len(sub) >= 6:
                # 最后一行是当天
                stock_data[sym] = sub

        if not stock_data:
            continue

        candidates, details = selector.select(stock_data)

        if candidates:
            day_list = []
            for code in candidates:
                d = details.get(code, {})
                day_list.append({
                    "symbol": code,
                    "close_return": d.get("close_return", 0),
                    "consecutive": d.get("consecutive_before", 0),
                    "limits_window": d.get("limits_in_window", 0),
                    "turnover": d.get("turnover", 0),
                    "volume_ratio": d.get("volume_ratio", 0),
                    "board_score": d.get("board_score", 0),
                    "total_score": d.get("total_score", 0),
                })
            daily_results[date] = day_list
            total_hits += len(candidates)
            print(f"    {date}: 候选 {len(candidates)} 只", flush=True)

    print(f"\n[3/3] 全月共命中 {total_hits} 次（含同日多只）", flush=True)
    return daily_results


# ---------------------------------------------------------------------------
# 4. 汇总输出
# ---------------------------------------------------------------------------
def print_summary(daily_results: dict[str, list[dict]]):
    # 统计每只股票被选中的次数
    hit_count: dict[str, int] = defaultdict(int)
    for date, stocks in daily_results.items():
        for s in stocks:
            hit_count[s["symbol"]] += 1

    print("\n" + "=" * 80)
    print("汇总：模块1 筛选结果（BrokenBoardSelector）")
    print(f"统计区间: {START_DATE} ~ {END_DATE}")
    print("=" * 80)

    if not daily_results:
        print("  无命中。")
        return

    # 按日期分组输出
    dates = sorted(daily_results.keys())
    print(f"\n交易日: {len(dates)} 天, 命中: {len(dates)} 天有候选")
    print(f"\n每日详情:\n")

    for date in dates:
        stocks = daily_results[date]
        print(f"  [{date}] ({len(stocks)} 只)")
        for s in stocks:
            print(f"    {s['symbol']:8s}  |  涨幅 {s['close_return']:>7.2%}  |"
                  f"  连板 {s['consecutive']}  |  窗口板 {s['limits_window']}  |"
                  f"  量比 {s['volume_ratio']:.2f}  |"
                  f"  连板分 {s['board_score']:.2f}  |"
                  f"  总分 {s['total_score']:.4f}")
        print()

    # 命中次数排行
    print("-" * 80)
    print("频次排行 (被选中 ≥2 次):")
    ranked = sorted(hit_count.items(), key=lambda x: (-x[1], x[0]))
    for sym, cnt in ranked:
        if cnt >= 2:
            print(f"  {sym}: {cnt} 次")

    print(f"\n总计: {sum(len(v) for v in daily_results.values())} 次命中, {len(hit_count)} 只不同股票")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    dfs = load_daily_bars(DB_PATH, START_DATE, END_DATE)
    results = run_scan(dfs)
    print_summary(results)
