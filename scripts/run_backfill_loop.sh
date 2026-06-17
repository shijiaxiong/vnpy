#!/bin/bash
# baostock补缺自动重启wrapper
# 功能：bash超时杀掉Python后，自动重启继续，直到全部完成
# 用法：bash scripts/run_backfill_loop.sh

SCRIPT="/Users/shijiaxiong/go/src/github.com/vnpy/scripts/baostock_to_vnpy_v8_auto.py"
PROGRESS="/tmp/baostock_backfill_2026_progress.json"
DONE_MARKER="/tmp/baostock_backfill_DONE"

export PYTHONUNBUFFERED=1

echo "=== baostock补缺自动重启wrapper ==="
echo "脚本: $SCRIPT"
echo "断点: $PROGRESS"
echo ""

while true; do
    echo "--- 启动 $(date '+%H:%M:%S') ---"
    python3 "$SCRIPT"
    exit_code=$?
    echo "--- 退出 code=$exit_code $(date '+%H:%M:%S') ---"

    # 检查是否有DONE标记
    if [ -f "$DONE_MARKER" ]; then
        echo "检测到完成标记，退出循环。"
        rm -f "$DONE_MARKER"
        break
    fi

    # 检查断点：如果done数量稳定且无待处理，退出
    remaining=$(python3 -c "
import json, sqlite3
p = json.load(open('$PROGRESS'))
done_count = len(p.get('done', []))
conn = sqlite3.connect('/Users/shijiaxiong/.vntrader/database.db')
rows = conn.execute(
    \"SELECT COUNT(DISTINCT symbol) FROM dbbardata_2026 WHERE interval='5m' AND datetime >= '2026-05-12' AND datetime <= '2026-05-21 23:59:59'\"
).fetchone()
db_count = rows[0]
print(f'{db_count},{done_count}')
conn.close()
" 2>/dev/null)

    if [ -n "$remaining" ]; then
        db_stocks=$(echo "$remaining" | cut -d, -f1)
        done_stocks=$(echo "$remaining" | cut -d, -f2)
        echo "数据库: ${db_stocks}只 | 断点done: ${done_stocks}只"

        # 如果数据库已有足够股票(>5000)，视为完成
        if [ "$db_stocks" -gt 5000 ]; then
            echo "数据基本完整(${db_stocks}只)，退出。"
            break
        fi

        # 如果done比数据库还多（异常），清除断点重算
        if [ "$done_stocks" -gt "$db_stocks" ]; then
            echo "断点异常(done>db)，继续重试。"
        fi
    fi

    echo "等待3秒后重启..."
    sleep 3
done

echo ""
echo "=== 全部完成 ==="
python3 -c "
import sqlite3
conn = sqlite3.connect('/Users/shijiaxiong/.vntrader/database.db')
rows = conn.execute(
    'SELECT COUNT(DISTINCT symbol), COUNT(*) FROM dbbardata_2026 WHERE interval=\"5m\" AND datetime >= \"2026-05-12\" AND datetime <= \"2026-05-21 23:59:59\"'
).fetchone()
print(f'05-12~05-21: {rows[0]}只股票, {rows[1]}条K线')
conn.close()
" 2>/dev/null
