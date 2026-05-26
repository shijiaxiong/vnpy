"""分析止损缓冲：多周期低点比较"""
import sqlite3, pandas as pd, numpy as np

signals = [
    ('603662', '柯力传感', '2026-03-13', 59.11, 60.07),
    ('300276', '三丰智能', '2026-03-17', 7.65, 7.71),
    ('688017', '绿的谐波', '2026-03-19', 189.00, 189.65),
    ('300024', '机器人', '2026-03-26', 14.64, 14.88),
    ('300276', '三丰智能②', '2026-03-26', 6.73, 6.85),
    ('688165', '埃夫特', '2026-03-31', 16.45, 16.53),
]

syms = list(set(s[0] for s in signals))
q = ', '.join(f"'{s}'" for s in syms)

conn = sqlite3.connect('/Users/zyb/.vntrader/database.db')
query = f'''
    WITH daily AS (
        SELECT symbol, DATE(datetime) AS trade_date,
               high_price AS high, low_price AS low,
               LAST_VALUE(close_price) OVER (
                   PARTITION BY symbol, DATE(datetime) ORDER BY datetime
                   ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
               ) AS close_price
        FROM dbbardata WHERE interval = '5m' AND symbol IN ({q})
            AND datetime >= '2026-02-01 09:00:00' AND datetime <= '2026-05-30 15:10:00'
    )
    SELECT symbol, trade_date, MAX(high) AS high, MIN(low) AS low, close_price AS close
    FROM daily GROUP BY symbol, trade_date ORDER BY symbol, trade_date
'''
df = pd.read_sql(query, conn); conn.close()
df['trade_date'] = pd.to_datetime(df['trade_date'])
stocks = {s: g.set_index('trade_date').sort_index() for s, g in df.groupby('symbol')}

print(f"{'名称':10s} {'10日低':>7s} {'20日低':>7s} {'买入后':>7s} {'低于10日低':>8s} {'低于20日低':>8s} {'回到买价':>10s} {'20日高':>7s}")
print("-" * 95)

for sym, name, date_str, stop10, entry in signals:
    if sym not in stocks: continue
    df_s = stocks[sym]
    entry_date = pd.Timestamp(date_str)
    if entry_date not in df_s.index: continue

    idx = df_s.index.get_loc(entry_date)
    
    # 入场时的20日最低价
    lookback = df_s.iloc[max(0, idx-19):idx+1]
    stop20 = float(lookback['low'].min())

    # 入场后30天数据
    end_idx = min(len(df_s), idx + 31)
    fut = df_s.iloc[idx+1:end_idx]
    if len(fut) == 0: continue

    lowest = float(fut['low'].min())
    vs10 = (lowest - stop10) / stop10
    vs20 = (lowest - stop20) / stop20

    # 回到买入价
    back_date = '未回到'
    for i in range(len(fut)):
        if float(fut['close'].iloc[i]) >= entry:
            actual_idx = df_s.index.get_loc(entry_date) + 1 + i
            back_date = str(df_s.index[actual_idx].date()) if actual_idx < len(df_s) else '?'
            break

    peak = float(fut['high'].max())

    print(f"{name:10s} {stop10:>7.2f} {stop20:>7.2f} {lowest:>7.2f} {vs10:>+7.1%} {vs20:>+7.1%} {back_date:>10s} {peak:>7.2f}")

# 统计
print()
print("结论：")
print("  止损用 10日低点 → 全部被震出（买入位就是10日低点附近）")
print("  止损用 20日低点 → 需要看具体数字")
print("  真正的安全止损位 → 需要在10日低点下方留充足空间")
