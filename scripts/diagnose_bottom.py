"""
诊断止跌形态各条件通过率
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

PROJECT_ROOT = "/Users/shijiaxiong/go/src/github.com/vnpy"
sys.path.insert(0, PROJECT_ROOT)

from vnpy.alpha.yuanjun.config import (
    BottomPatternConfig, EntryConfig, RiskConfig, StrategyConfig,
)
from vnpy.alpha.yuanjun.bottom_pattern import BottomPatternRecognizer
from vnpy.alpha.yuanjun.selector import BrokenBoardConfig, BrokenBoardSelector

DB_PATH = os.path.expanduser("~/.vntrader/database_2026.db")
STOCK_CACHE = os.path.join(PROJECT_ROOT, ".cache/stocks_akshare.json")
DATE_LOOKBACK = "2024-01-01"
DATE_END = "2026-06-17"
MAX_SYMBOLS = 200


def get_stock_symbols(max_n=MAX_SYMBOLS):
    with open(STOCK_CACHE, "r") as f:
        all_symbols = json.load(f)
    main_board = []
    for s in all_symbols:
        if s.startswith("sh.60") or s.startswith("sz.00") or s.startswith("sz.000"):
            code = s.split(".")[1]
            if len(code) == 6 and (code.startswith("60") or code.startswith("00")):
                main_board.append(code)
    if max_n > 0 and len(main_board) > max_n:
        np.random.seed(42)
        main_board = sorted(np.random.choice(main_board, max_n, replace=False))
    return main_board


def query_symbol(symbol, conn, date_start, date_end):
    try:
        df = pd.read_sql("""
            WITH daily AS (
                SELECT DATE(datetime) AS trade_date, high_price AS high,
                    low_price AS low, volume, turnover,
                    FIRST_VALUE(close_price) OVER (PARTITION BY DATE(datetime) ORDER BY datetime ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS open_price,
                    LAST_VALUE(close_price) OVER (PARTITION BY DATE(datetime) ORDER BY datetime ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS close_price
                FROM dbbardata WHERE symbol=? AND exchange IN ('SSE','SZSE') AND interval='5m'
                    AND datetime>=?||' 09:00:00' AND datetime<=?||' 15:10:00'
            )
            SELECT trade_date, open_price AS open, MAX(high) AS high,
                MIN(low) AS low, close_price AS close, SUM(volume) AS volume, SUM(turnover) AS turnover
            FROM daily GROUP BY trade_date ORDER BY trade_date
        """, conn, params=(symbol, date_start, date_end))
        if len(df) < 60:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date").sort_index()
        return df[["open","high","low","close","volume","turnover"]]
    except Exception:
        return None


def main():
    print("加载股票列表...")
    symbols = get_stock_symbols(MAX_SYMBOLS)
    print(f"  共 {len(symbols)} 只")

    print("加载数据...")
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    stock_data = {}
    for sym in symbols:
        df = query_symbol(sym, conn, DATE_LOOKBACK, DATE_END)
        if df is not None:
            stock_data[sym] = df
    conn.close()
    print(f"  加载 {len(stock_data)} 只")

    # 筛选器
    board_cfg = BrokenBoardConfig()
    selector = BrokenBoardSelector(board_cfg)
    bottom_cfg = StrategyConfig.leader_style().bottom_config
    recognizers = {sym: BottomPatternRecognizer(bottom_cfg) for sym in stock_data}

    # 诊断统计
    stats = {
        "cond1_down": {"pass": 0, "fail": 0},       # 回调幅度
        "cond2_ma250": {"pass": 0, "fail": 0},      # 站上MA250
        "cond3_macd": {"pass": 0, "fail": 0, "skip": 0},  # MACD (默认关闭)
        "cond4_strength": {"pass": 0, "fail": 0},   # 板块强度
        "cond5_no_new_low": {"pass": 0, "fail": 0}, # 不创新低
    }

    test_start = pd.Timestamp("2026-06-01")
    test_end = pd.Timestamp("2026-06-17")
    all_dates = sorted(set().union(*[set(d.index) for d in stock_data.values()]))
    trade_dates = [d for d in all_dates if test_start <= d <= test_end]

    detail_records = []

    for date in trade_dates:
        candidates = {}
        for sym, df in stock_data.items():
            if date not in df.index:
                continue
            candidates[sym] = df.loc[:date]

        if not candidates:
            continue

        board_codes, _ = selector.select(candidates)

        for code in board_codes:
            df = candidates[code]
            recognizer = recognizers[code]

            # Call the full detection
            is_bottom, _ = recognizer.is_bottom_pattern(df)

            # Check each condition individually
            try:
                cond1, _ = recognizer._check_down_amplitude(df)
            except Exception:
                cond1 = False
            try:
                cond2, _ = recognizer._check_above_ma250(df)
            except Exception:
                cond2 = False
            try:
                cond3, _ = recognizer._check_macd_convergence(df) if bottom_cfg.enable_macd_convergence else (True, {})
                if not bottom_cfg.enable_macd_convergence:
                    cond3 = None  # disabled
            except Exception:
                cond3 = False
            # rel_strength_rank_pct passed as 0.0 (same as backtest default)
            try:
                cond4, _ = recognizer._check_relative_strength(0.0)
            except Exception:
                cond4 = False
            try:
                cond5, _ = recognizer._check_no_new_low(df)
            except Exception:
                cond5 = False
            # Also check the "recent limit up in 2 days" filter
            try:
                has_recent = recognizer._has_limit_up_in_recent(df, days=2)
            except Exception:
                has_recent = False

            for cond_name, cond_val, stat_key in [
                ("cond1_down", cond1, "cond1_down"),
                ("cond2_ma250", cond2, "cond2_ma250"),
                ("cond3_macd", cond3, "cond3_macd"),
                ("cond4_strength", cond4, "cond4_strength"),
                ("cond5_no_new_low", cond5, "cond5_no_new_low"),
            ]:
                if cond_val is None:
                    stats[stat_key]["skip"] = stats[stat_key].get("skip", 0) + 1
                elif cond_val:
                    stats[stat_key]["pass"] += 1
                else:
                    stats[stat_key]["fail"] += 1

            current_price = float(df["close"].iloc[-1])
            detail_records.append({
                "date": str(date.date()),
                "code": code,
                "price": round(current_price, 2),
                "cond1_down": cond1,
                "cond2_ma250": cond2,
                "cond3_macd": cond3,
                "cond4_strength": cond4,
                "cond5_no_new_low": cond5,
                "has_recent_limit": has_recent,
                "is_bottom": is_bottom,
            })

    print("\n" + "=" * 60)
    print("止跌形态各条件通过率（共 {} 个候选）".format(len(detail_records)))
    print("=" * 60)
    for name, s in stats.items():
        total = s["pass"] + s["fail"]
        if total == 0:
            pct = "N/A"
        else:
            pct = f"{s['pass']/total*100:.1f}%"
        skip_info = f" 跳过{s['skip']}" if s.get("skip", 0) > 0 else ""
        print(f"  {name}: 通过{s['pass']}/{total}={pct}{skip_info}")

    # 统计涨停过滤
    recent_limit_count = sum(1 for r in detail_records if r["has_recent_limit"])
    print(f"  近2日涨停过滤（阻断）: {recent_limit_count}/{len(detail_records)}")

    if detail_records:
        print(f"\n前20条详细记录:")
        print(f"  {'日期':>12} {'代码':>8} {'价格':>8} {'回调':>6} {'MA250':>6} {'MACD':>6} {'强度':>6} {'新低':>6} {'近2涨':>6} {'通过':>6}")
        for r in detail_records[:20]:
            print(f"  {r['date']:>12} {r['code']:>8} {r['price']:>8.2f} "
                  f"{'✓' if r['cond1_down'] else '✗':>6} "
                  f"{'✓' if r['cond2_ma250'] else '✗':>6} "
                  f"{'✓' if r['cond3_macd'] else ('-' if r['cond3_macd'] is None else '✗'):>6} "
                  f"{'✓' if r['cond4_strength'] else '✗':>6} "
                  f"{'✓' if r['cond5_no_new_low'] else '✗':>6} "
                  f"{'✓' if r['has_recent_limit'] else '✗':>6} "
                  f"{'✓' if r['is_bottom'] else '✗':>6}")


if __name__ == "__main__":
    main()
