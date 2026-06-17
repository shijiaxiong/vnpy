"""
baostock -> vnpy SQLite 2026年5分钟K线补缺脚本
用途：自动检测缺失股票，从baostock下载补齐
特点：直接操作SQLite（不依赖vnpy安装），INSERT OR IGNORE避免重复
"""

import baostock as bs
import sqlite3
import sys
import time
import json
import os
import socket
from datetime import datetime

socket.setdefaulttimeout(30)

# ===================== 配置 =====================

DB_PATH = os.path.expanduser("~/.vntrader/database.db")
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", ".cache", "stocks_akshare.json")
PROGRESS_FILE = "/tmp/baostock_backfill_2026_progress.json"

# 补缺日期区间（baostock能提供数据的最近范围）
START_DATE = "2026-05-12"
END_DATE = "2026-05-21"
BS_FREQ = "5"

QUERY_DELAY = 0.35        # 每只股票查询间隔(秒)
BATCH_SIZE = 50            # 每批写入的股票数
RELOGIN_EVERY = 50         # 每N只重登录
MAX_RETRIES = 3            # baostock查询重试
RETRY_DELAY = 2            # 查询重试间隔
DB_BATCH_ROWS = 10000      # 每批写入SQLite的行数
CHECKPOINT_EVERY = 50      # 每N只保存断点
ID_OFFSET = 900000000      # 起始ID（避免与已有ID冲突）


# ===================== 工具函数 =====================

def baostock_to_vt_symbol(bs_symbol: str) -> tuple:
    """baostock代码 -> (vnpy_symbol, exchange)"""
    prefix, code = bs_symbol.split(".")
    if prefix == "sh":
        return code, "SSE"
    elif prefix == "sz":
        return code, "SZSE"
    raise ValueError(f"不支持的前缀: {prefix}")


def get_max_id(conn, table: str) -> int:
    """获取表中最大ID"""
    row = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()
    return row[0]


def load_existing_stocks(conn, start_date: str, end_date: str) -> set:
    """获取指定日期范围内已有数据的股票集合"""
    start_dt = f"{start_date} 00:00:00"
    end_dt = f"{end_date} 23:59:59"
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM dbbardata_2026 "
        "WHERE interval='5m' AND datetime >= ? AND datetime <= ?",
        (start_dt, end_dt)
    ).fetchall()
    return {r[0] for r in rows}


def query_bars(bs_symbol: str, start: str, end: str) -> list:
    """从baostock查询5分钟K线"""
    fields = "date,time,open,high,low,close,volume,amount"
    for attempt in range(MAX_RETRIES):
        try:
            rs = bs.query_history_k_data_plus(bs_symbol, fields, start, end, BS_FREQ)
            bars = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                try:
                    # row: [date, datetime, open, high, low, close, volume, amount]
                    dt_str = row[1]  # yyyymmddHHMMSS
                    if not dt_str or len(dt_str) < 12:
                        continue
                    bars.append((
                        row[0],           # date (辅助)
                        dt_str,           # datetime
                        row[2], row[3], row[4], row[5],  # OHLC
                        row[6], row[7],   # volume, amount
                    ))
                except Exception:
                    continue
            return bars
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            continue
    return []


def login():
    for attempt in range(3):
        try:
            bs.login()
            return True
        except Exception:
            if attempt < 2:
                time.sleep(3)
    return False


def logout():
    try:
        bs.logout()
    except Exception:
        pass


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"done": [], "failed": []}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, ensure_ascii=False)


# ===================== 主流程 =====================

def main():
    print("=" * 60)
    print("  baostock 2026年5分钟K线补缺")
    print("=" * 60)
    print(f"  数据库: {DB_PATH}")
    print(f"  补缺区间: {START_DATE} ~ {END_DATE}")
    print(f"  K线频率: {BS_FREQ} 分钟")
    print()

    # Step1: 加载股票池
    cache_path = os.path.abspath(CACHE_FILE)
    if not os.path.exists(cache_path):
        print(f"  错误: 股票池缓存不存在: {cache_path}")
        sys.exit(1)
    with open(cache_path) as f:
        all_stocks = json.load(f)
    print(f"  股票池: {len(all_stocks)} 只")

    # Step2: 连接数据库，检测已有数据
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    existing = load_existing_stocks(conn, START_DATE, END_DATE)
    print(f"  {START_DATE}~{END_DATE} 已有数据: {len(existing)} 只")

    # Step3: 计算缺失股票
    # 将 baostock 格式(sh.600000) 转为 vnpy symbol(600000)
    all_vt_symbols = {}
    for bs_sym in all_stocks:
        vt_sym, ex = baostock_to_vt_symbol(bs_sym)
        all_vt_symbols[vt_sym] = (bs_sym, ex)

    missing_vt = set(all_vt_symbols.keys()) - existing
    print(f"  缺失股票: {len(missing_vt)} 只")

    if not missing_vt:
        print("\n  无需补缺，数据完整！")
        conn.close()
        return

    # 统计交易所分布
    szse_missing = sum(1 for s in missing_vt
                       if all_vt_symbols[s][1] == "SZSE")
    sse_missing = len(missing_vt) - szse_missing
    print(f"    SSE: {sse_missing} 只, SZSE: {szse_missing} 只")

    # Step4: 构建待处理列表（只补缺的）
    to_import = []
    for vt_sym in sorted(missing_vt):
        bs_sym, ex = all_vt_symbols[vt_sym]
        to_import.append((bs_sym, vt_sym, ex))

    # Step5: 加载断点，排除已完成的
    progress = load_progress()
    done_set = set(progress.get("done", []))
    failed_set = set(progress.get("failed", []))

    to_import = [(bs, vt, ex) for bs, vt, ex in to_import
                 if bs not in done_set]
    print(f"  排除已完成: 待处理 {len(to_import)} 只")
    print(f"  预估耗时: ~{len(to_import) * QUERY_DELAY / 60:.0f} 分钟")
    print()

    # Step6: 获取起始ID
    max_id = get_max_id(conn, "dbbardata_2026")
    current_id = max(max_id + 1, ID_OFFSET)
    print(f"  起始ID: {current_id} (表最大ID: {max_id})")
    print()

    # Step7: 开始导入
    total = len(to_import)
    total_bars = 0
    total_stocks_ok = 0
    new_done = set(done_set)
    new_failed = set(failed_set - done_set)  # 重试之前失败的
    batch_rows = []
    start_time = time.time()

    try:
        if not login():
            print("  错误: baostock 登录失败！")
            sys.exit(1)

        for i, (bs_sym, vt_sym, ex) in enumerate(to_import):
            bars = query_bars(bs_sym, START_DATE, END_DATE)
            time.sleep(QUERY_DELAY)

            if bars:
                for bar in bars:
                    date_str, dt_str, o, h, l, c, vol, amt = bar
                    # 构造vnpy格式datetime: yyyy-mm-dd HH:MM:SS
                    vnpy_dt = (f"{dt_str[0:4]}-{dt_str[4:6]}-{dt_str[6:8]} "
                               f"{dt_str[8:10]}:{dt_str[10:12]}:{dt_str[12:14]}")
                    batch_rows.append((
                        current_id, vt_sym, ex, vnpy_dt, "5m",
                        float(vol) if vol else 0,
                        float(amt) if amt else 0,
                        0.0,  # open_interest
                        float(o) if o else 0,
                        float(h) if h else 0,
                        float(l) if l else 0,
                        float(c) if c else 0,
                    ))
                    current_id += 1

                total_bars += len(bars)
                total_stocks_ok += 1
                new_done.add(bs_sym)
            else:
                new_failed.add(bs_sym)

            # 批量写入
            if len(batch_rows) >= DB_BATCH_ROWS or (i + 1) == total:
                if batch_rows:
                    try:
                        conn.executemany(
                            "INSERT OR IGNORE INTO dbbardata_2026 VALUES "
                            "(?,?,?,?,?,?,?,?,?,?,?,?)",
                            batch_rows
                        )
                        conn.commit()
                    except Exception as e:
                        print(f"  DB写入错误: {e}")
                        conn.rollback()
                    batch_rows = []

            # 进度输出
            if (i + 1) % 20 == 0 or (i + 1) == total:
                pct = (i + 1) / total * 100
                elapsed = time.time() - start_time
                speed = (i + 1) / elapsed * 60 if elapsed > 0 else 0
                mark = "SSE" if bs_sym.startswith("sh.") else "SZSE"
                bc = len(bars) if bars else 0
                eta = (total - i - 1) * QUERY_DELAY / 60
                print(f"  [{i+1}/{total}] ({pct:.0f}%) {mark} {bs_sym} "
                      f"+{bc}条  累计: {total_bars}条  "
                      f"{speed:.0f}只/min  ETA: {eta:.0f}min")

            # 定期重登录
            if (i + 1) % RELOGIN_EVERY == 0:
                logout()
                time.sleep(1)
                login()

            # 定期保存断点
            if (i + 1) % CHECKPOINT_EVERY == 0:
                save_progress({
                    "done": sorted(new_done),
                    "failed": sorted(new_failed)
                })

        # 最终写入
        if batch_rows:
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO dbbardata_2026 VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?)",
                    batch_rows
                )
                conn.commit()
            except Exception as e:
                print(f"  最终DB写入错误: {e}")
                conn.rollback()

        save_progress({
            "done": sorted(new_done),
            "failed": sorted(new_failed)
        })

    finally:
        logout()
        conn.close()

    elapsed = time.time() - start_time

    # 结果汇总
    print(f"\n{'='*60}")
    print(f"  补缺完成！")
    print(f"  {'='*60}")
    print(f"  耗时: {elapsed/60:.1f} 分钟")
    print(f"  缺失股票: {len(missing_vt)} 只")
    print(f"  本次导入: {total_stocks_ok} 只, {total_bars:,} 条K线")
    print(f"  失败: {len(new_failed)} 只")
    if new_failed:
        print(f"  失败样本: {sorted(new_failed)[:5]}")
    print(f"  数据库: {DB_PATH}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
