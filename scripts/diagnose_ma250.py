"""
诊断MA250条件：对比当前价与250日均线
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

from vnpy.alpha.yuanjun.bottom_pattern import BottomPatternRecognizer
from vnpy.alpha.yuanjun.config import StrategyConfig

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

    bottom_cfg = StrategyConfig.leader_style().bottom_config
    print(f"\nBottomPatternConfig:")
    print(f"  above_ma250 = {bottom_cfg.above_ma250}")
    print(f"  down_amplitude_min = {bottom_cfg.down_amplitude_min}")

    # Sample some stocks to check MA250
    print(f"\n{'代码':>8} {'日期':>12} {'收盘价':>8} {'MA250':>10} {'价/MA':>8} {'结果':>8} {'数据天数':>8}")
    print("-" * 80)

    checked = 0
    above_count = 0
    below_count = 0

    for sym, df in list(stock_data.items())[:30]:
        if len(df) < 250:
            continue

        # Calculate MA250 on all data, then get the last value
        ma250 = df["close"].rolling(250).mean()
        last_ma250 = ma250.iloc[-1]
        last_price = float(df["close"].iloc[-1])

        if pd.isna(last_ma250):
            continue

        ratio = last_price / last_ma250
        is_above = last_price >= last_ma250
        if is_above:
            above_count += 1
        else:
            below_count += 1

        checked += 1
        if checked <= 20:
            print(f"  {sym:>8} {df.index[-1].date()} {last_price:>8.2f} {last_ma250:>10.2f} {ratio:>7.2%} {'✓' if is_above else '✗':>8} {len(df):>8}")

    print(f"\n汇总: 抽样{checked}只, 站上MA250={above_count}, 跌破MA250={below_count}")
    if checked > 0:
        print(f"  站上率: {above_count/checked:.1%}")


if __name__ == "__main__":
    main()
