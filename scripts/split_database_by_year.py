#!/usr/bin/env python3
"""
按年份拆分 vnpy SQLite 数据库（index-safe 版本）。
不依赖损坏索引，使用全表扫描 + 流式写入。
"""
import sqlite3
import os
import time

SRC_DB = os.path.expanduser("~/.vntrader/database.db")
DST_DIR = os.path.expanduser("~/.vntrader")

YEARS = [
    ("2023", "database_2023.db"),
    ("2024", "database_2024.db"),
    ("2025", "database_2025.db"),
    ("2026", "database_2026.db"),
]

# 表 schema（手动定义，避免读取源库的损坏索引）
SCHEMA_DBBARDATA = """
CREATE TABLE IF NOT EXISTS dbbardata (
    id INTEGER NOT NULL PRIMARY KEY,
    symbol VARCHAR(255) NOT NULL,
    exchange VARCHAR(255) NOT NULL,
    datetime DATETIME NOT NULL,
    interval VARCHAR(255) NOT NULL,
    volume REAL NOT NULL,
    turnover REAL NOT NULL,
    open_interest REAL NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    close_price REAL NOT NULL
)
"""

SCHEMA_DBBAROVERVIEW = """
CREATE TABLE IF NOT EXISTS dbbaroverview (
    id INTEGER NOT NULL PRIMARY KEY,
    symbol VARCHAR(255) NOT NULL,
    exchange VARCHAR(255) NOT NULL,
    interval VARCHAR(255) NOT NULL,
    count INTEGER NOT NULL,
    start DATETIME NOT NULL,
    end DATETIME NOT NULL
)
"""

SCHEMA_DBTICKDATA = """
CREATE TABLE IF NOT EXISTS dbtickdata (
    id INTEGER NOT NULL PRIMARY KEY,
    symbol VARCHAR(255) NOT NULL,
    exchange VARCHAR(255) NOT NULL,
    datetime DATETIME NOT NULL,
    name VARCHAR(255) NOT NULL,
    volume REAL NOT NULL,
    turnover REAL NOT NULL,
    open_interest REAL NOT NULL,
    last_price REAL NOT NULL,
    last_volume REAL NOT NULL,
    limit_up REAL NOT NULL,
    limit_down REAL NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    pre_close REAL NOT NULL,
    bid_price_1 REAL NOT NULL, bid_volume_1 REAL NOT NULL,
    bid_price_2 REAL NOT NULL, bid_volume_2 REAL NOT NULL,
    bid_price_3 REAL NOT NULL, bid_volume_3 REAL NOT NULL,
    bid_price_4 REAL NOT NULL, bid_volume_4 REAL NOT NULL,
    bid_price_5 REAL NOT NULL, bid_volume_5 REAL NOT NULL,
    ask_price_1 REAL NOT NULL, ask_volume_1 REAL NOT NULL,
    ask_price_2 REAL NOT NULL, ask_volume_2 REAL NOT NULL,
    ask_price_3 REAL NOT NULL, ask_volume_3 REAL NOT NULL,
    ask_price_4 REAL NOT NULL, ask_volume_4 REAL NOT NULL,
    ask_price_5 REAL NOT NULL, ask_volume_5 REAL NOT NULL
)
"""

SCHEMA_DBTICKOVERVIEW = """
CREATE TABLE IF NOT EXISTS dbtickoverview (
    id INTEGER NOT NULL PRIMARY KEY,
    symbol VARCHAR(255) NOT NULL,
    exchange VARCHAR(255) NOT NULL,
    count INTEGER NOT NULL,
    start DATETIME NOT NULL,
    end DATETIME NOT NULL
)
"""


def create_target_db(path):
    """创建目标数据库，手动建表（不复制损坏索引）"""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-256000")
    conn.execute(SCHEMA_DBBARDATA)
    conn.execute(SCHEMA_DBBAROVERVIEW)
    conn.execute(SCHEMA_DBTICKDATA)
    conn.execute(SCHEMA_DBTICKOVERVIEW)
    conn.commit()
    conn.close()


def count_year(src_path, year):
    """获取某年份行数（全表扫描，不触损坏索引）"""
    conn = sqlite3.connect(src_path)
    row = conn.execute(
        f"SELECT COUNT(*) FROM dbbardata "
        f"WHERE datetime >= '{year}-01-01' AND datetime < '{int(year)+1}-01-01'"
    ).fetchone()
    conn.close()
    return row[0]


def copy_year(src_path, dst_path, year):
    """流式复制：从源库读取 → 写入目标库（Python 层中转）"""
    next_year = str(int(year) + 1)
    batch = 100_000  # 每批 10 万行

    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    dst.execute("PRAGMA synchronous=OFF")
    dst.execute("PRAGMA journal_mode=WAL")

    # 用游标流式读取
    cur = src.cursor()
    cur.execute(
        f"SELECT symbol, exchange, datetime, interval, volume, turnover, "
        f"       open_interest, open_price, high_price, low_price, close_price "
        f"FROM dbbardata "
        f"WHERE datetime >= '{year}-01-01' AND datetime < '{next_year}-01-01' "
        f"ORDER BY datetime"
    )

    count = 0
    rows_batch = []
    t_start = time.time()

    for row in cur:
        rows_batch.append(row)
        if len(rows_batch) >= batch:
            dst.executemany(
                "INSERT INTO dbbardata (symbol, exchange, datetime, interval, "
                "volume, turnover, open_interest, open_price, high_price, low_price, close_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows_batch,
            )
            dst.commit()
            count += len(rows_batch)
            elapsed = time.time() - t_start
            rate = count / elapsed if elapsed > 0 else 0
            print(f"  {count:>12,} 行  |  {rate:>8,.0f} 行/秒", flush=True)
            rows_batch = []

    # 处理最后一批
    if rows_batch:
        dst.executemany(
            "INSERT INTO dbbardata (symbol, exchange, datetime, interval, "
            "volume, turnover, open_interest, open_price, high_price, low_price, close_price) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows_batch,
        )
        dst.commit()
        count += len(rows_batch)

    cur.close()
    src.close()

    # 创建索引
    dst.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS dbbardata_symbol_exchange_interval_datetime "
        "ON dbbardata (symbol, exchange, interval, datetime)"
    )

    # 重建 dbbaroverview
    dst.execute("DELETE FROM dbbaroverview")
    ov_rows = dst.execute(
        "SELECT symbol, exchange, interval, "
        "       COUNT(*) as cnt, MIN(datetime), MAX(datetime) "
        "FROM dbbardata GROUP BY symbol, exchange, interval"
    ).fetchall()
    for r in ov_rows:
        dst.execute(
            "INSERT INTO dbbaroverview (symbol, exchange, interval, count, start, end) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (r[0], r[1], r[2], r[3], r[4], r[5]),
        )
    dst.commit()
    dst.close()

    total_time = time.time() - t_start
    print(f"  ✓ 完成: {count:,} 行, 耗时 {total_time/60:.1f} 分钟")

    return count


def show_results():
    print(f"\n{'='*60}")
    print(f"  {'年份':<6} {'文件':<25} {'大小':>10} {'K线行数':>14}")
    print(f"  {'-'*60}")

    total_rows = 0
    for year, fname in YEARS:
        path = os.path.join(DST_DIR, fname)
        if os.path.exists(path):
            mb = os.path.getsize(path) / (1024 * 1024)
            conn = sqlite3.connect(path)
            bars = conn.execute("SELECT COUNT(*) FROM dbbardata").fetchone()[0]
            total_rows += bars
            conn.close()
            print(f"  {year:<6} {fname:<25} {mb:>8.0f} MB {bars:>14,}")

    src_gb = os.path.getsize(SRC_DB) / (1024**3)
    print(f"\n  原库: {src_gb:.1f} GB → 拆分为 {total_rows:,} 行 (原 118,072,774 行)")


def main():
    print("=" * 60)
    print("  vnpy 数据库按年度拆分 (index-safe)")
    print(f"  源库: {SRC_DB}")
    print("=" * 60)

    # Step 1: 创建目标库
    print("\n[1/3] 创建年度数据库...")
    for year, fname in YEARS:
        path = os.path.join(DST_DIR, fname)
        create_target_db(path)
        print(f"  ✓ {fname}")

    # Step 2: 流式复制
    print("\n[2/3] 按年份流式复制数据...")
    for year, fname in YEARS:
        dst_path = os.path.join(DST_DIR, fname)
        print(f"\n  --- {year} 年 → {fname} ---")
        copy_year(SRC_DB, dst_path, year)

    # Step 3: 汇总
    print("\n[3/3] 结果汇总")
    show_results()

    print(f"\n✅ 拆分完成！确认数据无误后可删除原库释放 ~15GB 空间：")
    print(f"   rm {SRC_DB}")
    print(f"   rm {SRC_DB}-wal")
    print(f"   rm {SRC_DB}-shm")


if __name__ == "__main__":
    main()
