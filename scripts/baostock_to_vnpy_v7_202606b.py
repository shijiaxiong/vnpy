"""
baostock -> vnpy SQLite 2026年6月增量补录（第二批）
用途：补录 2026-06-16 至 2026-06-23 的5分钟K线数据
数据库：~/.vntrader/database_2026.db
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

START_DATE = "2026-06-16"
END_DATE = "2026-06-23"
BS_FREQ = "5"
VT_INTERVAL = Interval.MINUTE5
QUERY_DELAY = 0.5
BATCH_SIZE = 50
RELOGIN_EVERY = 50
MAX_RETRIES = 3
RETRY_DELAY = 2
DB_RETRY_MAX = 5
DB_RETRY_DELAY = 3
CACHE_FILE = "/Users/zyb/go/src/github/vnpy/.cache/stocks_akshare.json"
PROGRESS_FILE = "/tmp/baostock_progress_2026_202606b.json"
LOG_FILE = "/tmp/baostock_v7_202606b.log"


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


def save_bars_with_retry(db, bars: list) -> bool:
    for attempt in range(DB_RETRY_MAX):
        try:
            db.save_bar_data(bars)
            return True
        except Exception as e:
            err = str(e)
            if "locked" in err.lower() or "OperationalError" in str(type(e).__name__):
                wait = DB_RETRY_DELAY * (2 ** attempt)
                print(f"  DB锁冲突，{wait}s后重试({attempt+1}/{DB_RETRY_MAX})...")
                time.sleep(wait)
            else:
                raise
    print(f"  DB写入失败(已重试{DB_RETRY_MAX}次)，丢弃{len(bars)}条")
    return False


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"done": [], "failed": []}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, ensure_ascii=False)


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ===================== 主流程 =====================

def main():
    log(f"=== baostock 2026年6月补录(第二批) -> vnpy SQLite ===")
    log(f"  数据区间: {START_DATE} ~ {END_DATE}  (5个交易日: 06-16~06-23)")
    log(f"  K线频率: {BS_FREQ} 分钟")
    log(f"  查询间隔: {QUERY_DELAY}s, 每{RELOGIN_EVERY}只重登录")
    log(f"  断点文件: {PROGRESS_FILE}")
    print()

    # Step1: 加载股票池
    with open(CACHE_FILE) as f:
        all_stocks = json.load(f)

    # Step2: 加载断点
    progress = load_progress()
    done_set = set(progress.get("done", []))
    failed_set = set(progress.get("failed", []))

    to_import = sorted(set(all_stocks) - done_set)
    # 把之前失败的也加回来重试
    to_import = sorted(list(set(to_import) | (failed_set - done_set)))

    log(f"  全部股票: {len(all_stocks)}")
    log(f"  已完成: {len(done_set)} 只（断点恢复）")
    log(f"  待处理: {len(to_import)} 只")
    log(f"  预估耗时: ~{len(to_import) * QUERY_DELAY / 60:.0f} 分钟（仅API时间）")
    print()

    # Step3: 初始化
    db = get_database()
    total_bars = 0
    total = len(to_import)
    new_done = set(done_set)
    new_failed = set()
    start_time = time.time()

    try:
        login()
        batch_bars = []

        for i, bs_sym in enumerate(to_import):
            bars = query_bars(bs_sym, START_DATE, END_DATE)
            time.sleep(QUERY_DELAY)

            if bars:
                batch_bars.extend(bars)
                total_bars += len(bars)
                new_done.add(bs_sym)
            else:
                new_failed.add(bs_sym)

            # 每 BATCH_SIZE 提交一次
            if len(batch_bars) >= BATCH_SIZE or (i + 1) == total:
                if batch_bars:
                    ok = save_bars_with_retry(db, batch_bars)
                    if not ok:
                        for sym in to_import[max(0, i-BATCH_SIZE+1):i+1]:
                            if sym in new_done:
                                new_done.discard(sym)
                                new_failed.add(sym)
                    batch_bars = []

            # 每20只打印进度
            if (i + 1) % 20 == 0 or (i + 1) == total:
                elapsed = time.time() - start_time
                pct = (i + 1) / total * 100
                remaining = elapsed / (i + 1) * (total - i - 1)
                mark = "SSE" if bs_sym.startswith("sh.") else "SZ "
                bc = len(bars) if bars else 0
                log(f"  [{i+1}/{total}] ({pct:.0f}%) {mark} {bs_sym} "
                    f"+{bc}条  累计: {total_bars}条  剩余: ~{remaining/60:.0f}分")

            # 每 RELOGIN_EVERY 只重登录
            if (i + 1) % RELOGIN_EVERY == 0:
                logout()
                time.sleep(1)
                login()
                log(f"  --- 重登录完成 ---")

            # 每50只保存断点
            if (i + 1) % 50 == 0:
                save_progress({
                    "done": sorted(new_done),
                    "failed": sorted(new_failed)
                })

        # 最终保存断点
        save_progress({
            "done": sorted(new_done),
            "failed": sorted(new_failed)
        })

    finally:
        logout()

    elapsed_total = time.time() - start_time
    print()
    log(f"{'='*50}")
    log(f"  全部股票: {len(all_stocks)}")
    log(f"  本次完成: {len(new_done - done_set)} 只")
    log(f"  累计完成: {len(new_done)} 只")
    log(f"  本次失败: {len(new_failed)} 只")
    log(f"  导入K线: {total_bars} 条")
    log(f"  耗时: {elapsed_total/60:.1f} 分钟")
    if new_failed:
        log(f"  失败样本: {sorted(new_failed)[:5]}")
    log(f"{'='*50}")


if __name__ == "__main__":
    main()
