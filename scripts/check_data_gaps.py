#!/usr/bin/env python3
"""
A股5分钟K线数据完整性检查工具（只读，不修改数据）

用法:
  python3 check_data_gaps.py 2026-01-01 2026-06-17     # 检查2026年
  python3 check_data_gaps.py 2024-01-01 2024-12-31     # 检查2024年
  python3 check_data_gaps.py all                       # 检查所有年份
"""
import sqlite3
import os
import sys
from datetime import datetime, timedelta

DB_DIR = os.path.expanduser("~/.vntrader")
TARGET_TABLE = "dbbardata"


def get_db_path(year: str) -> str:
    return os.path.join(DB_DIR, f"database_{year}.db")


def check_year(start_date: str, end_date: str):
    year = start_date[:4]
    db_path = get_db_path(year)

    if not os.path.exists(db_path):
        print(f"[{year}] 数据库文件不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print(f"\n{'='*60}")
    print(f"  数据完整性检查: {start_date} ~ {end_date}")
    print(f"  数据库: {db_path}")
    print(f"{'='*60}")

    # 1 总体统计
    total = conn.execute(
        f"SELECT COUNT(*) as c, COUNT(DISTINCT symbol) as s, "
        f"COUNT(DISTINCT DATE(datetime)) as d, "
        f"MIN(DATE(datetime)) as m1, MAX(DATE(datetime)) as m2 "
        f"FROM {TARGET_TABLE} WHERE interval='5m' "
        f"AND datetime>=? AND datetime<?",
        (f"{start_date} 00:00:00", f"{end_date} 23:59:59")
    ).fetchone()

    print(f"\n[1] 总体概况")
    print(f"    K线总量: {total['c']:>12,}")
    print(f"    股票数:   {total['s']:>12,}")
    print(f"    交易日:   {total['d']:>12}")

    if total['c'] == 0:
        print("    ⚠️  数据为空！")
        conn.close()
        return

    print(f"    首日:     {total['m1']}")
    print(f"    末日:     {total['m2']}")

    # 2 逐月覆盖
    print(f"\n[2] 逐月覆盖")
    print(f"    {'月份':<10} {'K线数':>12} {'股票数':>8} {'交易日':>6} {'状态'}")
    print(f"    {'-'*45}")

    months = conn.execute(
        f"SELECT strftime('%Y-%m', datetime) as m, COUNT(*) as c, "
        f"COUNT(DISTINCT symbol) as s, COUNT(DISTINCT DATE(datetime)) as d "
        f"FROM {TARGET_TABLE} WHERE interval='5m' "
        f"AND datetime>=? AND datetime<? "
        f"GROUP BY m ORDER BY m",
        (f"{start_date} 00:00:00", f"{end_date} 23:59:59")
    ).fetchall()

    # 找到最大股票数作为基准
    max_stocks = max(m['s'] for m in months) if months else 0
    issues = []

    for m in months:
        ratio = m['s'] / max_stocks * 100 if max_stocks > 0 else 0
        if ratio >= 99:
            status = "✅"
        elif ratio >= 95:
            status = "⚠️ "
        else:
            status = "❌"
        print(f"    {m['m']:<10} {m['c']:>12,} {m['s']:>8} {m['d']:>6} {status}")

        if ratio < 99:
            issues.append((m['m'], m['s'], max_stocks - m['s']))

    # 3 日均K线检查
    print(f"\n[3] 日内K线完整性（寻找每只股票 < 48 条的异常日）")
    abnormal = conn.execute(
        f"SELECT DATE(datetime) as d, symbol, COUNT(*) as cnt "
        f"FROM {TARGET_TABLE} WHERE interval='5m' "
        f"AND datetime>=? AND datetime<? "
        f"GROUP BY d, symbol HAVING cnt < 48 "
        f"ORDER BY cnt",
        (f"{start_date} 00:00:00", f"{end_date} 23:59:59")
    ).fetchall()

    if abnormal:
        print(f"    发现 {len(abnormal)} 条异常（退市/上市首日属正常现象）：")
        for row in abnormal:
            print(f"    {row['d']}  {row['symbol']}  {row['cnt']}条")
    else:
        print(f"    ✅ 无异常，所有个股日内 K 线完整")

    # 4 缺失股票检查（仅当有月份缺股时）
    if issues:
        print(f"\n[4] 缺失股票分析")
        for m, cnt, gap in issues:
            if gap <= 0:
                continue
            # 找出该月缺失的股票
            prev_month_stocks = conn.execute(
                f"SELECT DISTINCT symbol FROM {TARGET_TABLE} WHERE interval='5m' "
                f"AND datetime>=? AND datetime<?",
                (f"{m[:4]}-01-01 00:00:00", f"{m}-01 00:00:00")
            ).fetchall()
            prev_symbols = {r['symbol'] for r in prev_month_stocks}

            cur_symbols = {r[0] for r in conn.execute(
                f"SELECT DISTINCT symbol FROM {TARGET_TABLE} WHERE interval='5m' "
                f"AND datetime>=? AND datetime<?",
                (f"{m}-01 00:00:00", f"{m}-31 23:59:59")
            ).fetchall()}

            missing = prev_symbols - cur_symbols
            print(f"    {m}: 缺 {gap} 只股票")
            if missing and len(missing) <= 20:
                print(f"    缺失股票: {', '.join(sorted(missing)[:20])}")
            elif missing:
                print(f"    缺失股票(前20): {', '.join(sorted(missing)[:20])}")
                print(f"     ... 共 {len(missing)} 只")

    # 5 总结
    print(f"\n[5] 总结")
    if issues:
        print(f"    ⚠️  发现 {len(issues)} 个月份股票覆盖不完整：")
        for m, cnt, gap in issues:
            print(f"        {m}: 仅 {cnt} 只，缺 {gap} 只")
    else:
        print(f"    ✅ 所有月份股票覆盖完整")

    if abnormal:
        # 区分退市日和普通缺失
        delisting = [r for r in abnormal if r['cnt'] <= 20]
        normal_gap = [r for r in abnormal if r['cnt'] > 20]
        if delisting:
            print(f"    日内K线异常: {len(delisting)} 条疑似退市日 ({len(normal_gap)} 条其他)")
        else:
            print(f"    日内K线异常: {len(normal_gap)} 条")
    else:
        print(f"    ✅ 日内K线完整")

    conn.close()


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "all":
        for y in ["2024", "2025", "2026"]:
            check_year(f"{y}-01-01", f"{y}-12-31")
    elif len(sys.argv) >= 3:
        start = sys.argv[1]
        end = sys.argv[2]
        check_year(start, end)
    elif len(sys.argv) == 2:
        year = sys.argv[1]
        check_year(f"{year}-01-01", f"{year}-12-31")
    else:
        print(__doc__)
        print("示例:")
        print("  python3 check_data_gaps.py 2026-01-01 2026-06-17")
        print("  python3 check_data_gaps.py 2024")
        print("  python3 check_data_gaps.py all")


if __name__ == "__main__":
    main()
