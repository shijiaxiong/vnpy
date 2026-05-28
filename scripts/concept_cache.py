#!/usr/bin/env python3
# ⚠️ 必须在所有 import 之前清除代理，否则 requests 已缓存代理配置
import os
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['no_proxy'] = '*'
# 同时清除可能存在于 .bashrc/.zshrc 里的代理
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

"""
题材概念缓存刷新
==================
拉取东方财富概念板块列表 + 每个板块的成分股 → SQLite 缓存

数据源: 东方财富 (akshare)
API:
  - stock_board_concept_name_em() → 题材元数据
  - stock_board_concept_cons_em(symbol) → 题材成分股

运行频率: 每周/月度（题材边界变化慢）
用法:
  python scripts/concept_cache.py              # 全量刷新
  python scripts/concept_cache.py --quick       # 快速模式（仅更新超7天未更新的）
  python scripts/concept_cache.py --force       # 强制全量

依赖: akshare, pandas
"""

import akshare as ak
import pandas as pd
import sqlite3
import time
import sys
import argparse
from datetime import datetime

# ===== 数据库路径 =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, ".workbuddy", "market_mood.db")
os.makedirs(os.path.join(PROJECT_DIR, ".workbuddy"), exist_ok=True)

# ===== DDL =====
DDL = """
CREATE TABLE IF NOT EXISTS concept_meta (
    concept_code   TEXT PRIMARY KEY,
    concept_name   TEXT,
    update_date    TEXT
);

CREATE TABLE IF NOT EXISTS concept_stock (
    concept_code   TEXT,
    stock_code     TEXT,
    update_date    TEXT,
    PRIMARY KEY (concept_code, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_cs_code ON concept_stock(concept_code);
CREATE INDEX IF NOT EXISTS idx_cs_stock ON concept_stock(stock_code);
"""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    conn.commit()
    return conn


def fetch_concept_list():
    """拉取概念板块列表"""
    print("  [1/3] 拉取概念板块列表...")
    df = ak.stock_board_concept_name_em()
    # 字段: code, name, (可能还有 price_change_pct, total_market_cap 等)
    records = []
    for _, row in df.iterrows():
        code = str(row.get("code", row.get("板块代码", ""))).strip()
        name = str(row.get("name", row.get("板块名称", ""))).strip()
        if code and name:
            records.append((code, name, datetime.now().strftime("%Y%m%d")))
    print(f"    概念板块数: {len(records)}")
    return records


def fetch_concept_stocks(concept_code):
    """拉取单个概念板块的成分股"""
    try:
        df = ak.stock_board_concept_cons_em(symbol=concept_code)
        records = []
        today = datetime.now().strftime("%Y%m%d")
        for _, row in df.iterrows():
            code = str(row.get("代码", row.get("code", ""))).strip()
            if code:
                # 统一代码格式：加 sz/sh 前缀
                if code.startswith("0") or code.startswith("3"):
                    code = "sz" + code
                elif code.startswith("6"):
                    code = "sh" + code
                elif code.startswith("8") or code.startswith("4"):
                    code = "bj" + code
                records.append((concept_code, code, today))
        return records
    except Exception as e:
        return []


def refresh(force=False, quick=False):
    conn = init_db()
    today = datetime.now().strftime("%Y%m%d")

    # 1. 概念板块列表
    concepts = fetch_concept_list()
    if not concepts:
        print("  ❌ 概念板块列表为空，中止")
        conn.close()
        return

    conn.executemany(
        "INSERT OR REPLACE INTO concept_meta (concept_code, concept_name, update_date) VALUES (?, ?, ?)",
        concepts
    )
    conn.commit()
    print(f"  ✅ concept_meta 已更新: {len(concepts)} 条")

    # 2. 每个板块的成分股
    print(f"  [2/3] 拉取成分股 ({'强制全量' if force else '增量' if quick else '全量'})...")

    # quick 模式：只更新超过7天未更新的
    skip_codes = set()
    if quick and not force:
        cutoff = (datetime.now() - __import__('datetime').timedelta(days=7)).strftime("%Y%m%d")
        rows = conn.execute(
            "SELECT DISTINCT concept_code FROM concept_stock WHERE update_date >= ?", (cutoff,)
        ).fetchall()
        skip_codes = {r[0] for r in rows}
        print(f"    Quick模式: 跳过 {len(skip_codes)} 个近期已更新的板块")

    total_stocks = 0
    errors = 0
    for i, (code, name, _) in enumerate(concepts, 1):
        if code in skip_codes:
            continue

        sys.stdout.write(f"\r   进度: {i}/{len(concepts)} {name[:10]:<12} ")
        sys.stdout.flush()

        stocks = fetch_concept_stocks(code)
        if stocks is None:
            errors += 1
            time.sleep(0.5)
            continue

        if stocks:
            # 删除旧数据，写入新数据
            conn.execute("DELETE FROM concept_stock WHERE concept_code = ?", (code,))
            conn.executemany(
                "INSERT OR REPLACE INTO concept_stock (concept_code, stock_code, update_date) VALUES (?, ?, ?)",
                stocks
            )
            total_stocks += len(stocks)

        conn.commit()

        # 限速：避免被ban
        if i % 20 == 0:
            time.sleep(0.3)

    print()  # 换行
    print(f"  ✅ concept_stock 已更新: {total_stocks} 条成分股关系 ({errors} 个错误)")

    # 统计
    n_concepts = conn.execute("SELECT COUNT(*) FROM concept_meta").fetchone()[0]
    n_relations = conn.execute("SELECT COUNT(*) FROM concept_stock").fetchone()[0]
    n_stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM concept_stock").fetchone()[0]
    print(f"\n  📊 统计: {n_concepts} 个题材, {n_relations} 条映射, 覆盖 {n_stocks} 只股票")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="题材概念缓存刷新")
    parser.add_argument("--force", action="store_true", help="强制全量刷新")
    parser.add_argument("--quick", action="store_true", help="快速模式（跳过7天内已更新的）")
    args = parser.parse_args()

    print(f"\n{'='*50}")
    print(f"  题材概念缓存刷新")
    print(f"  数据库: {DB_PATH}")
    print(f"{'='*50}\n")

    refresh(force=args.force, quick=args.quick)


if __name__ == "__main__":
    main()
