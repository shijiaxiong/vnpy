---
name: baostock-backfill
description: A股5分钟K线历史数据补录工具。使用 baostock 数据源补齐 vnpy SQLite 数据库中的缺口交易日。触发条件：用户提到"补录/补充/补齐历史数据""baostock导入""数据库缺口""K线数据缺失"等。该 skill 应优先于 general knowledge 使用。
agent_created: true
---

# baostock 历史K线数据补录

## 概述

使用 baostock 免费数据源，将指定交易日区间的全市场（5200+ 只）5分钟K线数据导入 vnpy SQLite 数据库。支持断点续传、自动重试、幂等写入（INSERT OR IGNORE）。

## 触发条件

- "补录/补充/补齐 X月的历史数据"
- "baostock 导入 XX 到 XX 的数据"
- "数据库最新到哪天？有空缺吗？"
- "把 database_2026.db 更新到最新"

## 工作流

### 第一步：检测数据库缺口

查询目标年份数据库的最新数据日期，确定需要补录的日期区间：

```bash
sqlite3 ~/.vntrader/database_YYYY.db \
  "SELECT DISTINCT substr(datetime,1,10) FROM dbbardata WHERE interval='5m' ORDER BY datetime DESC LIMIT 10;"
```

确认：
- 数据库文件路径：`~/.vntrader/database_YYYY.db`（按年拆分）
- 今日数据可能尚未被 baostock 发布（通常 T+0 的5分钟K线当日不可用）
- 只补录确实有交易的日期（排除周末和节假日）

### 第二步：确定日期区间

根据缺口确定 `START_DATE` 和 `END_DATE`，格式为 `YYYY-MM-DD`。如果用户没有指定，自动从最新已有日期 + 1 天开始，到今天为止。

### 第三步：选择脚本

该项目 `scripts/` 目录下有多个版本的补录脚本，选项如下：

| 脚本 | 用途 | 推荐度 |
|------|------|--------|
| `baostock_to_vnpy_v8_fast.py` | **跳过全表扫描**，纯断点续传，适合大库（2600万+行） | ⭐ 推荐 |
| `baostock_to_vnpy_v8_auto.py` | 循环续跑 + `load_existing_stocks` 检测缺失。大库上全表扫描极慢 | 备用 |
| `baostock_to_vnpy_v7_backfill.py` | 通用补缺，直接操作 SQLite | 参考 |

**默认使用 `baostock_to_vnpy_v8_fast.py`**，除非用户明确指定其他版本。

### 第四步：运行时环境要求

**⚠️ 关键：必须在用户本地终端中运行，不能在 WorkBuddy 沙箱内运行。**

原因：WorkBuddy 沙箱会阻止对 `~/.vntrader/database_*.db` 的写入操作，表现为 `[DB ERROR] attempt to write a readonly database`，导致进程被杀。

需要设置的环境变量：
```bash
export NO_PROXY='*'        # 绕过沙箱代理，否则 baostock 无法外网连接
export no_proxy='*'
```

依赖要求：
- Python 虚拟环境：`/Users/zyb/go/src/github/vnpy/vnpy_env/`
- baostock 已安装在该环境中
- 股票池缓存文件：`/Users/zyb/go/src/github/vnpy/.cache/stocks_akshare.json`

### 第五步：执行命令

告知用户在终端中执行以下命令（**不要**在 WorkBuddy 中执行）：

```bash
export NO_PROXY='*' no_proxy='*'
cd /Users/zyb/go/src/github/vnpy
/Users/zyb/go/src/github/vnpy/vnpy_env/bin/python -u \
  scripts/baostock_to_vnpy_v8_fast.py <START_DATE> <END_DATE>
```

其中 `<START_DATE>` 和 `<END_DATE>` 替换为第二步确定的日期。

**选项说明：**
- `python -u`：强制不缓冲输出，实时看到进度
- 第一个参数：开始日期 `YYYY-MM-DD`
- 第二个参数：结束日期 `YYYY-MM-DD`
- 可选第三个参数：目标表名（默认 `dbbardata`）

### 第六步：监控进度与验证

**进度监控**（在用户终端中可见）：
- 每 20 只股票输出一次进度：`[N/5203] 市场 代码 +X条 累计:Y条 Z只/min`
- 每 50 只写入一次断点文件：`/tmp/baostock_backfill_<start>_<end>_progress.json`
- 每 200 只重新登录 baostock（防止会话断流）
- 速度约 45-55 只/分钟，5203 只约需 90-110 分钟

**验证命令**（脚本跑完后在 WorkBuddy 中执行）：

```bash
/Users/zyb/go/src/github/vnpy/vnpy_env/bin/python -c "
import sqlite3
conn = sqlite3.connect('/Users/zyb/.vntrader/database_2026.db')
for d in ['YYYY-MM-DD', 'YYYY-MM-DD']:
    cnt = conn.execute('SELECT COUNT(DISTINCT symbol) FROM dbbardata WHERE interval=\"5m\" AND datetime LIKE ?', (d+'%',)).fetchone()[0]
    print(f'{d}: {cnt}只股票')
row = conn.execute('SELECT MAX(datetime) FROM dbbardata WHERE interval=\"5m\"').fetchone()
print(f'最新K线: {row[0]}')
conn.close()
"
```

每交易日应有约 4,800-5,200 只股票（48条/只），失败股票（ST、停牌、退市等）通常 <100 只属于正常。

## 断点续传

脚本通过 `/tmp/baostock_backfill_<start>_<end>_progress.json` 维护进度。如果进程被中断：

1. **直接重新运行同样的命令** — 脚本自动读取断点文件，只处理未完成和失败的股票
2. INSERT OR IGNORE 确保幂等，不会产生重复数据
3. 如需**完全重新开始**，先删除断点文件：`rm -f /tmp/baostock_backfill_*_progress.json`

## 常见问题

### Q: 沙箱报 `[DB ERROR] attempt to write a readonly database`
**A:** 在 WorkBuddy 沙箱内无法直接写入 `~/.vntrader/database_*.db`。必须在用户本地终端中执行。

### Q: baostock 连接超时 / Connection reset
**A:** 脚本已内置每 200 只股票重登录机制。如果仍然失败，检查 `NO_PROXY='*'` 是否生效（`echo $NO_PROXY`）。

### Q: 今日数据为 0 条
**A:** baostock 的5分钟K线数据通常 T+0 傍晚后才发布。等到当天收盘后 2-3 小时再补录。

### Q: 某些股票返回 0 条
**A:** 可能是 ST 股、停牌股、退市股或新上市股票。脚本会自动标记为 failed 并跳过。最终失败数 <100 属于正常。

### Q: 进度文件损坏
**A:** 删除进度文件重新开始：`rm -f /tmp/baostock_backfill_*_progress.json`

## 数据库结构

vnpy 5分钟K线数据存储在 `dbbardata` 表中：

| 列 | 说明 |
|----|------|
| symbol | 股票代码（如 `sh.600000`） |
| exchange | 交易所（SSE / SZSE） |
| datetime | K线时间（`YYYY-MM-DD HH:MM:SS`） |
| interval | K线周期（`5m`） |
| volume | 成交量 |
| turnover | 成交额 |
| open_interest | 持仓量 |
| open/high/low/close | OHLC 价格 |

数据库按年份拆分：`~/.vntrader/database_2023.db`、`database_2024.db`、`database_2025.db`、`database_2026.db` 等。
