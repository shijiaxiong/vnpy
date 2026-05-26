"""
详细表格：机器人板块通过的9个信号
"""
import os, sys, sqlite3, json
import pandas as pd
import numpy as np

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
DB_PATH = os.path.expanduser("~/.vntrader/database.db")
CSV_PATH = os.path.join(PROJECT_ROOT, "scripts", "output", "filter_l12_results.csv")
SECTOR_PATH = os.path.join(PROJECT_ROOT, ".cache", "sector_stocks.json")

signals = pd.read_csv(CSV_PATH)
print(f"加载信号: {len(signals)} 条")

# 加载所有涉及股票的完整数据
symbols = signals['symbol'].unique().tolist()
quoted = ', '.join(f"'{s}'" for s in symbols)

conn = sqlite3.connect(DB_PATH)
query = f'''
    WITH daily AS (
        SELECT symbol, DATE(datetime) AS trade_date,
               high_price AS high, low_price AS low,
               FIRST_VALUE(close_price) OVER (
                   PARTITION BY symbol, DATE(datetime) ORDER BY datetime
                   ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
               ) AS open_price,
               LAST_VALUE(close_price) OVER (
                   PARTITION BY symbol, DATE(datetime) ORDER BY datetime
                   ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
               ) AS close_price
        FROM dbbardata
        WHERE interval = '5m'
            AND datetime >= '2025-10-01 09:00:00'
            AND datetime <= '2026-06-01 15:10:00'
            AND symbol IN ({quoted})
    )
    SELECT symbol, trade_date,
           open_price AS open, MAX(high) AS high, MIN(low) AS low,
           close_price AS close
    FROM daily GROUP BY symbol, trade_date ORDER BY symbol, trade_date
'''
df = pd.read_sql(query, conn)
conn.close()
df['trade_date'] = pd.to_datetime(df['trade_date'])
stock_data = {s: g.set_index('trade_date').sort_index() for s, g in df.groupby('symbol')}

# 汇总表
rows = []

for _, sig in signals.iterrows():
    sym = str(sig['symbol']).zfill(6)
    entry_date = pd.Timestamp(sig['date'])
    sector = sig['industry']
    name = sig['name']

    if sym not in stock_data:
        continue
    df_s = stock_data[sym]
    if entry_date not in df_s.index:
        continue

    idx = df_s.index.get_loc(entry_date)

    # 从信号日前30天找最高点
    lookback_start = max(0, idx - 30)
    pre_segment = df_s.iloc[lookback_start:idx]
    if len(pre_segment) == 0:
        continue

    peak_price = float(pre_segment['high'].max())
    peak_idx_in_seg = pre_segment['high'].idxmax()
    peak_date = str(peak_idx_in_seg.date())

    # 从最高点到信号日之间的最低点
    peak_idx = df_s.index.get_loc(peak_idx_in_seg)
    between_segment = df_s.iloc[peak_idx:idx+1]
    bottom_price = float(between_segment['low'].min())
    bottom_date = str(between_segment['low'].idxmin().date())

    # 跌幅和时间跨度
    decline_pct = (peak_price - bottom_price) / peak_price
    time_span_days = (entry_date - pd.Timestamp(peak_date)).days

    entry_price = sig['current_price']
    entry_close = float(df_s.loc[entry_date, 'close'])
    dist_from_stop = sig['distance_from_stop']
    stop_price = sig['stop_loss_price']

    # 卖出价格 = 入场后20天内最高价
    exit_idx_end = min(len(df_s), idx + 21)
    exit_segment = df_s.iloc[idx+1:exit_idx_end]
    if len(exit_segment) > 0:
        exit_price = float(exit_segment['high'].max())
        exit_date = str(exit_segment['high'].idxmax().date())
        exit_return = (exit_price - entry_price) / entry_price
    else:
        exit_price = entry_price
        exit_date = ""
        exit_return = 0

    rows.append({
        '板块': sector,
        '代码': sym,
        '名称': name,
        '最高点价格': round(peak_price, 2),
        '最高点日期': peak_date,
        '最低点价格': round(bottom_price, 2),
        '最低点日期': bottom_date,
        '跌幅': f"{decline_pct:.1%}",
        '高→低天数': time_span_days,
        '信号日期': str(entry_date.date()),
        '买入价': round(entry_price, 2),
        '当日收盘': round(entry_close, 2),
        '距撤军线': f"{dist_from_stop:.1%}",
        '撤军线': round(stop_price, 2),
        '20日最高卖出价': round(exit_price, 2),
        '卖出日期': exit_date,
        '潜在收益': f"{exit_return:+.1%}",
    })

# 打印表格
df_out = pd.DataFrame(rows)
print(df_out.to_string(index=False))

# 保存
out_csv = os.path.join(PROJECT_ROOT, "scripts", "output", "robotics_signals_detail.csv")
df_out.to_csv(out_csv, index=False)
print(f"\n结果已保存: {out_csv}")
