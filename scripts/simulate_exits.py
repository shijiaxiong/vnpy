"""按策略规则模拟退出（含ATR止损对比）"""
import os, sys, sqlite3
import pandas as pd
import numpy as np

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
DB_PATH = os.path.expanduser("~/.vntrader/database.db")

# 从detail CSV读信号
detail = pd.read_csv(os.path.join(PROJECT_ROOT, "scripts/output/robotics_signals_detail.csv"))
print(f"信号数: {len(detail)}")

# 加载数据
symbols = list(set(detail['代码'].astype(str).str.zfill(6)))
quoted = ', '.join(f"'{s}'" for s in symbols)

conn = sqlite3.connect(DB_PATH)
query = f'''
    WITH daily AS (
        SELECT symbol, DATE(datetime) AS trade_date,
               high_price AS high, low_price AS low,
               LAST_VALUE(close_price) OVER (
                   PARTITION BY symbol, DATE(datetime) ORDER BY datetime
                   ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
               ) AS close_price
        FROM dbbardata WHERE interval = '5m' AND symbol IN ({quoted})
            AND datetime >= '2026-01-01 09:00:00' AND datetime <= '2026-06-01 15:10:00'
    )
    SELECT symbol, trade_date, MAX(high) AS high, MIN(low) AS low, close_price AS close
    FROM daily GROUP BY symbol, trade_date ORDER BY symbol, trade_date
'''
df = pd.read_sql(query, conn); conn.close()
df['trade_date'] = pd.to_datetime(df['trade_date'])
stocks = {s: g.set_index('trade_date').sort_index() for s, g in df.groupby('symbol')}

print(f"\n{'名称':8s} {'买入日':>10s} {'买入价':>7s} {'ATR14':>7s} {'撤军线':>7s} {'ATR止损':>7s} {'卖出日':>10s} {'卖出价':>7s} {'收益':>7s} {'原因':8s} {'持仓':5s}")
print("-" * 100)

for _, sig in detail.iterrows():
    sym = str(sig['代码']).zfill(6)
    name = sig['名称']
    entry_date = pd.Timestamp(sig['信号日期'])
    entry_price = sig['买入价']
    stop10 = sig['撤军线']

    if sym not in stocks or entry_date not in stocks[sym].index:
        continue

    df_s = stocks[sym]
    idx = df_s.index.get_loc(entry_date)

    # 计算入场时的 ATR(14)
    hist_atr = df_s.iloc[max(0, idx-13):idx+1]
    if len(hist_atr) >= 2:
        tr_list = []
        for i in range(1, len(hist_atr)):
            hl = abs(float(hist_atr['high'].iloc[i]) - float(hist_atr['low'].iloc[i]))
            hc = abs(float(hist_atr['high'].iloc[i]) - float(hist_atr['close'].iloc[i-1]))
            lc = abs(float(hist_atr['low'].iloc[i]) - float(hist_atr['close'].iloc[i-1]))
            tr_list.append(max(hl, hc, lc))
        atr14 = np.mean(tr_list) if tr_list else 0.01
    else:
        atr14 = 0.01

    # ATR止损 = 入场价 - 1.5×ATR
    atr_stop = entry_price - 1.5 * atr14

    # 模拟：止损用min(撤军线, ATR止损) → 取更宽松的那个
    use_atr = atr_stop < stop10  # ATR止损更低（更宽松）
    effective_stop = min(stop10, atr_stop) if use_atr else stop10

    exit_price = entry_price
    exit_date = ""
    exit_reason = "到期"
    hold_days = 0

    for i in range(idx + 1, min(len(df_s), idx + 21)):
        day = df_s.iloc[i]
        date_str = str(day.name.date())
        close_p = float(day['close'])
        low_p = float(day['low'])
        high_p = float(day['high'])

        if low_p <= effective_stop:
            exit_price = effective_stop
            exit_date = date_str
            exit_reason = "ATR止损" if use_atr else "止损"
            hold_days = (pd.Timestamp(exit_date) - entry_date).days
            break

        if i == min(len(df_s), idx + 11) - 1:
            exit_price = close_p
            exit_date = date_str
            exit_reason = "到期"
            hold_days = (pd.Timestamp(exit_date) - entry_date).days
            break

    if not exit_date:
        exit_price = close_p
        exit_date = date_str
        exit_reason = "到期"

    ret = (exit_price - entry_price) / entry_price
    stop_type = "ATR" if use_atr else "撤军线"

    print(f"{name:8s} {sig['信号日期']:>10s} {entry_price:>7.2f} {atr14:>7.3f} {stop10:>7.2f} {atr_stop:>7.2f} {exit_date:>10s} {exit_price:>7.2f} {ret:>+6.1%} {exit_reason:8s} {hold_days:>5d}")
