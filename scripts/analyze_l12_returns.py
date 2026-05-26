"""
分析 filter_l12_results.csv 中各信号的买入后收益
"""
import os
import sys
import sqlite3
import pandas as pd
import numpy as np

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
sys.path.insert(0, PROJECT_ROOT)

CSV_PATH = os.path.join(PROJECT_ROOT, "scripts", "output", "filter_l12_results.csv")
DB_PATH = os.path.expanduser("~/.vntrader/database.db")

# 读取信号
signals = pd.read_csv(CSV_PATH)
print(f"加载信号: {len(signals)} 条, {signals['date'].nunique()} 个交易日, {signals['symbol'].nunique()} 只股票")

# 加载所有涉及的股票数据（拉取到2026-06-01确保有足够后验数据）
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
            AND datetime >= '2026-01-01 09:00:00'
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
print(f"加载行情: {len(df):,} 行")

# 按股票分组
stock_data = {s: g.set_index('trade_date').sort_index() for s, g in df.groupby('symbol')}

# 持有期
horizons = [1, 3, 5, 10, 20]
results = []

for _, sig in signals.iterrows():
    sym = str(sig['symbol']).zfill(6)  # 确保6位代码
    entry_date = pd.Timestamp(sig['date'])
    entry_price = sig['current_price']
    
    # 修复symbol匹配（数据库的symbol可能是6位或带点的）
    if sym not in stock_data:
        # 尝试去掉前导0
        alt_sym = str(int(sym)) if sym.startswith('0') else sym
        if alt_sym not in stock_data:
            continue
        sym = alt_sym
    
    df_s = stock_data[sym]
    if entry_date not in df_s.index:
        continue
    
    idx = df_s.index.get_loc(entry_date)
    row = {
        'date': str(entry_date.date()),
        'symbol': sig['symbol'],
        'name': sig['name'],
        'industry': sig['industry'],
        'entry_price': entry_price,
        'down_amplitude': sig['down_amplitude'],
        'distance_from_stop': sig['distance_from_stop'],
        'macd_bar': sig['macd_bar'],
    }
    
    for h in horizons:
        future_idx = idx + h
        if future_idx < len(df_s):
            future_close = float(df_s['close'].iloc[future_idx])
            ret = (future_close - entry_price) / entry_price
            row[f'ret_{h}d'] = round(ret, 4)
        else:
            row[f'ret_{h}d'] = None
    
    results.append(row)

results_df = pd.DataFrame(results)
print(f"\n有效统计: {len(results_df)} 条 (原始 {len(signals)} 条)")

# 统计汇总
print("\n" + "=" * 70)
print("买入后收益统计")
print("=" * 70)
for h in horizons:
    col = f'ret_{h}d'
    valid = results_df[col].dropna()
    if len(valid) == 0:
        continue
    print(f"\n--- {h}日持有 ---")
    print(f"  样本数:    {len(valid)}")
    print(f"  平均收益:  {valid.mean():.2%}")
    print(f"  中位数:    {valid.median():.2%}")
    print(f"  胜率:      {(valid > 0).mean():.2%} ({sum(valid > 0)}/{len(valid)})")
    print(f"  最大涨幅:  {valid.max():.2%}")
    print(f"  最大跌幅:  {valid.min():.2%}")
    print(f"  标准差:    {valid.std():.2%}")

# 按行业分组
print("\n" + "=" * 70)
print("按行业分组的 10日收益")
print("=" * 70)
for ind, grp in results_df.groupby('industry'):
    valid = grp['ret_10d'].dropna()
    if len(valid) == 0:
        continue
    print(f"\n{ind} ({len(valid)}笔):")
    print(f"  平均: {valid.mean():.2%}  中位数: {valid.median():.2%}  胜率: {(valid > 0).mean():.2%}")

# 按月份分组
print("\n" + "=" * 70)
print("按月份分组的 10日收益")
print("=" * 70)
results_df['month'] = pd.to_datetime(results_df['date']).dt.strftime('%Y-%m')
for mo, grp in results_df.groupby('month'):
    valid = grp['ret_10d'].dropna()
    if len(valid) == 0:
        continue
    print(f"  {mo} ({len(valid):>2d}笔): 平均 {valid.mean():+.2%}  胜率 {(valid > 0).mean():.0%}")

# 保存详细结果
out_csv = os.path.join(PROJECT_ROOT, "scripts", "output", "filter_l12_returns.csv")
results_df.to_csv(out_csv, index=False)
print(f"\n详细结果: {out_csv}")
