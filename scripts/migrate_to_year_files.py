#!/usr/bin/env python3
"""
将 database.db 中 dbbardata_YYYY 表迁移到独立的 database_YYYY.db 文件。

用法: python3 migrate_to_year_files.py
"""
import sqlite3
import os
import time

SRC_DB = os.path.expanduser("~/.vntrader/database.db")
DST_DIR = os.path.expanduser("~/.vntrader")
YEARS = os.environ.get("MIGRATE_YEARS", "2023,2024,2025,2026").split(",")

SCHEMA = """
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

BATCH = 100_000


def main():
    print("=" * 60)
    print("  迁移: database.db → database_YYYY.db")
    print("=" * 60)

    total_all = 0

    for year in YEARS:
        dst_path = os.path.join(DST_DIR, f"database_{year}.db")
        src_table = f"dbbardata_{year}"

        # 检查源表是否有数据
        src = sqlite3.connect(SRC_DB)
        cnt = src.execute(f"SELECT COUNT(*) FROM {src_table}").fetchone()[0]
        src.close()

        if cnt == 0:
            print(f"\n[{year}] 源表 {src_table} 为空，跳过")
            continue

        print(f"\n[{year}] {src_table} → {dst_path}  ({cnt:,} 行)")

        # 创建目标库
        if os.path.exists(dst_path):
            os.remove(dst_path)
        dst = sqlite3.connect(dst_path)
        dst.execute("PRAGMA journal_mode=WAL")
        dst.execute("PRAGMA synchronous=NORMAL")
        dst.execute("PRAGMA cache_size=-256000")
        dst.execute(SCHEMA)
        dst.commit()

        # 流式复制
        src = sqlite3.connect(SRC_DB)
        cur = src.cursor()
        cur.execute(
            f"SELECT symbol, exchange, datetime, interval, volume, turnover, "
            f"       open_interest, open_price, high_price, low_price, close_price "
            f"FROM {src_table} ORDER BY datetime"
        )

        count = 0
        batch = []
        t_start = time.time()

        for row in cur:
            batch.append(row)
            if len(batch) >= BATCH:
                dst.executemany(
                    "INSERT INTO dbbardata (symbol, exchange, datetime, interval, "
                    "volume, turnover, open_interest, open_price, high_price, low_price, close_price) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                dst.commit()
                count += len(batch)
                elapsed = time.time() - t_start
                rate = count / elapsed if elapsed > 0 else 0
                pct = count / cnt * 100
                print(f"  {count:>12,} / {cnt:,} ({pct:>5.1f}%)  |  {rate:>8,.0f} 行/秒", flush=True)
                batch = []

        if batch:
            dst.executemany(
                "INSERT INTO dbbardata (symbol, exchange, datetime, interval, "
                "volume, turnover, open_interest, open_price, high_price, low_price, close_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            dst.commit()
            count += len(batch)

        cur.close()
        src.close()

        # 建索引
        print(f"  创建索引...", flush=True)
        dst.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bar "
            "ON dbbardata (symbol, exchange, interval, datetime)"
        )
        dst.commit()
        dst.close()

        elapsed = time.time() - t_start
        mb = os.path.getsize(dst_path) / (1024 * 1024)
        print(f"  ✓ 完成: {count:,} 行, {mb:.0f} MB, 耗时 {elapsed/60:.1f} 分钟")
        total_all += count

    # 汇总
    print(f"\n{'='*60}")
    print(f"  迁移完成，总计 {total_all:,} 行")
    print(f"{'='*60}")
    for year in YEARS:
        path = os.path.join(DST_DIR, f"database_{year}.db")
        if os.path.exists(path):
            mb = os.path.getsize(path) / (1024 * 1024)
            print(f"  database_{year}.db  {mb:>8.0f} MB")
    print(f"\n  确认无误后可删除原库释放 ~18GB:")
    print(f"    rm ~/.vntrader/database.db")
    print(f"    rm ~/.vntrader/database.db-wal")
    print(f"    rm ~/.vntrader/database.db-shm")


if __name__ == "__main__":
    main()
