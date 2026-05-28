#!/usr/bin/env python3
"""
市场情绪引擎
==============
从涨停数据计算：连板梯队、砸盘系数、赚钱效应、题材强度

依赖:
  - zt_daily 表（涨停板数据）
  - concept_meta / concept_stock 表（题材映射）

用法:
  python scripts/mood_engine.py                     # 计算今天
  python scripts/mood_engine.py --date 20260527     # 指定日期
  python scripts/mood_engine.py --start 20260520    # 日期范围
  python scripts/mood_engine.py --top 15            # 题材强度 Top N
  python scripts/mood_engine.py --no-db             # 只输出不写库

输出表:
  - market_mood_daily: 每日情绪指标快照
  - concept_strength_daily: 每日题材强度排行
"""

import json
import math
import sqlite3
import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

# ===== 路径 =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, ".workbuddy", "market_mood.db")

# ===== DDL =====
DDL = """
CREATE TABLE IF NOT EXISTS market_mood_daily (
    trade_date   TEXT PRIMARY KEY,
    zt_total     INTEGER,
    zt_ge2_count INTEGER,
    max_board    INTEGER,
    board_dist   TEXT,
    smash_coef   REAL,
    cum_rate     REAL,
    bomb_rate    REAL,
    profit_effect INTEGER,
    top_themes   TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS concept_strength_daily (
    trade_date   TEXT,
    concept_code TEXT,
    concept_name TEXT,
    zt_count     INTEGER,
    max_board    INTEGER,
    rank         INTEGER,
    PRIMARY KEY (trade_date, concept_code, rank)
);

CREATE INDEX IF NOT EXISTS idx_csd_date ON concept_strength_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_csd_concept ON concept_strength_daily(concept_code);
"""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    conn.commit()
    return conn


def load_zt_data(conn, dates):
    """加载涨停数据"""
    if len(dates) == 1:
        rows = conn.execute(
            "SELECT trade_date, stock_code, stock_name, board_count, bomb_count, industry "
            "FROM zt_daily WHERE trade_date = ?",
            (dates[0],)
        ).fetchall()
    else:
        placeholders = ",".join(["?" for _ in dates])
        rows = conn.execute(
            f"SELECT trade_date, stock_code, stock_name, board_count, bomb_count, industry "
            f"FROM zt_daily WHERE trade_date IN ({placeholders}) ORDER BY trade_date",
            dates
        ).fetchall()

    # 按日期分组
    data = defaultdict(list)
    for row in rows:
        dt, code, name, bc, bomb, ind = row
        data[dt].append({
            "stock_code": code,
            "stock_name": name,
            "board_count": bc or 0,
            "bomb_count": bomb or 0,
            "industry": ind or "",
        })
    return data


def load_concept_map(conn):
    """加载题材-个股映射 {stock_code: [concept_codes]}"""
    rows = conn.execute(
        "SELECT cs.stock_code, cs.concept_code, cm.concept_name "
        "FROM concept_stock cs "
        "JOIN concept_meta cm ON cs.concept_code = cm.concept_code"
    ).fetchall()

    code_to_concepts = defaultdict(list)
    code_to_name = {}
    for sc, cc, cn in rows:
        code_to_concepts[sc].append(cc)
        code_to_name[cc] = cn
    return code_to_concepts, code_to_name


def compute_board_dist(stocks):
    """连板梯队统计 (≥2板)"""
    dist = defaultdict(int)
    max_b = 0
    total = 0
    for s in stocks:
        bc = s["board_count"]
        total += 1
        if bc >= 2:
            dist[bc] += 1
            if bc > max_b:
                max_b = bc

    return dict(dist), max_b, total


def compute_board_advancement(prev_dist, curr_max):
    """
    计算晋级率: N板→N+1板的晋级率
    累计晋级率 = Σ 所有级别的晋级率(百分比)
    砸盘系数 = ceil(累计晋级率 / 40, 1位小数)
    """
    rates = []
    details = {}

    for board in range(2, curr_max):
        prev_count = prev_dist.get(board, 0)
        next_count = prev_dist.get(board + 1, 0)  # 注意：用的是同一天 prev，不是第二天！

        if prev_count > 0:
            rate = round(next_count / prev_count * 100, 1)
            rates.append(rate)
            details[f"{board}→{board+1}"] = {"prev": prev_count, "next": next_count, "rate": rate}

    cum_rate = round(sum(rates), 1) if rates else 0.0
    raw = cum_rate / 4 * 10 / 100
    smash = math.ceil(raw * 10) / 10

    return cum_rate, smash, details


def compute_bomb_rate(stocks):
    """炸板率"""
    bomb_count = sum(1 for s in stocks if s.get("bomb_count", 0) > 0)
    return round(bomb_count / len(stocks) * 100, 1) if stocks else 0.0


def compute_concept_strength(stocks, code_to_concepts, code_to_name, top_n=20):
    """
    题材强度: 涨停个股 → 查题材 → 按题材聚合计数
    """
    concept_counts = defaultdict(lambda: {"zt_count": 0, "max_board": 0, "board_sum": 0})

    for s in stocks:
        code = s["stock_code"]
        bc = s["board_count"]

        concepts = code_to_concepts.get(code, [])
        # 如果没有题材映射，用行业作为 fallback
        if not concepts and s.get("industry"):
            concepts = [s["industry"]]

        for cc in concepts:
            concept_counts[cc]["zt_count"] += 1
            if bc > concept_counts[cc]["max_board"]:
                concept_counts[cc]["max_board"] = bc
            if bc >= 2:
                concept_counts[cc]["board_sum"] += bc

    # 按涨停数排序（二级：最高板）
    sorted_concepts = sorted(
        concept_counts.items(),
        key=lambda x: (x[1]["zt_count"], x[1]["max_board"]),
        reverse=True
    )[:top_n]

    result = []
    for rank, (cc, info) in enumerate(sorted_concepts, 1):
        name = code_to_name.get(cc, cc)
        result.append({
            "concept_code": cc,
            "concept_name": name,
            "zt_count": info["zt_count"],
            "max_board": info["max_board"],
            "board_sum": info["board_sum"],
            "rank": rank,
        })

    return result


def compute_single_day(conn, date_str, data, code_to_concepts, code_to_name, top_n=20):
    """计算单日情绪指标"""
    stocks = data.get(date_str, [])

    if not stocks:
        print(f"  ⚠️ {date_str}: 无涨停数据")
        return None

    # 连板梯队
    board_dist, max_board, zt_total = compute_board_dist(stocks)
    zt_ge2 = sum(v for k, v in board_dist.items() if k >= 2)

    # 晋级率 & 砸盘系数 (基于当天内部的梯队分布)
    cum_rate, smash, adv_details = compute_board_advancement(board_dist, max_board)

    # 炸板率
    bomb_rate = compute_bomb_rate(stocks)

    # 赚钱效应 = 最高连板数
    profit_effect = max_board

    # 题材强度
    top_themes = compute_concept_strength(stocks, code_to_concepts, code_to_name, top_n)

    # 最强的几个题材（用于标注在热力图格子上）
    top_labels = [f"{t['concept_name']}({t['zt_count']})" for t in top_themes[:5]]

    result = {
        "trade_date": date_str,
        "zt_total": zt_total,
        "zt_ge2_count": zt_ge2,
        "max_board": max_board,
        "board_dist": json.dumps(board_dist, ensure_ascii=False),
        "smash_coef": smash,
        "cum_rate": cum_rate,
        "bomb_rate": bomb_rate,
        "profit_effect": profit_effect,
        "top_themes": json.dumps(top_labels, ensure_ascii=False),
    }

    return result, top_themes


def write_results(conn, mood_result, top_themes):
    """写入数据库"""
    if mood_result is None:
        return

    # 写入情绪指标
    conn.execute(
        """INSERT OR REPLACE INTO market_mood_daily
           (trade_date, zt_total, zt_ge2_count, max_board, board_dist,
            smash_coef, cum_rate, bomb_rate, profit_effect, top_themes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mood_result["trade_date"], mood_result["zt_total"],
         mood_result["zt_ge2_count"], mood_result["max_board"],
         mood_result["board_dist"], mood_result["smash_coef"],
         mood_result["cum_rate"], mood_result["bomb_rate"],
         mood_result["profit_effect"], mood_result["top_themes"])
    )

    # 删除该日旧数据
    conn.execute("DELETE FROM concept_strength_daily WHERE trade_date = ?",
                 (mood_result["trade_date"],))

    # 写入题材强度
    for t in top_themes:
        conn.execute(
            """INSERT INTO concept_strength_daily
               (trade_date, concept_code, concept_name, zt_count, max_board, rank)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mood_result["trade_date"], t["concept_code"], t["concept_name"],
             t["zt_count"], t["max_board"], t["rank"])
        )

    conn.commit()


def print_summary(results):
    """打印摘要表格"""
    print(f"\n{'='*90}")
    print(f"  {'日期':<10} {'涨停':<6} {'≥2板':<6} {'最高板':<6} "
          f"{'砸盘':<6} {'累加%':<8} {'炸板率':<7} {'最强题材'}")
    print(f"{'─'*90}")

    for r in results[-20:]:  # 最近20天
        themes = json.loads(r["top_themes"]) if isinstance(r["top_themes"], str) else r["top_themes"]
        top_theme = themes[0] if themes else "-"
        print(f"  {r['trade_date']:<10} {r['zt_total']:<6} {r['zt_ge2_count']:<6} "
              f"{r['max_board']:<6} {r['smash_coef']:<6.1f} "
              f"{r['cum_rate']:<8.1f} {r['bomb_rate']:<7.1f}% "
              f"{top_theme}")

    print(f"{'─'*90}")

    # 情绪判断
    if results:
        latest = results[-1]
        smash = latest["smash_coef"]
        if smash > 8.0:
            tag = "🔥🔥 极度亢奋"
        elif smash > 6.0:
            tag = "🔥 偏亢奋"
        elif smash > 4.0:
            tag = "😐 中性"
        elif smash > 2.0:
            tag = "❄️ 偏冷"
        else:
            tag = "🧊 冰点"
        print(f"\n  📅 {latest['trade_date']} 砸盘系数: {smash:.1f} → {tag}")
        print(f"     涨停: {latest['zt_total']}只  ≥2板: {latest['zt_ge2_count']}只  "
              f"最高板: {latest['max_board']}板")


def main():
    parser = argparse.ArgumentParser(description="市场情绪引擎")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD")
    parser.add_argument("--start", default=None, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--top", type=int, default=20, help="题材强度 Top N")
    parser.add_argument("--no-db", action="store_true", help="只计算不写入 DB")
    parser.add_argument("--csv", default=None, help="导出 CSV")
    args = parser.parse_args()

    # 日期处理
    if args.date:
        dates = [args.date]
    elif args.start:
        conn_temp = init_db()
        rows = conn_temp.execute(
            "SELECT DISTINCT trade_date FROM zt_daily "
            f"WHERE trade_date >= ? "
            + (f"AND trade_date <= ? " if args.end else "ORDER BY trade_date") +
            "ORDER BY trade_date",
            (args.start, args.end) if args.end else (args.start,)
        ).fetchall()
        dates = [r[0] for r in rows]
        conn_temp.close()
    else:
        dates = [datetime.now().strftime("%Y%m%d")]

    if not dates:
        print("❌ 无交易日数据")
        return

    print(f"\n{'='*50}")
    print(f"  市场情绪引擎")
    print(f"  日期: {dates[0]}" if len(dates) == 1 else f"  日期: {dates[0]} → {dates[-1]} ({len(dates)} 天)")
    print(f"{'='*50}")

    conn = init_db()

    # 加载数据
    print(f"\n[1/3] 加载涨停数据...")
    data = load_zt_data(conn, dates)
    print(f"  有效交易日: {len(data)}")

    # 加载题材映射（可选，表不存在则跳过，用行业字段 fallback）
    print(f"[2/3] 加载题材映射...")
    code_to_concepts, code_to_name = {}, {}
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='concept_stock'"
        ).fetchall()
        if rows:
            code_to_concepts, code_to_name = load_concept_map(conn)
            mapped_stocks = sum(1 for codes in code_to_concepts.values() if codes)
            print(f"  题材映射已加载: {mapped_stocks} 只股票")
        else:
            print(f"  ⚠️ concept_stock 表不存在，使用「所属行业」替代题材")
    except Exception as e:
        print(f"  ⚠️ 题材映射加载失败: {e}")
        code_to_concepts, code_to_name = {}, {}

    # 计算
    print(f"[3/3] 计算情绪指标...")
    all_results = []
    all_theme_rows = []

    for ds in dates:
        result = compute_single_day(conn, ds, data, code_to_concepts, code_to_name, args.top)
        if result:
            mood, themes = result
            all_results.append(mood)
            all_theme_rows.extend(themes)

            if not args.no_db:
                write_results(conn, mood, themes)

            # 进度
            if len(all_results) % 20 == 0:
                print(f"    进度: {len(all_results)}/{len(dates)}")

    print(f"  计算完成: {len(all_results)} 天")

    # 输出
    if all_results:
        print_summary(all_results)

        if args.csv:
            import csv
            with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
                if all_results:
                    writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
                    writer.writeheader()
                    writer.writerows(all_results)
            print(f"\n  💾 CSV 已保存: {args.csv}")

    conn.close()
    print(f"\n{'='*50}\n")


if __name__ == "__main__":
    main()
