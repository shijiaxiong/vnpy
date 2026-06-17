#!/usr/bin/env python3
"""
行业板块数据缓存管理
=====================
管理东方财富行业板块列表的本地缓存（cache_zt/board_cache/）。
缓存包含 100 个行业板块的代码和名称。

数据源: 东方财富 API
网络说明: 本环境通过系统代理时无法直连 push2.eastmoney.com，
         实时行情涨跌幅需在能直连的网络环境下运行。
"""

import json
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache_zt', 'board_cache')

INDEX_PATH = os.path.join(CACHE_DIR, 'board_index.json')
CSV_PATH = os.path.join(CACHE_DIR, 'board_list.csv')


def status():
    """查看缓存状态"""
    if not os.path.exists(INDEX_PATH):
        print("  缓存不存在")
        return

    with open(INDEX_PATH) as f:
        data = json.load(f)

    mtime = os.path.getmtime(INDEX_PATH)
    mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')

    print(f"\n  行业板块缓存: {mtime_str}")
    print(f"  板块数量: {len(data)}")
    print(f"  存储路径: {CACHE_DIR}")

    # 按板块名首字母分组统计
    groups = {}
    for b in data:
        first = b['name'][0]
        groups[first] = groups.get(first, 0) + 1

    print(f"\n  板块分布（首字）:")
    for k in sorted(groups):
        print(f"    {k}: {groups[k]} 个")


def list_boards():
    """列出所有板块"""
    if not os.path.exists(INDEX_PATH):
        print("  缓存不存在")
        return

    with open(INDEX_PATH) as f:
        data = json.load(f)

    print(f"\n  共 {len(data)} 个行业板块:\n")
    for i, b in enumerate(data, 1):
        print(f"    {i:3d}. {b['code']:8s}  {b['name']}")


def search(name):
    """按名称搜索板块"""
    if not os.path.exists(INDEX_PATH):
        print("  缓存不存在")
        return

    with open(INDEX_PATH) as f:
        data = json.load(f)

    matches = [b for b in data if name in b['name']]
    if matches:
        print(f"\n  找到 {len(matches)} 个匹配:\n")
        for b in matches:
            print(f"    {b['code']:8s}  {b['name']}")
    else:
        print(f"  未找到包含 '{name}' 的板块")


def main():
    if len(sys.argv) < 2:
        status()
        return

    cmd = sys.argv[1]

    if cmd == 'status':
        status()
    elif cmd == 'list':
        list_boards()
    elif cmd == 'search' and len(sys.argv) >= 3:
        search(sys.argv[2])
    else:
        print(f"用法: python {sys.argv[0]} [status|list|search <关键词>]")


if __name__ == '__main__':
    main()
