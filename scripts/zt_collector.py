#!/usr/bin/env python3
"""
涨停板数据采集器
==================
每日从东方财富拉取涨停板数据，存入 CSV 缓存 + SQLite

数据源: 东方财富 (akshare.stock_zt_pool_em)

用法:
  python scripts/zt_collector.py                    # 采集今天
  python scripts/zt_collector.py --date 20260527    # 指定日期
  python scripts/zt_collector.py --start 20260520   # 日期范围
  python scripts/zt_collector.py --mode db          # 只写数据库
  python scripts/zt_collector.py --mode csv         # 只写CSV(默认csv)

依赖: akshare, pandas
"""

# 必须在所有 import 之前清除代理，否则 akshare/requests 已读取代理配置
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

import akshare as ak
import pandas as pd
import sqlite3
import os
import sys
import argparse
import time
from datetime import datetime, timedelta

# ===== 路径 =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(PROJECT_DIR, "cache_zt")
DB_PATH = os.path.join(PROJECT_DIR, ".workbuddy", "market_mood.db")

os.makedirs(CACHE_DIR, exist_ok=True)

# ===== DDL =====
DDL_ZT_DAILY = """
CREATE TABLE IF NOT EXISTS zt_daily (
    trade_date  TEXT NOT NULL,
    stock_code  TEXT NOT NULL,
    stock_name  TEXT,
    pct_change  REAL,
    latest_price REAL,
    turnover    REAL,
    float_mv    REAL,
    total_mv    REAL,
    turnover_rate REAL,
    seal_amount REAL,
    first_time  TEXT,
    last_time   TEXT,
    bomb_count  INTEGER,
    zt_stat     TEXT,
    board_count INTEGER,
    industry    TEXT,
    PRIMARY KEY (trade_date, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_zt_date ON zt_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_zt_board ON zt_daily(trade_date, board_count);
"""

# CSV 列名 → DB 字段映射
CSV_COL_MAP = {
    "代码": "stock_code",
    "名称": "stock_name",
    "涨跌幅": "pct_change",
    "最新价": "latest_price",
    "成交额": "turnover",
    "流通市值": "float_mv",
    "总市值": "total_mv",
    "换手率": "turnover_rate",
    "封板资金": "seal_amount",
    "首次封板时间": "first_time",
    "最后封板时间": "last_time",
    "炸板次数": "bomb_count",
    "涨停统计": "zt_stat",
    "连板数": "board_count",
    "所属行业": "industry",
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL_ZT_DAILY)
    conn.commit()
    return conn


def fetch_zt(date_str):
    """
    拉取单日涨停板数据
    date_str: 'YYYYMMDD'
    返回: (DataFrame, int) 或 (None, None)
    """
    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or len(df) == 0:
            return None, None
        return df, len(df)
    except Exception as e:
        print(f"  ❌ 获取失败: {e}")
        return None, None


def save_csv(df, date_str):
    """保存 CSV 缓存"""
    csv_path = os.path.join(CACHE_DIR, f"{date_str}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def save_db(df, date_str, conn):
    """写入 SQLite"""
    # 先删除该日数据（幂等写入）
    conn.execute("DELETE FROM zt_daily WHERE trade_date = ?", (date_str,))

    # 只保留有映射的列（跳过"序号"等无关列）
    mapped_cols = [(col, CSV_COL_MAP[col]) for col in df.columns if col in CSV_COL_MAP]
    cols_db = ["trade_date"] + [db_col for _, db_col in mapped_cols]

    values = []
    for _, row in df.iterrows():
        rec = {"trade_date": date_str}
        for csv_col, db_col in mapped_cols:
            val = row.get(csv_col)
            if pd.isna(val):
                val = None
            rec[db_col] = val
        values.append(rec)

    placeholders = ", ".join([f":{c}" for c in cols_db])
    cols_str = ", ".join(cols_db)
    sql = f"INSERT INTO zt_daily ({cols_str}) VALUES ({placeholders})"
    conn.executemany(sql, values)


def get_trade_dates(start_date, end_date):
    """获取交易日列表"""
    try:
        df = ak.tool_trade_date_hist_sina()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        start_dt = pd.Timestamp(datetime.strptime(start_date, "%Y%m%d"))
        end_dt = pd.Timestamp(datetime.strptime(end_date, "%Y%m%d"))
        mask = (df["trade_date"] >= start_dt) & (df["trade_date"] <= end_dt)
        dates = sorted(df[mask]["trade_date"].dt.strftime("%Y%m%d").tolist())
        return dates
    except Exception as e:
        print(f"  [WARNING] 交易日历获取失败: {e}，使用连续日期")
        dates = []
        d = datetime.strptime(start_date, "%Y%m%d")
        end = datetime.strptime(end_date, "%Y%m%d")
        while d <= end:
            if d.weekday() < 5:
                dates.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        return dates


def collect_date(date_str, mode="both", conn=None):
    """采集单个交易日"""
    # 检查 CSV 缓存
    csv_path = os.path.join(CACHE_DIR, f"{date_str}.csv")

    need_fetch = False
    need_csv = mode in ("csv", "both")
    need_db = mode in ("db", "both")

    if need_csv and not os.path.exists(csv_path):
        need_fetch = True

    if need_fetch:
        df, count = fetch_zt(date_str)
        if df is None:
            return None

        if need_csv:
            save_csv(df, date_str)
        if need_db and conn:
            save_db(df, date_str, conn)
            conn.commit()

        print(f"  ✅ {date_str}: {count} 只涨停")
        return count
    else:
        # 已有 CSV，检查是否需要写 DB
        if need_db and conn:
            existing = conn.execute(
                "SELECT COUNT(*) FROM zt_daily WHERE trade_date = ?", (date_str,)
            ).fetchone()[0]
            if existing == 0 and os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                save_db(df, date_str, conn)
                conn.commit()
                print(f"  📥 {date_str}: CSV→DB 导入 ({len(df)} 条)")
                return len(df)
            else:
                print(f"  ⏭️ {date_str}: 已缓存 (DB:{existing}条)")
                return existing
        return 0


def main():
    parser = argparse.ArgumentParser(description="涨停板数据采集器")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD")
    parser.add_argument("--start", default=None, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--mode", default="both",
                        choices=["csv", "db", "both"],
                        help="存储模式 (default: both)")
    parser.add_argument("--list-cache", action="store_true",
                        help="列出已缓存的日期")
    parser.add_argument("--check-db", action="store_true",
                        help="检查 DB 状态")
    args = parser.parse_args()

    if args.list_cache:
        files = sorted(os.listdir(CACHE_DIR))
        csvs = [f.replace(".csv", "") for f in files if f.endswith(".csv")]
        print(f"CSV 缓存: {len(csvs)} 个交易日")
        for d in csvs:
            print(f"  {d}")
        return

    if args.check_db:
        conn = init_db()
        rows = conn.execute(
            "SELECT trade_date, COUNT(*) as cnt, MAX(board_count) as max_b "
            "FROM zt_daily GROUP BY trade_date ORDER BY trade_date DESC LIMIT 20"
        ).fetchall()
        print(f"{'日期':<12} {'涨停数':<8} {'最高板':<8}")
        for dt, cnt, mb in rows:
            print(f"  {dt:<10} {cnt:<8} {mb or 0:<8}")
        conn.close()
        return

    # 日期处理
    if args.date:
        dates = [args.date]
    elif args.start:
        if args.end:
            dates = get_trade_dates(args.start, args.end)
        else:
            dates = get_trade_dates(args.start, datetime.now().strftime("%Y%m%d"))
    else:
        dates = [datetime.now().strftime("%Y%m%d")]

    print(f"\n{'='*50}")
    print(f"  涨停板数据采集")
    print(f"  日期: {dates[0]}" if len(dates) == 1 else f"  日期: {dates[0]} → {dates[-1]} ({len(dates)} 天)")
    print(f"  模式: {args.mode}")
    print(f"{'='*50}\n")

    conn = init_db() if args.mode in ("db", "both") else None
    success = 0
    fail = 0
    total_count = 0

    for i, ds in enumerate(dates):
        result = collect_date(ds, mode=args.mode, conn=conn)
        if result is not None:
            success += 1
            total_count += result
        else:
            fail += 1
        if (i + 1) % 20 == 0:
            time.sleep(0.5)

    if conn:
        conn.close()

    print(f"\n{'='*50}")
    print(f"  完成: {success} 成功 / {fail} 失败")
    if total_count:
        print(f"  涨停总数: {total_count}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
