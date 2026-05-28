#!/usr/bin/env python3
"""
连板 × 题材热力图仪表盘
==========================
从数据库读取情绪指标数据，生成交互式 HTML 热力图

用法:
  python scripts/heatmap_render.py                    # 默认近30天
  python scripts/heatmap_render.py --days 60          # 近60天
  python scripts/heatmap_render.py --start 20260501   # 指定起始日
  python scripts/heatmap_render.py --output dashboard.html

依赖: 需要 market_mood_daily + concept_strength_daily 表有数据
"""

import json
import sqlite3
import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict

# ===== 路径 =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, ".workbuddy", "market_mood.db")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def load_mood_data(conn, dates):
    """加载情绪指标数据"""
    if not dates:
        return []

    placeholders = ",".join(["?" for _ in dates])
    rows = conn.execute(
        f"SELECT trade_date, zt_total, zt_ge2_count, max_board, board_dist, "
        f"smash_coef, cum_rate, bomb_rate, profit_effect, top_themes "
        f"FROM market_mood_daily WHERE trade_date IN ({placeholders}) "
        f"ORDER BY trade_date",
        dates
    ).fetchall()

    results = []
    for row in rows:
        dt, zt_total, zt_ge2, max_b, bd_json, smash, cum, bomb, pe, themes_json = row
        results.append({
            "trade_date": dt,
            "zt_total": zt_total,
            "zt_ge2_count": zt_ge2,
            "max_board": max_b,
            "board_dist": json.loads(bd_json) if bd_json else {},
            "smash_coef": smash,
            "cum_rate": cum,
            "bomb_rate": bomb,
            "profit_effect": pe,
            "top_themes": json.loads(themes_json) if themes_json else [],
        })
    return results


def load_concept_strength(conn, dates, top_n=20):
    """加载题材强度数据"""
    if not dates:
        return {}

    placeholders = ",".join(["?" for _ in dates])
    rows = conn.execute(
        f"SELECT trade_date, concept_code, concept_name, zt_count, max_board, rank "
        f"FROM concept_strength_daily WHERE trade_date IN ({placeholders}) "
        f"AND rank <= {top_n} "
        f"ORDER BY trade_date, rank",
        dates
    ).fetchall()

    data = defaultdict(list)
    for row in rows:
        dt, cc, cn, zc, mb, rk = row
        data[dt].append({
            "concept_code": cc,
            "concept_name": cn,
            "zt_count": zc,
            "max_board": mb,
            "rank": rk,
        })

    return dict(data)


def build_board_heatmap(mood_data):
    """
    构建连板梯队热力图数据
    x: 板位 [2板, 3板, ..., 最高板]
    y: 日期
    """
    # 确定最高板位
    max_level = max((d["max_board"] or 2) for d in mood_data) if mood_data else 2
    max_level = min(max_level, 16)  # 截断到16板

    board_levels = list(range(2, max_level + 1))
    dates = [d["trade_date"] for d in mood_data]

    # 构建热力图数据 [[x_idx, y_idx, value], ...]
    heat_data = []
    for y_idx, day in enumerate(mood_data):
        for x_idx, level in enumerate(board_levels):
            count = day["board_dist"].get(str(level), 0)
            if count > 0:
                heat_data.append([x_idx, y_idx, count])

    # 2板格子的题材标注
    two_board_themes = {}
    for day in mood_data:
        dt = day["trade_date"]
        themes = day["top_themes"]
        if themes:
            two_board_themes[dt] = ", ".join(themes[:3])

    return {
        "levels": [f"{l}板" for l in board_levels],
        "dates": dates,
        "data": heat_data,
        "two_board_themes": two_board_themes,
    }


def build_theme_heatmap(dates, concept_data, top_n=30):
    """
    构建题材活跃度热力图
    x: 题材名
    y: 日期
    """
    # 统计所有出现过的题材，按总涨停数排序
    theme_total = defaultdict(int)
    for dt, themes in concept_data.items():
        for t in themes:
            theme_total[t["concept_name"]] += t["zt_count"]

    top_themes = sorted(theme_total.items(), key=lambda x: x[1], reverse=True)[:top_n]
    theme_names = [t[0] for t in top_themes]

    # 构建热力图数据
    heat_data = []
    for y_idx, dt in enumerate(dates):
        daily_themes = {t["concept_name"]: t["zt_count"] for t in concept_data.get(dt, [])}
        for x_idx, name in enumerate(theme_names):
            count = daily_themes.get(name, 0)
            if count > 0:
                heat_data.append([x_idx, y_idx, count])

    return {
        "themes": theme_names,
        "dates": dates,
        "data": heat_data,
    }


def get_date_range(conn, start=None, end=None, days=None):
    """获取日期范围"""
    if start and end:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market_mood_daily "
            "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (start, end)
        ).fetchall()
    elif start:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market_mood_daily "
            "WHERE trade_date >= ? ORDER BY trade_date",
            (start,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market_mood_daily ORDER BY trade_date"
        ).fetchall()

    dates = [r[0] for r in rows]
    if days and len(dates) > days:
        dates = dates[-days:]
    return dates


def render_html(mood_data, concept_data, output_path):
    """渲染 HTML 热力图仪表盘"""
    board_data = build_board_heatmap(mood_data)
    dates = board_data["dates"]

    theme_data = build_theme_heatmap(dates, concept_data)

    # 准备砸盘系数序列数据
    smash_series = [d["smash_coef"] or 0 for d in mood_data]
    cum_rate_series = [d["cum_rate"] or 0 for d in mood_data]
    dates_short = [d[4:] for d in dates]  # MM-DD

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A股连板 × 题材热力图</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 20px;
  }}
  .header {{
    text-align: center;
    padding: 20px 0 10px;
  }}
  .header h1 {{
    font-size: 24px;
    font-weight: 600;
    color: #ffffff;
    margin-bottom: 6px;
  }}
  .header .sub {{
    font-size: 13px;
    color: #8892b0;
  }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    max-width: 1600px;
    margin: 0 auto;
  }}
  .panel {{
    background: #16213e;
    border-radius: 12px;
    padding: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  }}
  .panel h3 {{
    font-size: 15px;
    font-weight: 500;
    color: #ccd6f6;
    margin-bottom: 8px;
  }}
  .chart {{
    width: 100%;
    height: 600px;
  }}
  .full {{ grid-column: 1 / -1; }}
  .indicators {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin: 0 auto 20px;
    max-width: 1600px;
  }}
  .card {{
    background: #16213e;
    border-radius: 10px;
    padding: 18px;
    text-align: center;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  }}
  .card .label {{
    font-size: 12px;
    color: #8892b0;
    margin-bottom: 6px;
  }}
  .card .value {{
    font-size: 28px;
    font-weight: 700;
    color: #64ffda;
  }}
  .card .sub-value {{
    font-size: 13px;
    color: #a8b2d1;
    margin-top: 2px;
  }}
  .latest-info {{
    max-width: 1600px;
    margin: 0 auto 20px;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 16px;
  }}
  .theme-badge {{
    display: inline-block;
    padding: 2px 8px;
    margin: 2px;
    border-radius: 4px;
    font-size: 11px;
    background: rgba(100, 255, 218, 0.15);
    color: #64ffda;
  }}
  @media (max-width: 768px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .indicators {{ grid-template-columns: repeat(2, 1fr); }}
    .latest-info {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>A股连板梯队 × 题材活跃度热力图</h1>
  <div class="sub">数据来源: 东方财富 | 时间: {dates[0]} ~ {dates[-1]} | {len(dates)} 个交易日</div>
</div>

<div class="indicators" id="indicatorCards"></div>

<div class="grid">
  <div class="panel">
    <h3>连板梯队分布 (2板 ~ {board_data["levels"][-1] if board_data["levels"] else "N"}板)</h3>
    <div class="chart" id="boardChart"></div>
  </div>
  <div class="panel">
    <h3>题材活跃度 (Top {len(theme_data["themes"])})</h3>
    <div class="chart" id="themeChart"></div>
  </div>
</div>

<div class="grid">
  <div class="panel full">
    <h3>砸盘系数 & 累加晋级率 走势</h3>
    <div class="chart" id="smashChart"></div>
  </div>
</div>

<script>
var boardLevels = {json.dumps(board_data["levels"], ensure_ascii=False)};
var boardDates = {json.dumps(dates, ensure_ascii=False)};
var boardData = {json.dumps(board_data["data"])};
var twoBoardThemes = {json.dumps(board_data["two_board_themes"], ensure_ascii=False)};
var themeNames = {json.dumps(theme_data["themes"], ensure_ascii=False)};
var themeDates = {json.dumps(dates, ensure_ascii=False)};
var themeHeatData = {json.dumps(theme_data["data"])};
var smashSeries = {json.dumps(smash_series)};
var cumRateSeries = {json.dumps(cum_rate_series)};
var datesShort = {json.dumps(dates_short, ensure_ascii=False)};

// ===== 指标卡片 =====
var latest = smashSeries[smashSeries.length - 1];
var latestDate = boardDates[boardDates.length - 1];
var latestCum = cumRateSeries[cumRateSeries.length - 1];
var avgSmash = smashSeries.reduce((a,b)=>a+b,0)/smashSeries.length;
var maxSmash = Math.max(...smashSeries);
var maxSmashDate = boardDates[smashSeries.indexOf(maxSmash)];

var tag = '';
if (latest > 8) tag = '极度亢奋';
else if (latest > 6) tag = '偏亢奋';
else if (latest > 4) tag = '中性';
else if (latest > 2) tag = '偏冷';
else tag = '冰点';

var cards = [
  {{ label: '最新砸盘系数', value: latest.toFixed(1), sub: tag + ' | ' + latestDate }},
  {{ label: '平均砸盘系数', value: avgSmash.toFixed(1), sub: '区间均值' }},
  {{ label: '累加晋级率', value: latestCum.toFixed(1) + '%', sub: '最新' }},
  {{ label: '最高砸盘系数', value: maxSmash.toFixed(1), sub: maxSmashDate }},
];

document.getElementById('indicatorCards').innerHTML = cards.map(c => `
  <div class="card">
    <div class="label">${{c.label}}</div>
    <div class="value">${{c.value}}</div>
    <div class="sub-value">${{c.sub}}</div>
  </div>
`).join('');

// ===== 通用 ECharts 配置 =====
function initHeatmap(domId, xData, yData, data, maxVal, labelFormatter) {{
  var chart = echarts.init(document.getElementById(domId));
  chart.setOption({{
    tooltip: {{
      position: 'top',
      formatter: function(p) {{
        return labelFormatter ? labelFormatter(p) : p.value[2] ? xData[p.value[0]] + '<br>' + yData[p.value[1]] + ': ' + p.value[2] + ' 只' : '';
      }}
    }},
    grid: {{
      left: 110, right: 50, top: 20, bottom: 60
    }},
    xAxis: {{
      type: 'category',
      data: xData,
      splitArea: {{ show: true }},
      axisLabel: {{ color: '#8892b0', fontSize: 11, rotate: 0 }},
      position: 'top',
    }},
    yAxis: {{
      type: 'category',
      data: yData,
      splitArea: {{ show: true }},
      axisLabel: {{ color: '#8892b0', fontSize: 10 }},
      inverse: true,
    }},
    visualMap: {{
      min: 0,
      max: maxVal,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 5,
      inRange: {{ color: ['#1a1a2e', '#1b4332', '#2d6a4f', '#40916c', '#52b788', '#74c69d', '#95d5b2'] }},
      textStyle: {{ color: '#8892b0' }},
    }},
    series: [{{
      type: 'heatmap',
      data: data,
      label: {{
        show: true,
        color: '#ffffff',
        fontSize: 10,
        formatter: function(p) {{
          return p.value[2] > 0 ? p.value[2] : '';
        }}
      }},
      emphasis: {{
        itemStyle: {{ shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.3)' }}
      }}
    }}]
  }});
  return chart;
}}

// ===== 连板梯队热力图 =====
var maxBoardVal = Math.max(...boardData.map(d => d[2]), 1);
var boardChart = initHeatmap('boardChart', boardLevels, boardDates, boardData, maxBoardVal);

// ===== 题材活跃度热力图 =====
var maxThemeVal = Math.max(...themeHeatData.map(d => d[2]), 1);
var themeChart = initHeatmap('themeChart', themeNames, themeDates, themeHeatData, maxThemeVal);

// ===== 砸盘系数走势 =====
var smashChart = echarts.init(document.getElementById('smashChart'));
smashChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ data: ['砸盘系数', '累加晋级率'], textStyle: {{ color: '#8892b0' }}, top: 0 }},
  grid: {{ left: 70, right: 70, top: 40, bottom: 50 }},
  xAxis: {{
    type: 'category',
    data: datesShort,
    axisLabel: {{ color: '#8892b0', fontSize: 10, rotate: 60 }},
  }},
  yAxis: [
    {{
      type: 'value',
      name: '砸盘系数',
      nameTextStyle: {{ color: '#64ffda' }},
      axisLabel: {{ color: '#8892b0' }},
      splitLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.05)' }} }},
    }},
    {{
      type: 'value',
      name: '晋级率 %',
      nameTextStyle: {{ color: '#ffd700' }},
      axisLabel: {{ color: '#8892b0' }},
    }}
  ],
  series: [
    {{
      name: '砸盘系数',
      type: 'bar',
      data: smashSeries,
      itemStyle: {{
        color: function(p) {{
          var v = p.value;
          return v > 8 ? '#ff6b6b' : v > 6 ? '#ffa500' : v > 4 ? '#4ecdc4' : v > 2 ? '#45b7d1' : '#7f8c8d';
        }}
      }},
      yAxisIndex: 0,
      markLine: {{
        silent: true,
        data: [{{ yAxis: avgSmash, label: {{ formatter: '均值 ' + avgSmash.toFixed(1), color: '#8892b0' }}, lineStyle: {{ color: '#ff6b6b', type: 'dashed' }} }}],
      }}
    }},
    {{
      name: '累加晋级率',
      type: 'line',
      data: cumRateSeries,
      yAxisIndex: 1,
      lineStyle: {{ color: '#ffd700', width: 1.5 }},
      symbol: 'none',
    }}
  ]
}});

// 响应式
window.addEventListener('resize', function() {{
  boardChart.resize();
  themeChart.resize();
  smashChart.resize();
}});
</script>
</body>
</html>'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="连板 × 题材热力图仪表盘")
    parser.add_argument("--days", type=int, default=30, help="最多显示天数")
    parser.add_argument("--start", default=None, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--output", default=None, help="输出文件路径")
    parser.add_argument("--top", type=int, default=30, help="题材热度 Top N")
    args = parser.parse_args()

    conn = init_db()

    # 获取日期范围
    dates = get_date_range(conn, start=args.start, end=args.end, days=args.days)

    if not dates:
        print("❌ 数据库中无数据，请先运行 mood_engine.py")
        conn.close()
        return

    print(f"\n  日期范围: {dates[0]} → {dates[-1]} ({len(dates)} 天)")

    # 加载数据
    mood_data = load_mood_data(conn, dates)
    concept_data = load_concept_strength(conn, dates, top_n=args.top)

    if not mood_data:
        print("❌ 无情绪数据")
        conn.close()
        return

    print(f"  情绪数据: {len(mood_data)} 天")
    print(f"  题材数据: {len(concept_data)} 天")

    # 渲染
    output_path = args.output or os.path.join(OUTPUT_DIR, "mood_dashboard.html")
    render_html(mood_data, concept_data, output_path)

    conn.close()
    print(f"\n  ✅ 仪表盘已生成: {output_path}")
    print(f"  题材数: {args.top}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
