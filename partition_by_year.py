#!/usr/bin/env python3
"""
将 dbbardata 单表按年拆分为 dbbardata_2024 / dbbardata_2025 / dbbardata_2026，
并创建 dbbardata 视图做 UNION ALL，保证 vnpy 兼容。
"""

import sqlite3
import os
import shutil

SRC_DB = os.path.expanduser("~/.vntrader/database_clean.db")
DST_DB = os.path.expanduser("~/.vntrader/database_partitioned.db")
INTERVAL = "5m"

def main():
    src = sqlite3.connect(SRC_DB)
    src.row_factory = sqlite3.Row

    # 获取年份范围
    years = []
    for row in src.execute(
        "SELECT DISTINCT substr(datetime,1,4) AS y FROM dbbardata WHERE interval=? ORDER BY y",
        (INTERVAL,),
    ):
        years.append(row["y"])
    print(f"发现年份: {years}")

    # 统计各年行数
    for y in years:
        cnt = src.execute(
            "SELECT COUNT(*) FROM dbbardata WHERE interval=? AND substr(datetime,1,4)=?",
            (INTERVAL, y),
        ).fetchone()[0]
        print(f"  {y}: {cnt:,} 行")

    # 创建新库（带按年分表）
    dst = sqlite3.connect(DST_DB)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=OFF")
    dst.execute("PRAGMA cache_size=-1000000")  # 1GB cache

    # 创建年表
    for y in years:
        dst.execute(f"""
            CREATE TABLE dbbardata_{y} (
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
        """)
        dst.execute(f"""
            CREATE UNIQUE INDEX idx_dbbardata_{y}_symbol
            ON dbbardata_{y} (symbol, exchange, interval, datetime)
        """)
    print("年表已创建")

    # 迁移 dbbardata
    total = 0
    for y in years:
        dst.execute(f"DELETE FROM dbbardata_{y}")
        dst.commit()

        rows = src.execute(
            "SELECT * FROM dbbardata WHERE interval=? AND substr(datetime,1,4)=?",
            (INTERVAL, y),
        )
        batch = []
        batch_size = 50000
        count = 0

        for r in rows:
            batch.append((
                r["id"], r["symbol"], r["exchange"], r["datetime"],
                r["interval"], r["volume"], r["turnover"], r["open_interest"],
                r["open_price"], r["high_price"], r["low_price"], r["close_price"],
            ))
            if len(batch) >= batch_size:
                dst.executemany(
                    f"INSERT OR IGNORE INTO dbbardata_{y} VALUES ({','.join('?'*13)})",
                    batch,
                )
                dst.commit()
                count += len(batch)
                print(f"  {y}: {count:,} / ...", end="\r")
                batch = []

        if batch:
            dst.executemany(
                f"INSERT OR IGNORE INTO dbbardata_{y} VALUES ({','.join('?'*13)})",
                batch,
            )
            dst.commit()
            count += len(batch)

        total += count
        print(f"  {y}: {count:,} 行 ✅")

    print(f"\n总计迁移: {total:,} 行")

    # 创建 dbbardata 视图（UNION ALL 所有年表）
    union_parts = "\n    UNION ALL\n    ".join(
        f"SELECT * FROM dbbardata_{y}" for y in years
    )
    dst.execute(f"CREATE VIEW dbbardata AS\n    {union_parts}")
    print("dbbardata 视图已创建")

    # 复制 dbbaroverview
    src.row_factory = None
    dst.execute("""
        CREATE TABLE dbbaroverview (
            id INTEGER NOT NULL PRIMARY KEY,
            symbol VARCHAR(255) NOT NULL,
            exchange VARCHAR(255) NOT NULL,
            interval VARCHAR(255) NOT NULL,
            count INTEGER NOT NULL,
            start DATETIME NOT NULL,
            end DATETIME NOT NULL
        )
    """)
    dst.execute("""
        CREATE UNIQUE INDEX idx_dbbaroverview_symbol
        ON dbbaroverview (symbol, exchange, interval)
    """)

    overview_rows = src.execute("SELECT * FROM dbbaroverview").fetchall()
    dst.executemany(
        "INSERT OR IGNORE INTO dbbaroverview VALUES (?,?,?,?,?,?,?)",
        overview_rows,
    )
    dst.commit()
    print(f"dbbaroverview: {len(overview_rows)} 行")

    # 如果有 dbtickdata，也复制（不分区）
    try:
        src.execute("SELECT COUNT(*) FROM dbtickdata")
    except:
        print("dbtickdata 不存在或无数据，跳过")
    else:
        dst.execute("""
            CREATE TABLE dbtickdata (
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
                bid_price_1 REAL NOT NULL,
                bid_price_2 REAL, bid_price_3 REAL, bid_price_4 REAL, bid_price_5 REAL,
                ask_price_1 REAL NOT NULL,
                ask_price_2 REAL, ask_price_3 REAL, ask_price_4 REAL, ask_price_5 REAL,
                bid_volume_1 REAL NOT NULL,
                bid_volume_2 REAL, bid_volume_3 REAL, bid_volume_4 REAL, bid_volume_5 REAL,
                ask_volume_1 REAL NOT NULL,
                ask_volume_2 REAL, ask_volume_3 REAL, ask_volume_4 REAL, ask_volume_5 REAL,
                localtime DATETIME
            )
        """)
        tick_rows = src.execute("SELECT * FROM dbtickdata").fetchall()
        dst.executemany(
            f"INSERT OR IGNORE INTO dbtickdata VALUES ({','.join('?'*36)})",
            tick_rows,
        )
        dst.commit()
        print(f"dbtickdata: {len(tick_rows)} 行")

    dst.close()
    src.close()

    # 统计新库大小
    new_size = os.path.getsize(DST_DB)
    print(f"\n新库大小: {new_size / (1024**3):.1f} GB")

    print("\n✅ 全部完成！新库: database_partitioned.db")
    print("   如需替换原库，请确认后执行:")
    print(f"   mv {DST_DB} {os.path.expanduser('~/.vntrader/database.db')}")


if __name__ == "__main__":
    main()
