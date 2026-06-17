"""
每日自动补缺脚本 - 每天中午13点执行
1. 补齐前一天的5分钟K线
2. 检查最近15个交易日数据缺失并补齐
3. 汇总结果发送到飞书

运行方式：
  python3 daily_backfill_5m.py
  
飞书 Webhook 配置（二选一）：
  export FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
  或在脚本同级目录创建 .env 文件写入：
  FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
"""

import baostock as bs
import sqlite3
import sys
import time
import json
import os
import socket
import urllib.request
from datetime import datetime, timedelta

socket.setdefaulttimeout(30)

# ===================== 配置 =====================

DB_PATH = os.path.expanduser("~/.vntrader/database.db")
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", ".cache", "stocks_akshare.json")

BS_FREQ = "5"
QUERY_DELAY = 0.35
RELOGIN_EVERY = 50
MAX_RETRIES = 3
RETRY_DELAY = 2
DB_BATCH_ROWS = 10000
ID_OFFSET = 900000000

# 最近N个自然日用于检查缺失（覆盖约15个交易日）
CHECK_DAYS = 22


def load_feishu_webhook() -> str:
    """从环境变量或 .env 文件读取 webhook URL"""
    url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if url:
        return url
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("FEISHU_WEBHOOK_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ===================== 工具函数 =====================

def baostock_to_vt_symbol(bs_symbol: str) -> tuple:
    prefix, code = bs_symbol.split(".")
    if prefix == "sh":
        return code, "SSE"
    elif prefix == "sz":
        return code, "SZSE"
    raise ValueError(f"不支持的前缀: {prefix}")


def get_trade_dates_in_range(start_date: str, end_date: str) -> list:
    """通过 baostock 获取区间内的交易日列表"""
    rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
    dates = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        if row[1] == "1":  # is_trading_day
            dates.append(row[0])
    return dates


def get_max_id(conn, table: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()
    return row[0]


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
                    bars.append((row[0], dt_str, row[2], row[3], row[4], row[5], row[6], row[7]))
                except Exception:
                    continue
            return bars
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
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


def insert_bars(conn, table: str, bars: list, bs_symbol: str, current_id: int) -> tuple:
    """将 bars 插入数据库，返回 (插入条数, new_current_id)"""
    vt_sym, ex = baostock_to_vt_symbol(bs_symbol)
    batch_rows = []
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

    if batch_rows:
        conn.executemany(
            f"INSERT OR IGNORE INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            batch_rows
        )
        conn.commit()

    return len(batch_rows), current_id


def get_symbols_with_data(conn, table: str, start_date: str, end_date: str) -> set:
    """获取在指定日期范围内有数据的 symbol 集合"""
    rows = conn.execute(
        f"SELECT DISTINCT symbol FROM {table} "
        "WHERE interval='5m' AND datetime >= ? AND datetime <= ?",
        (f"{start_date} 00:00:00", f"{end_date} 23:59:59")
    ).fetchall()
    return {r[0] for r in rows}


def send_feishu(webhook_url: str, text: str):
    """发送飞书机器人消息"""
    if not webhook_url:
        print("  [警告] 未配置 FEISHU_WEBHOOK_URL，跳过飞书通知", flush=True)
        return
    payload = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("code", 0) != 0:
                print(f"  [飞书] 发送失败: {result}", flush=True)
            else:
                print("  [飞书] 消息发送成功", flush=True)
    except Exception as e:
        print(f"  [飞书] 发送异常: {e}", flush=True)


# ===================== 主流程 =====================

def main():
    start_ts = time.time()
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    check_start = today - timedelta(days=CHECK_DAYS)

    feishu_url = load_feishu_webhook()

    print("=" * 60, flush=True)
    print(f"  每日5分钟K线补缺  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print("=" * 60, flush=True)

    # 加载股票池
    cache_path = os.path.abspath(CACHE_FILE)
    if not os.path.exists(cache_path):
        print(f"  错误: 股票池缓存不存在: {cache_path}", flush=True)
        sys.exit(1)
    with open(cache_path) as f:
        all_stocks = json.load(f)

    all_vt_symbols = {}
    for bs_sym in all_stocks:
        vt_sym, ex = baostock_to_vt_symbol(bs_sym)
        all_vt_symbols[bs_sym] = (vt_sym, ex)

    total_stocks = len(all_stocks)
    print(f"  股票池: {total_stocks} 只", flush=True)

    # baostock 登录，获取交易日
    if not bs_login():
        print("  [ERROR] baostock 登录失败", flush=True)
        sys.exit(1)

    # 获取最近15个交易日（含昨天）
    trade_dates = get_trade_dates_in_range(
        check_start.strftime("%Y-%m-%d"),
        yesterday.strftime("%Y-%m-%d")
    )
    trade_dates = sorted(trade_dates)[-15:]  # 取最近15个交易日

    bs_logout()

    if not trade_dates:
        print("  未找到交易日，退出", flush=True)
        sys.exit(0)

    yesterday_str = yesterday.strftime("%Y-%m-%d")
    print(f"  前一天: {yesterday_str}", flush=True)
    print(f"  检查区间: {trade_dates[0]} ~ {trade_dates[-1]} ({len(trade_dates)}个交易日)", flush=True)

    # 连接数据库
    year = trade_dates[0][:4]
    target_table = f"dbbardata_{year}"
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current_id = max(get_max_id(conn, target_table) + 1, ID_OFFSET)

    # ---- 阶段1: 检测哪些股票在哪些交易日缺失 ----
    print(f"\n[阶段1] 检测最近{len(trade_dates)}个交易日的缺失情况...", flush=True)

    # 按 (date) 统计每只股票有数据的日期
    rows = conn.execute(
        f"SELECT symbol, DATE(datetime) as d FROM {target_table} "
        "WHERE interval='5m' AND datetime >= ? AND datetime <= ? "
        "GROUP BY symbol, d",
        (f"{trade_dates[0]} 00:00:00", f"{trade_dates[-1]} 23:59:59")
    ).fetchall()

    # symbol -> set of dates with data
    symbol_dates: dict = {}
    for sym, d in rows:
        symbol_dates.setdefault(sym, set()).add(d)

    # 找出每个交易日缺失的 bs_symbol 列表
    # 缺失定义：该 symbol 在某交易日完全没有数据
    # 为了效率，先找出昨天缺失的，再找历史缺失的
    yesterday_missing = []
    history_missing_dates: dict = {}  # date -> [bs_syms]

    for bs_sym, (vt_sym, ex) in all_vt_symbols.items():
        for d in trade_dates:
            has_data = vt_sym in symbol_dates and d in symbol_dates[vt_sym]
            if not has_data:
                if d == yesterday_str:
                    yesterday_missing.append(bs_sym)
                else:
                    history_missing_dates.setdefault(d, []).append(bs_sym)

    total_yesterday_missing = len(yesterday_missing)
    total_history_missing = sum(len(v) for v in history_missing_dates.values())

    print(f"  昨天({yesterday_str})缺失: {total_yesterday_missing} 只", flush=True)
    print(f"  历史缺失(按日统计): {total_history_missing} 只次，涉及 {len(history_missing_dates)} 个交易日", flush=True)

    # ---- 阶段2: 补缺 ----
    yesterday_ok = 0
    yesterday_bars = 0
    yesterday_failed = []
    history_ok = 0
    history_bars = 0
    history_failed = []

    if not bs_login():
        print("  [ERROR] baostock 登录失败", flush=True)
        conn.close()
        sys.exit(1)

    login_counter = 0

    def maybe_relogin():
        nonlocal login_counter
        login_counter += 1
        if login_counter % RELOGIN_EVERY == 0:
            bs_logout()
            time.sleep(1)
            bs_login()

    # 补昨天
    if yesterday_missing:
        print(f"\n[阶段2a] 补昨天 {yesterday_str} 缺失 {len(yesterday_missing)} 只...", flush=True)
        for i, bs_sym in enumerate(sorted(yesterday_missing)):
            bars = query_bars(bs_sym, yesterday_str, yesterday_str)
            time.sleep(QUERY_DELAY)
            if bars:
                inserted, current_id = insert_bars(conn, target_table, bars, bs_sym, current_id)
                yesterday_ok += 1
                yesterday_bars += inserted
            else:
                yesterday_failed.append(bs_sym)
            maybe_relogin()
            if (i + 1) % 100 == 0 or (i + 1) == len(yesterday_missing):
                print(f"  [{i+1}/{len(yesterday_missing)}] 已处理, 成功:{yesterday_ok}, K线:{yesterday_bars}", flush=True)
    else:
        print(f"\n[阶段2a] 昨天 {yesterday_str} 无缺失，跳过", flush=True)

    # 补历史缺失（按日期从新到旧）
    if history_missing_dates:
        all_history_missing_count = sum(len(v) for v in history_missing_dates.values())
        print(f"\n[阶段2b] 补历史缺失 {all_history_missing_count} 只次...", flush=True)
        processed = 0
        for d in sorted(history_missing_dates.keys(), reverse=True):
            syms = history_missing_dates[d]
            for bs_sym in sorted(syms):
                bars = query_bars(bs_sym, d, d)
                time.sleep(QUERY_DELAY)
                if bars:
                    inserted, current_id = insert_bars(conn, target_table, bars, bs_sym, current_id)
                    history_ok += 1
                    history_bars += inserted
                else:
                    history_failed.append(f"{bs_sym}@{d}")
                maybe_relogin()
                processed += 1
                if processed % 200 == 0:
                    print(f"  [{processed}/{all_history_missing_count}] 历史补缺进度, 成功:{history_ok}", flush=True)
    else:
        print("\n[阶段2b] 历史无缺失，跳过", flush=True)

    bs_logout()
    conn.close()

    elapsed = time.time() - start_ts

    # ---- 汇总报告 ----
    report_lines = [
        f"📊 每日K线补缺报告 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"股票池: {total_stocks} 只",
        f"检查区间: {trade_dates[0]} ~ {trade_dates[-1]} (最近{len(trade_dates)}个交易日)",
        f"",
        f"▶ 昨日({yesterday_str})补缺",
        f"  缺失: {total_yesterday_missing} 只",
        f"  已补: {yesterday_ok} 只 / {yesterday_bars:,} 条K线",
        f"  失败: {len(yesterday_failed)} 只",
        f"",
        f"▶ 历史缺失补缺",
        f"  缺失: {total_history_missing} 只次",
        f"  已补: {history_ok} 只次 / {history_bars:,} 条K线",
        f"  失败: {len(history_failed)} 只次",
        f"",
        f"⏱ 总耗时: {elapsed/60:.1f} 分钟",
    ]

    if yesterday_failed:
        report_lines.append(f"昨日失败: {', '.join(sorted(yesterday_failed)[:10])}" +
                            (f"...共{len(yesterday_failed)}只" if len(yesterday_failed) > 10 else ""))
    if history_failed:
        report_lines.append(f"历史失败: {', '.join(sorted(history_failed)[:5])}" +
                            (f"...共{len(history_failed)}只次" if len(history_failed) > 5 else ""))

    report = "\n".join(report_lines)
    print(f"\n{report}", flush=True)

    send_feishu(feishu_url, report)


if __name__ == "__main__":
    main()
