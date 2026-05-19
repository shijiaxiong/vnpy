"""
baostock -> vnpy SQLite 补录2024年数据
用途：补充 2024-01-01 ~ 2024-12-31 的5分钟K线数据
"""

import baostock as bs
import sys
import time
import json
import os
import socket
from datetime import datetime

socket.setdefaulttimeout(30)

sys.path.insert(0, "/Users/zyb/go/src/github/vnpy")
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, Datetime
from vnpy.trader.database import get_database, DB_TZ


# ===================== 配置 =====================

START_DATE = "2024-01-01"    # 补录起点
END_DATE = "2024-12-31"       # 截止
BS_FREQ = "5"
VT_INTERVAL = Interval.MINUTE5
QUERY_DELAY = 0.5             # 官方推荐：>= 500ms
BATCH_SIZE = 50              # 每批提交数
RELOGIN_EVERY = 50           # 每 N 只重登录一次，防止会话级断流
MAX_RETRIES = 3               # 单只股票最大重试
RETRY_DELAY = 2               # 重试前等待（秒）
CACHE_FILE = "/Users/zyb/go/src/github/vnpy/.cache/stocks_akshare.json"
PROGRESS_FILE = "/tmp/baostock_progress_2024.json"
LOG_FILE = "/tmp/baostock_v4.log"


# ===================== 工具函数 =====================

def baostock_to_vt_symbol(bs_symbol: str) -> tuple[str, Exchange]:
    prefix, code = bs_symbol.split(".")
    if prefix == "sh":
        return code, Exchange.SSE
    elif prefix == "sz":
        return code, Exchange.SZSE
    raise ValueError(f"不支持的前缀: {prefix}")


def parse_bs_datetime(time_str: str) -> Datetime:
    year = int(time_str[0:4]); month = int(time_str[4:6]); day = int(time_str[6:8])
    hour = int(time_str[8:10]); minute = int(time_str[10:12]); second = int(time_str[12:14])
    return Datetime(year, month, day, hour, minute, second, 0, DB_TZ)


def query_bars(bs_symbol: str, start: str, end: str) -> list[BarData]:
    """下载单只股票，带重试"""
    fields = "date,time,open,high,low,close,volume,amount"
    for attempt in range(MAX_RETRIES):
        try:
            rs = bs.query_history_k_data_plus(bs_symbol, fields, start, end, BS_FREQ)
            bars = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                try:
                    sym, ex = baostock_to_vt_symbol(bs_symbol)
                    bars.append(BarData(
                        gateway_name="",
                        symbol=sym, exchange=ex,
                        datetime=parse_bs_datetime(row[1]),
                        interval=VT_INTERVAL,
                        volume=float(row[6]) if row[6] else 0,
                        turnover=float(row[7]) if row[7] else 0,
                        open_price=float(row[2]) if row[2] else 0,
                        high_price=float(row[3]) if row[3] else 0,
                        low_price=float(row[4]) if row[4] else 0,
                        close_price=float(row[5]) if row[5] else 0,
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
    """带重试的登录"""
    for attempt in range(3):
        try:
            bs.login()
            return True
        except Exception:
            if attempt < 2:
                time.sleep(3)
    return False


def logout():
    """带忽略错误的登出"""
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
    print(f"=== baostock 补录2025年数据 -> vnpy SQLite ===")
    print(f"  数据区间: {START_DATE} ~ {END_DATE}")
    print(f"  K线频率: {BS_FREQ} 分钟")
    print(f"  查询间隔: {QUERY_DELAY}s, 每{RELOGIN_EVERY}只重登录")
    print()

    # Step1: 加载股票池
    with open(CACHE_FILE) as f:
        all_stocks = json.load(f)

    # Step2: 加载断点
    progress = load_progress()
    done_set = set(progress["done"])
    failed_set = set(progress["failed"])

    # 优先SSE（上次全部失败），再SZ，跳过已完成
    sh_stocks = [s for s in all_stocks if s.startswith("sh.") and s not in done_set]
    sz_stocks = [s for s in all_stocks if s.startswith("sz.") and s not in done_set]
    to_import = sh_stocks + sz_stocks

    print(f"  全部股票: {len(all_stocks)} (SSE {len([s for s in all_stocks if s.startswith('sh.')])} + SZ {len([s for s in all_stocks if s.startswith('sz.')])})")
    print(f"  已完成: {len(done_set)} 只")
    print(f"  失败(跳过): {len(failed_set)} 只")
    print(f"  待处理: {len(to_import)} 只")
    if sh_stocks:
        print(f"  SSE优先: {len(sh_stocks)} 只待导入")
    print(f"  预估耗时: ~{len(to_import) * QUERY_DELAY / 60:.0f} 分钟")
    print()

    # Step3: 初始化
    db = get_database()
    total_bars = 0
    total = len(to_import)
    new_done = set(progress["done"])
    new_failed = set(progress["failed"])

    try:
        login()
        batch_bars = []
        consecutive_fail = 0

        for i, bs_sym in enumerate(to_import):
            bars = query_bars(bs_sym, START_DATE, END_DATE)
            time.sleep(QUERY_DELAY)

            if bars:
                batch_bars.extend(bars)
                total_bars += len(bars)
                new_done.add(bs_sym)
                consecutive_fail = 0
            else:
                new_failed.add(bs_sym)
                consecutive_fail += 1

            # 每 BATCH_SIZE 提交一次
            if len(batch_bars) >= BATCH_SIZE or (i + 1) == total:
                if batch_bars:
                    db.save_bar_data(batch_bars)
                    batch_bars = []

            # 每20只打印进度
            if (i + 1) % 20 == 0 or (i + 1) == total:
                pct = (i + 1) / total * 100
                mark = "SSE" if bs_sym.startswith("sh.") else "SZ "
                print(f"  [{i+1}/{total}] ({pct:.0f}%) {mark} {bs_sym} "
                      f"+{len(bars) if bars else 0}条  累计: {total_bars}条")

            # 每 RELOGIN_EVERY 只重登录一次
            if (i + 1) % RELOGIN_EVERY == 0:
                logout()
                time.sleep(1)
                login()
                print(f"  --- 重登录完成 ---")

            # 每50只保存断点
            if (i + 1) % 50 == 0:
                save_progress({
                    "done": sorted(new_done),
                    "failed": sorted(new_failed)
                })

        # 剩余提交
        if batch_bars:
            db.save_bar_data(batch_bars)

        save_progress({
            "done": sorted(new_done),
            "failed": sorted(new_failed)
        })

    finally:
        logout()

    print(f"\n{'='*50}")
    print(f"  全部股票: {len(all_stocks)}")
    print(f"  本次导入: {total_bars} 条 ({total - len([s for s in to_import if s in new_failed])} 只)")
    print(f"  累计完成: {len(new_done)} 只")
    print(f"  累计失败: {len(new_failed)} 只")
    if new_failed:
        print(f"  失败样本: {sorted(new_failed)[:5]}")
    print(f"  数据库: ~/.vntrader/database.db")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
