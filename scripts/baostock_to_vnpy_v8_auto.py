"""
baostock -> vnpy SQLite 5分钟K线自动补缺脚本
用途：自动检测缺失、循环续跑直到全部完成，无需人工干预
特点：
  - 直接操作SQLite（不依赖vnpy安装）
  - INSERT OR IGNORE 避免重复
  - 每轮自动检测剩余、断点续传
  - 兼容进程被杀（SIGTERM/SIGKILL）场景
  - 失败重试：每轮末尾重试之前失败的股票
  - 连续空轮退出（无新进展则终止）
"""

import baostock as bs
import sqlite3
import sys
import time
import json
import os
import socket
import signal
from datetime import datetime

socket.setdefaulttimeout(30)

# ===================== 配置 =====================

# 数据库路径（按年份拆分，通过命令行年份动态设置）
DB_PATH = os.path.expanduser("~/.vntrader/database.db")  # 会被 main() 动态覆盖
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", ".cache", "stocks_akshare.json")

# 默认补缺日期区间（可通过命令行覆盖）
START_DATE = "2026-01-02"
END_DATE = "2026-06-02"
BS_FREQ = "5"

# 目标表（每个年份文件内统一为 dbbardata，不再是 dbbardata_YYYY）
TARGET_TABLE = "dbbardata"

# 进度文件名模板
PROGRESS_FILE_TEMPLATE = "/tmp/baostock_backfill_{start}_{end}_progress.json"

QUERY_DELAY = 0.3
RELOGIN_EVERY = 100
MAX_RETRIES = 3
RETRY_DELAY = 5
DB_BATCH_ROWS = 10000
CHECKPOINT_EVERY = 50
ID_OFFSET = 900000000

# 循环控制
MAX_ROUNDS = 50           # 最大循环轮数（防死循环）
MAX_EMPTY_ROUNDS = 3      # 连续多少轮无进展则退出
ROUND_COOLDOWN = 2        # 轮间冷却(秒)


# ===================== 工具函数 =====================

def baostock_to_vt_symbol(bs_symbol: str) -> tuple:
    prefix, code = bs_symbol.split(".")
    if prefix == "sh":
        return code, "SSE"
    elif prefix == "sz":
        return code, "SZSE"
    raise ValueError(f"不支持的前缀: {prefix}")


def get_max_id(conn, table: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()
    return row[0]


def load_existing_stocks(conn, start_date: str, end_date: str) -> set:
    start_dt = f"{start_date} 00:00:00"
    end_dt = f"{end_date} 23:59:59"
    rows = conn.execute(
        f"SELECT DISTINCT symbol FROM {TARGET_TABLE} "
        "WHERE interval='5m' AND datetime >= ? AND datetime <= ?",
        (start_dt, end_dt)
    ).fetchall()
    return {r[0] for r in rows}


def query_bars(bs_symbol: str, start: str, end: str) -> list:
    fields = "date,time,open,high,low,close,volume,amount"
    for attempt in range(MAX_RETRIES):
        try:
            rs = bs.query_history_k_data_plus(bs_symbol, fields, start, end, BS_FREQ)
            bars = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                try:
                    dt_str = row[1]
                    if not dt_str or len(dt_str) < 12:
                        continue
                    bars.append((
                        row[0], dt_str,
                        row[2], row[3], row[4], row[5],
                        row[6], row[7],
                    ))
                except Exception:
                    continue
            return bars
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            continue
    return []


def bs_login():
    for attempt in range(3):
        try:
            bs.login()
            return True
        except Exception:
            if attempt < 2:
                time.sleep(3)
    return False


def bs_logout():
    try:
        bs.logout()
    except Exception:
        pass


def load_progress() -> dict:
    pf = PROGRESS_FILE_TEMPLATE.format(start=START_DATE, end=END_DATE)
    if os.path.exists(pf):
        with open(pf) as f:
            return json.load(f)
    return {"done": [], "failed": []}


def save_progress(progress: dict):
    pf = PROGRESS_FILE_TEMPLATE.format(start=START_DATE, end=END_DATE)
    with open(pf, "w") as f:
        json.dump(progress, f, ensure_ascii=False)


# ===================== 单轮导入 =====================

def run_one_round(conn, to_import: list, current_id: int) -> tuple:
    """
    执行一轮导入。返回 (导入成功数, K线条数, 新current_id, done_set, failed_set)
    """
    total = len(to_import)
    if total == 0:
        return 0, 0, current_id, set(), set()

    total_bars = 0
    total_stocks_ok = 0
    done_set = set()
    failed_set = set()
    batch_rows = []
    start_time = time.time()

    try:
        if not bs_login():
            print("    [ERROR] baostock 登录失败", flush=True)
            return 0, 0, current_id, set(), set()

        for i, (bs_sym, vt_sym, ex) in enumerate(to_import):
            bars = query_bars(bs_sym, START_DATE, END_DATE)
            time.sleep(QUERY_DELAY)

            if bars:
                for bar in bars:
                    date_str, dt_str, o, h, l, c, vol, amt = bar
                    vnpy_dt = (f"{dt_str[0:4]}-{dt_str[4:6]}-{dt_str[6:8]} "
                               f"{dt_str[8:10]}:{dt_str[10:12]}:{dt_str[12:14]}")
                    batch_rows.append((
                        current_id, vt_sym, ex, vnpy_dt, "5m",
                        float(vol) if vol else 0,
                        float(amt) if amt else 0,
                        0.0,
                        float(o) if o else 0,
                        float(h) if h else 0,
                        float(l) if l else 0,
                        float(c) if c else 0,
                    ))
                    current_id += 1

                total_bars += len(bars)
                total_stocks_ok += 1
                done_set.add(bs_sym)
            else:
                failed_set.add(bs_sym)

            # 批量写入
            if len(batch_rows) >= DB_BATCH_ROWS or (i + 1) == total:
                if batch_rows:
                    try:
                        conn.executemany(
                            f"INSERT OR IGNORE INTO {TARGET_TABLE} VALUES "
                            "(?,?,?,?,?,?,?,?,?,?,?,?)",
                            batch_rows
                        )
                        conn.commit()
                    except Exception as e:
                        print(f"    [DB ERROR] {e}", flush=True)
                        conn.rollback()
                    batch_rows = []

            # 进度输出
            if (i + 1) % 20 == 0 or (i + 1) == total:
                elapsed = time.time() - start_time
                speed = (i + 1) / elapsed * 60 if elapsed > 0 else 0
                mark = "SSE" if bs_sym.startswith("sh.") else "SZSE"
                bc = len(bars) if bars else 0
                print(f"    [{i+1}/{total}] {mark} {bs_sym} "
                      f"+{bc}条  累计:{total_bars}条  "
                      f"{speed:.0f}只/min", flush=True)

            # 定期重登录
            if (i + 1) % RELOGIN_EVERY == 0:
                bs_logout()
                time.sleep(1)
                bs_login()

            # 定期保存断点
            if (i + 1) % CHECKPOINT_EVERY == 0:
                progress = load_progress()
                progress["done"] = sorted(set(progress["done"]) | done_set)
                progress["failed"] = sorted(
                    (set(progress["failed"]) | failed_set) - done_set
                )
                save_progress(progress)

        # 最终写入 + 断点
        if batch_rows:
            try:
                conn.executemany(
                    f"INSERT OR IGNORE INTO {TARGET_TABLE} VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?)",
                    batch_rows
                )
                conn.commit()
            except Exception as e:
                print(f"    [DB ERROR] {e}", flush=True)
                conn.rollback()

    finally:
        bs_logout()

    return total_stocks_ok, total_bars, current_id, done_set, failed_set


# ===================== 主流程（自动循环） =====================

def main():
    global START_DATE, END_DATE, TARGET_TABLE, DB_PATH

    # 命令行参数：python3 v8_auto.py [start_date] [end_date] [table]
    if len(sys.argv) >= 3:
        START_DATE = sys.argv[1]
        END_DATE = sys.argv[2]
    # 根据起始年份设置数据库路径和表名
    year = START_DATE[:4]
    DB_PATH = os.path.expanduser(f"~/.vntrader/database_{year}.db")
    TARGET_TABLE = "dbbardata"
    # 也可以手动指定第三个参数覆盖表名
    if len(sys.argv) >= 4:
        TARGET_TABLE = sys.argv[3]

    pf = PROGRESS_FILE_TEMPLATE.format(start=START_DATE, end=END_DATE)
    print("=" * 60, flush=True)
    print(f"  baostock {year}年5分钟K线自动补缺", flush=True)
    print("  自动循环模式：无需人工干预", flush=True)
    print("=" * 60, flush=True)
    print(f"  数据库: {DB_PATH}", flush=True)
    print(f"  目标表: {TARGET_TABLE}", flush=True)
    print(f"  补缺区间: {START_DATE} ~ {END_DATE}", flush=True)
    print(f"  K线频率: {BS_FREQ} 分钟", flush=True)
    print(f"  进度文件: {pf}", flush=True)
    print()

    # 加载股票池
    cache_path = os.path.abspath(CACHE_FILE)
    if not os.path.exists(cache_path):
        print(f"  错误: 股票池缓存不存在: {cache_path}", flush=True)
        sys.exit(1)
    with open(cache_path) as f:
        all_stocks = json.load(f)

    # 构建 bs_sym -> (vt_sym, exchange) 映射
    all_vt_symbols = {}
    for bs_sym in all_stocks:
        vt_sym, ex = baostock_to_vt_symbol(bs_sym)
        all_vt_symbols[bs_sym] = (vt_sym, ex)

    print(f"  股票池: {len(all_stocks)} 只", flush=True)
    print()

    # 自动循环
    global_start = time.time()
    global_bars = 0
    empty_rounds = 0
    prev_done_count = 0

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"\n--- Round {round_num} ---", flush=True)

        # 每轮重新连接数据库（防止连接断开）
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        # 检测已有数据
        existing = load_existing_stocks(conn, START_DATE, END_DATE)
        progress = load_progress()
        done_set = set(progress.get("done", []))
        failed_set = set(progress.get("failed", []))

        # 计算本轮待处理
        pending = []
        for bs_sym in sorted(all_vt_symbols.keys()):
            if bs_sym not in done_set:
                vt_sym, ex = all_vt_symbols[bs_sym]
                if vt_sym not in existing:
                    pending.append((bs_sym, vt_sym, ex))

        # 加上之前失败的（重试）
        retry_list = []
        for bs_sym in sorted(failed_set - done_set):
            if bs_sym in all_vt_symbols:
                vt_sym, ex = all_vt_symbols[bs_sym]
                retry_list.append((bs_sym, vt_sym, ex))

        to_import = pending + retry_list

        print(f"  已有数据: {len(existing)} 只 | "
              f"断点done: {len(done_set)} | "
              f"本轮待处理: {len(to_import)} 只", flush=True)

        if not to_import:
            conn.close()
            print(f"\n  全部完成！{START_DATE}~{END_DATE} 共 {len(existing)} 只股票有数据", flush=True)
            # 写完成标记，通知wrapper退出
            with open("/tmp/baostock_backfill_DONE", "w") as mf:
                mf.write("done")
            break

        # 执行本轮
        current_id = max(get_max_id(conn, TARGET_TABLE) + 1, ID_OFFSET)
        ok_count, bars_count, new_id, new_done, new_failed = \
            run_one_round(conn, to_import, current_id)

        # 更新全局断点
        progress["done"] = sorted(done_set | new_done)
        progress["failed"] = sorted((failed_set | new_failed) - new_done)
        save_progress(progress)

        global_bars += bars_count
        elapsed = time.time() - global_start

        print(f"  Round {round_num} 结果: +{ok_count}只, +{bars_count}条K线 | "
              f"累计done: {len(progress['done'])}只 | "
              f"总耗时: {elapsed/60:.1f}min", flush=True)

        conn.close()

        # 检测是否有进展
        cur_done_count = len(progress["done"])
        if cur_done_count == prev_done_count:
            empty_rounds += 1
            print(f"  连续 {empty_rounds} 轮无新进展", flush=True)
            if empty_rounds >= MAX_EMPTY_ROUNDS:
                print(f"\n  连续 {MAX_EMPTY_ROUNDS} 轮无进展，终止。", flush=True)
                break
        else:
            empty_rounds = 0
        prev_done_count = cur_done_count

        # 轮间冷却
        time.sleep(ROUND_COOLDOWN)

    # 最终汇总
    final_existing = 0
    final_conn = sqlite3.connect(DB_PATH, timeout=30)
    final_existing = len(load_existing_stocks(final_conn, START_DATE, END_DATE))
    final_conn.close()

    progress = load_progress()
    final_failed = set(progress.get("failed", [])) - set(progress.get("done", []))

    total_elapsed = time.time() - global_start
    print(f"\n{'='*60}", flush=True)
    print(f"  自动补缺结束", flush=True)
    print(f"  {'='*60}", flush=True)
    print(f"  补缺区间: {START_DATE} ~ {END_DATE}", flush=True)
    print(f"  数据库股票数: {final_existing} 只", flush=True)
    print(f"  累计导入K线: {global_bars:,} 条", flush=True)
    print(f"  断点done: {len(progress['done'])} 只", flush=True)
    print(f"  最终失败: {len(final_failed)} 只", flush=True)
    if final_failed:
        print(f"  失败列表: {sorted(final_failed)[:10]}", flush=True)
        if len(final_failed) > 10:
            print(f"             ...共 {len(final_failed)} 只", flush=True)
    print(f"  总耗时: {total_elapsed/60:.1f} 分钟", flush=True)
    print(f"  进度文件: {pf}", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
