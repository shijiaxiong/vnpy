"""
援军战法 第一+第二层 筛选快照
日期范围：2026-01-01 ~ 2026-05-22
只跑板块动量 + 止跌形态，统计每天通过两层筛选的个股
"""
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
sys.path.insert(0, PROJECT_ROOT)
os.environ["PYTHONPATH"] = PROJECT_ROOT

from vnpy.alpha.yuanjun.config import BottomPatternConfig, EntryConfig
from vnpy.alpha.yuanjun.bottom_pattern import BottomPatternRecognizer
from vnpy.alpha.yuanjun.sector_filter import SectorTrendFilter

DB_PATH = os.path.expanduser("~/.vntrader/database.db")
SECTOR_PATH = os.path.join(PROJECT_ROOT, ".cache/sector_stocks.json")
DATE_START = "2026-01-01"
DATE_END = "2026-05-22"


def load_sector_pool() -> Dict[str, dict]:
    with open(SECTOR_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_daily_from_db(sector_pool: Dict[str, dict]) -> Dict[str, pd.DataFrame]:
    """从5分钟K线聚合日线"""
    t0 = time.time()
    symbols = list(sector_pool.keys())
    quoted = ", ".join(f"'{s}'" for s in symbols)

    conn = sqlite3.connect(DB_PATH)
    query = f"""
        WITH daily AS (
            SELECT
                symbol,
                DATE(datetime) AS trade_date,
                high_price    AS high,
                low_price     AS low,
                volume,
                FIRST_VALUE(close_price) OVER (
                    PARTITION BY symbol, DATE(datetime)
                    ORDER BY datetime
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) AS open_price,
                LAST_VALUE(close_price) OVER (
                    PARTITION BY symbol, DATE(datetime)
                    ORDER BY datetime
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) AS close_price
            FROM dbbardata
            WHERE interval = '5m'
                AND datetime >= '{DATE_START} 09:00:00'
                AND datetime <= '{DATE_END} 15:10:00'
                AND symbol IN ({quoted})
        )
        SELECT
            symbol,
            trade_date,
            open_price  AS open,
            MAX(high)   AS high,
            MIN(low)    AS low,
            close_price AS close,
            SUM(volume) AS volume
        FROM daily
        GROUP BY symbol, trade_date
        ORDER BY symbol, trade_date
    """
    print(f"  执行SQL聚合（{len(symbols)}只股票）...")
    df = pd.read_sql(query, conn)
    conn.close()
    print(f"  SQL完成: {len(df):,}行, 耗时{time.time()-t0:.1f}s")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].str.replace(".", "", regex=False)

    stock_data: Dict[str, pd.DataFrame] = {}
    for sym, grp in df.groupby("symbol"):
        s = grp.set_index("trade_date").sort_index()
        s = s[["open", "high", "low", "close", "volume"]]
        if len(s) >= 20:
            stock_data[sym] = s

    all_dates = sorted(set().union(*[set(d.index) for d in stock_data.values()]))
    print(f"  有效股票: {len(stock_data)}只, 交易日: {all_dates[0].date()}~{all_dates[-1].date()}, 共{len(all_dates)}天")
    return stock_data


def calc_sector_momentum(
    stock_data: Dict[str, pd.DataFrame],
    sector_pool: Dict[str, dict],
    date: pd.Timestamp,
) -> Dict[str, float]:
    """计算当日各板块平均涨跌幅"""
    sector_rets: Dict[str, List[float]] = {}
    for sym, df in stock_data.items():
        if date not in df.index:
            continue
        idx = df.index.get_loc(date)
        if idx == 0:
            continue
        prev_close = float(df["close"].iloc[idx - 1])
        curr_close = float(df["close"].iloc[idx])
        if prev_close <= 0:
            continue
        ret = (curr_close - prev_close) / prev_close
        sector = sector_pool.get(sym, {}).get("industry", "其他")
        sector_rets.setdefault(sector, []).append(ret)

    return {
        sector: np.mean(rets)
        for sector, rets in sector_rets.items()
        if rets
    }


def is_top_sector(momentum: Dict[str, float], sector: str) -> bool:
    """判断板块是否在当日动量前50%"""
    if not momentum:
        return True
    sorted_sectors = sorted(momentum.items(), key=lambda x: x[1], reverse=True)
    top_half = [s for s, _ in sorted_sectors[: len(sorted_sectors) // 2 + 1]]
    return sector in top_half


def run() -> None:
    print("=" * 70)
    print(f"第一+第二层筛选快照: {DATE_START} ~ {DATE_END}")
    print("=" * 70)

    sector_pool = load_sector_pool()
    print(f"\n板块池: {len(sector_pool)}只 (券商55 + 半导体27)")

    stock_data = load_daily_from_db(sector_pool)

    # 交易日列表（限制范围）
    trade_dates = sorted(set().union(*[set(d.index) for d in stock_data.values()]))
    trade_dates = [d for d in trade_dates if DATE_START <= str(d.date()) <= DATE_END]
    print(f"  目标区间交易日: {len(trade_dates)}天")

    # 初始化止跌形态识别器（每只股票独立实例）
    bp_config = BottomPatternConfig(
        down_amplitude_min=0.08,
        price_spike_threshold=0.06,
        near_bottom_max_pct=0.05,
        above_ma250=True,
        cooldown_days=15,
        cooldown_interrupt_threshold=0.05,
        enable_macd_convergence=True,
        macd_convergence_days=2,
        enable_rel_strength_filter=True,
        rel_strength_top_pct=0.5,
    )
    recognizers: Dict[str, BottomPatternRecognizer] = {
        sym: BottomPatternRecognizer(bp_config) for sym in stock_data
    }

    # 统计
    results: List[dict] = []
    csv_rows: List[dict] = []
    stats = {
        "days": 0,
        "candidates_scanned": 0,
        "not_top_sector": 0,
        "not_ma5_up": 0,
        "no_data": 0,
        "bottom_pass": 0,
        "bottom_fail": 0,
        "bottom_fail_macd": 0,
        "bottom_fail_rel": 0,
        "bottom_cooldown": 0,
    }

    t0 = time.time()
    # 初始化板块趋势过滤器（MA5不创新低）
    trend_filter = SectorTrendFilter(window=5, lookback_days=5, tolerance=0.002)

    for di, date in enumerate(trade_dates):
        sector_momentum = calc_sector_momentum(stock_data, sector_pool, date)

        # --- 第一层增强：喂入板块等权均价，判断MA5趋势 ---
        sector_daily: Dict[str, List[float]] = {}
        for sym, df in stock_data.items():
            ind = sector_pool.get(sym, {}).get("industry", "")
            if not ind: continue
            if date in df.index:
                idx = df.index.get_loc(date)
                sector_daily.setdefault(ind, []).append(float(df["close"].iloc[idx]))
        for ind, closes in sector_daily.items():
            trend_filter.feed(ind, np.mean(closes))

        day_pass_stocks: List[dict] = []

        for sym, df in stock_data.items():
            stats["candidates_scanned"] += 1

            if date not in df.index:
                stats["no_data"] += 1
                continue

            # --- 第一层：板块动量 ---
            sector = sector_pool.get(sym, {}).get("industry", "其他")
            sector_mom = sector_momentum.get(sector, 0.0)
            sector_rank = "N/A"
            if sector_momentum:
                ranked = sorted(sector_momentum.items(), key=lambda x: x[1], reverse=True)
                ranks = {s: i + 1 for i, (s, _) in enumerate(ranked)}
                sector_rank = f"{ranks.get(sector, 0)}/{len(ranked)}"

            if not is_top_sector(sector_momentum, sector):
                stats["not_top_sector"] += 1
                continue

            # 第一层增强：板块MA5趋势过滤
            if not trend_filter.is_trend_stable(sector):
                stats["not_ma5_up"] += 1
                continue

            # --- 第二层：止跌形态 ---
            idx = df.index.get_loc(date)
            hist = df.iloc[: idx + 1]

            if len(hist) >= 10:
                stop_loss_price = float(hist["low"].iloc[-10:].min())
            else:
                stop_loss_price = float(hist["low"].min())

            recognizer = recognizers[sym]

            # 计算子板块内5日涨幅排名
            rank_pct = 0.0
            industry = sector_pool.get(sym, {}).get("industry", "")
            peers = {
                k for k, v in sector_pool.items()
                if v.get("industry") == industry and k in stock_data and date in stock_data[k].index
            }
            if len(peers) >= 2:
                peer_rets = {}
                for p in peers:
                    df_p = stock_data[p]
                    p_idx = df_p.index.get_loc(date)
                    if p_idx >= 5:
                        ret = float(df_p["close"].iloc[p_idx]) / float(df_p["close"].iloc[p_idx - 5]) - 1
                    else:
                        ret = 0.0
                    peer_rets[p] = ret
                ranked_peers = sorted(peer_rets.items(), key=lambda x: x[1], reverse=True)
                for i, (p, _) in enumerate(ranked_peers):
                    if p == sym:
                        rank_pct = (i + 1) / len(ranked_peers)
                        break

            passed, info = recognizer.is_bottom_pattern(hist, stop_loss_price, rank_pct)

            if info.get("cooldown"):
                stats["bottom_cooldown"] += 1
                continue

            if passed:
                stats["bottom_pass"] += 1
                row = {
                    "date": str(date.date()),
                    "symbol": sym,
                    "name": sector_pool[sym]["name"],
                    "industry": sector,
                    "sector_avg_chg": round(sector_mom, 4),
                    "sector_rank": sector_rank,
                    "down_amplitude": info.get("down_amplitude", 0),
                    "spike_max_abs_pct": info.get("spike_max_abs_pct", 0),
                    "stop_loss_price": info.get("stop_loss_price", 0),
                    "current_price": float(hist["close"].iloc[-1]),
                    "distance_from_stop": info.get("distance_from_stop", 0),
                    "cooldown_interrupted": info.get("cooldown_interrupted", False),
                    "macd_bar": info.get("macd_bar", 0),
                    "macd_dif": info.get("macd_dif", 0),
                    "macd_dea": info.get("macd_dea", 0),
                    "macd_bar_converging": info.get("macd_bar_converging", False),
                    "rel_strength_rank_pct": info.get("rel_strength_rank_pct", 0),
                }
                csv_rows.append(row)
                day_pass_stocks.append(row)
            else:
                stats["bottom_fail"] += 1
                reason = info.get("reason", "")
                if "MACD" in reason:
                    stats["bottom_fail_macd"] += 1
                if "子板块" in reason:
                    stats["bottom_fail_rel"] += 1

        if day_pass_stocks:
            stats["days"] += 1
            results.append({
                "date": str(date.date()),
                "count": len(day_pass_stocks),
                "stocks": [
                    f"{s['symbol']}({s['name']}) "
                    f"回调{s['down_amplitude']:.1%} "
                    f"距撤军线{s['distance_from_stop']:.1%}"
                    for s in day_pass_stocks
                ],
            })

        if (di + 1) % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (di + 1) * (len(trade_dates) - di - 1)
            print(f"  [{di+1}/{len(trade_dates)}] "
                  f"{date.date()} 耗时{elapsed:.0f}s ETA{eta:.0f}s "
                  f"通过:{stats['bottom_pass']} "
                  f"板块过滤:{stats['not_top_sector']}")

    print(f"\n总耗时: {time.time()-t0:.1f}s")

    # --- 输出结果 ---
    print("\n" + "=" * 70)
    print("统计汇总")
    print("=" * 70)
    total = max(stats["candidates_scanned"], 1)
    print(f"  候选扫描:       {stats['candidates_scanned']:>8,}")
    print(f"  数据缺失:       {stats['no_data']:>8,} ({stats['no_data']/total:.1%})")
    print(f"  板块动量过滤:   {stats['not_top_sector']:>8,} ({stats['not_top_sector']/total:.1%})")
    print(f"  板块MA5过滤:   {stats['not_ma5_up']:>8,} ({stats['not_ma5_up']/total:.1%})")
    print(f"  止跌形态通过:   {stats['bottom_pass']:>8,} ({stats['bottom_pass']/total:.1%})")
    print(f"    其中MACD过滤:  {stats['bottom_fail_macd']:>8,} ({stats['bottom_fail_macd']/total:.1%})")
    print(f"    其中相对强度:  {stats['bottom_fail_rel']:>8,} ({stats['bottom_fail_rel']/total:.1%})")
    print(f"  止跌形态未通过: {stats['bottom_fail']:>8,} ({stats['bottom_fail']/total:.1%})")
    print(f"  冷却中:         {stats['bottom_cooldown']:>8,} ({stats['bottom_cooldown']/total:.1%})")
    print(f"  有信号的交易日: {stats['days']}天")

    # --- 逐日明细 ---
    print("\n" + "=" * 70)
    print(f"逐日通过个股明细（共{len(results)}个交易日有信号）")
    print("=" * 70)
    for r in results:
        print(f"\n  {r['date']} ({r['count']}只):")
        for s in r["stocks"]:
            print(f"    - {s}")

    # --- 个股频次排名 ---
    print("\n" + "=" * 70)
    print("个股通过频次排名")
    print("=" * 70)
    freq: Dict[str, int] = defaultdict(int)
    for r in results:
        for s in r["stocks"]:
            code = s.split("(")[0]
            freq[code] += 1

    for code, cnt in sorted(freq.items(), key=lambda x: -x[1]):
        name = sector_pool[code]["name"]
        industry = sector_pool[code]["industry"]
        print(f"  {code} {name:8s} [{industry}]  {cnt}次")

    # --- 保存CSV ---
    out_csv = os.path.join(PROJECT_ROOT, "scripts", "output", "filter_l12_results.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    pd.DataFrame(csv_rows).to_csv(out_csv, index=False, float_format="%.4f")
    print(f"\n结果已保存: {out_csv} ({len(csv_rows)}行)")


if __name__ == "__main__":
    run()
