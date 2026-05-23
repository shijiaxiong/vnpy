#!/usr/bin/env python3
"""
从损坏的 SQLite 数据库 recover dbbardata 数据，
直接写入按年分表的新库，并创建兼容视图。
"""

import sqlite3
import os
import subprocess
import re
from datetime import datetime

SRC_DB = os.path.expanduser("~/.vntrader/database.db")
DST_DB = os.path.expanduser("~/.vntrader/database_partitioned.db")
INTERVAL = "5m"

YEARS = ["2023", "2024", "2025", "2026"]


def create_dst_schema(dst):
    """创建按年分表的目标库 schema"""
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=OFF")
    dst.execute("PRAGMA cache_size=-1000000")  # 1GB

    for y in YEARS:
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

    # dbbaroverview
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

    # dbtickdata (不分区，原样保留)
    dst.execute("""
        CREATE TABLE dbtickdata (
            id INTEGER NOT NULL PRIMARY KEY,
            symbol VARCHAR(255) NOT NULL,
            exchange VARCHAR(255) NOT NULL,
            datetime DATETIME NOT NULL,
            name VARCHAR(255) NOT NULL,
            volume REAL NOT NULL, turnover REAL NOT NULL,
            open_interest REAL NOT NULL,
            last_price REAL NOT NULL, last_volume REAL NOT NULL,
            limit_up REAL NOT NULL, limit_down REAL NOT NULL,
            open_price REAL NOT NULL, high_price REAL NOT NULL,
            low_price REAL NOT NULL, pre_close REAL NOT NULL,
            bid_price_1 REAL NOT NULL, bid_price_2 REAL, bid_price_3 REAL, bid_price_4 REAL, bid_price_5 REAL,
            ask_price_1 REAL NOT NULL, ask_price_2 REAL, ask_price_3 REAL, ask_price_4 REAL, ask_price_5 REAL,
            bid_volume_1 REAL NOT NULL, bid_volume_2 REAL, bid_volume_3 REAL, bid_volume_4 REAL, bid_volume_5 REAL,
            ask_volume_1 REAL NOT NULL, ask_volume_2 REAL, ask_volume_3 REAL, ask_volume_4 REAL, ask_volume_5 REAL,
            localtime DATETIME
        )
    """)

    dst.commit()


INSERT_RE = re.compile(
    r"INSERT OR IGNORE INTO '(\w+)'\("
    r"'id',\s*'symbol',\s*'exchange',\s*'datetime',\s*'interval',\s*"
    r"'volume',\s*'turnover',\s*'open_interest',\s*'open_price',\s*"
    r"'high_price',\s*'low_price',\s*'close_price'"
    r"\)\s*VALUES\s*\("
    r"(\d+),\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*"
    r"([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*"
    r"([\d.]+),\s*([\d.]+),\s*([\d.]+)"
    r"\)"
)

OVERVIEW_INSERT_RE = re.compile(
    r"INSERT OR IGNORE INTO 'dbbaroverview'\("
    r"'id',\s*'symbol',\s*'exchange',\s*'interval',\s*'count',\s*'start',\s*'end'"
    r"\)\s*VALUES"
)

TICKDATA_INSERT_RE = re.compile(
    r"INSERT OR IGNORE INTO 'dbtickdata'\("
)

CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+")
CREATE_INDEX_RE = re.compile(r"CREATE\s+UNIQUE\s+INDEX\s+")


def parse_insert_dbbardata(line):
    m = INSERT_RE.match(line)
    if not m:
        return None
    tbl, id_, symbol, exchange, dt, interval = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
    rest = m.groups()[6:]
    return (tbl, int(id_), symbol, exchange, dt, interval, *rest)


def get_year_from_datetime(dt_str):
    return dt_str[:4]


def main():
    # 删除旧目标库
    if os.path.exists(DST_DB):
        os.remove(DST_DB)
    for ext in ["-wal", "-shm"]:
        p = DST_DB + ext
        if os.path.exists(p):
            os.remove(p)

    dst = sqlite3.connect(DST_DB)
    create_dst_schema(dst)

    # 启动 .recover 进程
    proc = subprocess.Popen(
        ["sqlite3", SRC_DB, ".recover"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    counts = {y: 0 for y in YEARS}
    overview_count = 0
    tick_count = 0
    total = 0
    batch = {y: [] for y in YEARS}
    batch_size = 50000
    overview_batch = []

    # 当前正在处理的表
    current_table = None

    print("开始从损坏库恢复数据...")
    start_time = datetime.now()

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue

        # 检测 DDL 语句
        if line.startswith(".dbconfig") or line == "BEGIN;" or line.startswith("PRAGMA"):
            continue

        if CREATE_TABLE_RE.match(line):
            continue
        if CREATE_INDEX_RE.match(line):
            continue

        # 解析 dbbardata INSERT
        row = parse_insert_dbbardata(line)
        if row:
            tbl, id_, symbol, exchange, dt, interval, volume, turnover, oi, op, hp, lp, cp = row
            if interval != INTERVAL:
                continue

            year = get_year_from_datetime(dt)
            if year not in counts:
                print(f"  [跳过] 未知年份: {year} - {dt}")
                continue

            batch[year].append((id_, symbol, exchange, dt, interval,
                                float(volume), float(turnover), float(oi),
                                float(op), float(hp), float(lp), float(cp)))
            counts[year] += 1
            total += 1

            if len(batch[year]) >= batch_size:
                dst.executemany(
                    f"INSERT OR IGNORE INTO dbbardata_{year} VALUES ({','.join('?'*12)})",
                    batch[year],
                )
                dst.commit()
                batch[year] = []

            if total % 500000 == 0:
                elapsed = (datetime.now() - start_time).total_seconds()
                print(f"  已处理 {total:,} 行 ({elapsed:.0f}s)...", flush=True)
            continue

        # dbbaroverview - 直接收集到 batch
        if OVERVIEW_INSERT_RE.match(line):
            overview_batch.append(line)
            overview_count += 1
            if len(overview_batch) >= 10000:
                # 批量执行 overview INSERT（直接走 sql）
                for ol in overview_batch:
                    try:
                        dst.execute(ol)
                    except:
                        pass
                dst.commit()
                overview_batch = []
            continue

        # dbtickdata - 跳过（不关心 tick 数据）
        if TICKDATA_INSERT_RE.match(line):
            tick_count += 1
            continue

        # 其他：COMMIT 等，忽略
        if line == "COMMIT;":
            continue

    # 清空残留 batch
    for y in YEARS:
        if batch[y]:
            dst.executemany(
                f"INSERT OR IGNORE INTO dbbardata_{y} VALUES ({','.join('?'*12)})",
                batch[y],
            )
    if overview_batch:
        for ol in overview_batch:
            try:
                dst.execute(ol)
            except:
                pass

    dst.commit()

    proc.wait()
    stderr_output = proc.stderr.read()
    if stderr_output:
        print(f"\n⚠️  stderr 输出:\n{stderr_output[:500]}")

    elapsed = (datetime.now() - start_time).total_seconds()

    # 创建 dbbardata 视图
    union_parts = "\n    UNION ALL\n    ".join(
        f"SELECT * FROM dbbardata_{y}" for y in YEARS
    )
    dst.execute(f"CREATE VIEW dbbardata AS\n    {union_parts}")

    # 重建各年表统计信息
    for y in YEARS:
        dst.execute(f"ANALYZE dbbardata_{y}")

    dst.close()

    # 结果汇总
    print(f"\n{'='*50}")
    print(f"恢复完成！耗时 {elapsed:.0f} 秒")
    print(f"{'='*50}")
    for y in YEARS:
        print(f"  dbbardata_{y}: {counts[y]:,} 行")
    print(f"  dbbaroverview: {overview_count} 行")
    print(f"  dbtickdata (跳过): {tick_count} 行")
    print(f"  总计 dbbardata: {total:,} 行")

    new_size = os.path.getsize(DST_DB)
    print(f"\n  新库大小: {new_size / (1024**3):.1f} GB")
    print(f"\n  目标库: {DST_DB}")
    print(f"  替换命令: mv {DST_DB} {os.path.expanduser('~/.vntrader/database.db')}")


if __name__ == "__main__":
    main()
