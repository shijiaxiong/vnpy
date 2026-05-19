"""
模块1 龙头筛选准确度测试
======================
流程：
  1. 通过 baostock/NeoData 获取板块成分股
  2. 拉取2024年Q3-Q4日线（含换手率）
  3. 对每个交易日执行 select_leaders()
  4. 记录Top3 + 得分 + 后续5/10/20日涨幅
  5. 输出评估报告
"""

import os
import sys
import time
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import baostock as bs
import pandas as pd

from scripts.fundamental_data_fetcher import fetch_fundamental_data

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
sys.path.insert(0, PROJECT_ROOT)
os.environ["PYTHONPATH"] = PROJECT_ROOT

from vnpy.alpha.yuanjun.config import LeaderConfig
from vnpy.alpha.yuanjun.leader_selector import SectorLeaderSelector


# ================================================================
# 配置
# ================================================================

# 半导体成分股（手工整理的A股主流半导体股，覆盖设计/制造/封测/设备/材料）
SEMICONDUCTOR_STOCKS = [
    "sh.688981", "sh.688012",  # 中芯国际、华润微（晶圆代工）
    "sh.688008", "sh.688009",  # 澜起科技、中国通号
    "sh.688256",               # 寒武纪（AI芯片）
    "sz.300782", "sh.688536",  # 卓胜微、思瑞浦（芯片设计）
    "sh.603501",               # 韦尔股份（芯片设计）
    "sh.688037", "sh.688120",  # 芯源微、华海清科（设备）
    "sz.002371", "sh.688072",  # 北方华创、拓荆科技（设备）
    "sh.688019", "sz.300236",  # 安集科技、上海新阳（材料）
    "sh.688200",               # 华峰测控（测试）
    "sz.002049", "sh.688521",  # 紫光国微、芯原股份（芯片设计）
    "sh.605111", "sh.600745",  # 新洁能、闻泰科技（功率）
    "sz.002409", "sh.688261",  # 雅克科技、东微半导（材料/功率）
    "sh.688396", "sh.688385",  # 华润微、复旦微电
    "sh.600171", "sh.688595",  # 上海贝岭、芯海科技
    "sz.300661", "sz.300724",  # 圣邦股份、捷佳伟创
]
SEMICONDUCTOR_BS_CODES = SEMICONDUCTOR_STOCKS

# 券商：baostock J67（资本市场服务=证券公司）
SECTORS = [
    {"name": "半导体", "bs_codes": SEMICONDUCTOR_BS_CODES},
    {"name": "券商", "bs_class": "J67资本市场服务"},
]

TEST_PERIOD = ("2024-10-01", "2024-12-31")  # 默认Q4，可通过命令行覆盖
LOOKBACK_DAYS = 70
TOP_N = 3
DATA_CACHE = f"{PROJECT_ROOT}/.cache/leader_test_data"
if len(sys.argv) >= 3:
    TEST_PERIOD = (sys.argv[1], sys.argv[2])
    print(f"[配置] 测试周期: {TEST_PERIOD[0]} ~ {TEST_PERIOD[1]}")


# ================================================================
# baostock 数据获取
# ================================================================

def login_bs() -> None:
    for i in range(3):
        lg = bs.login()
        if lg.error_code == "0":
            return
        print(f"[baostock] 登录失败({i+1}/3): {lg.error_msg}")
        time.sleep(1)
    raise RuntimeError("baostock 登录失败")


def get_bs_stocks_by_class(class_name: str) -> List[Tuple[str, str]]:
    """获取指定行业分类（industryClassification）的股票列表

    baostock query_stock_industry() 返回字段：
      [0]date [1]code(eg.sh.600030) [2]code_name [3]industry [4]industryClassification

    Returns
    -------
    List[Tuple[str, str]]
        [(baostock_code, stock_name)]
    """
    rs = bs.query_stock_industry()
    stocks = []
    while rs.next():
        row = rs.get_row_data()
        if row[3] == class_name:
            stocks.append((row[1], row[2]))
    return stocks


def get_k_data(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """获取个股日线（含换手率）"""
    rs = bs.query_history_k_data_plus(
        code, 'date,open,high,low,close,volume,amount,turn',
        start_date=start, end_date=end, frequency="d", adjustflag="2",
    )
    rows = []
    while rs.next():
        row = rs.get_row_data()
        if row[1] == "" or row[5] == "":
            continue
        rows.append({
            "date": row[0], "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
            "turnover": float(row[7]) if row[7] else 0,
            "amount": float(row[6]) if row[6] else 0,
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    return df


def build_sector_index(stock_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """用板块内个股均值拟合板块指数"""
    close_prices = pd.concat({c: df["close"] for c, df in stock_data.items()}, axis=1)
    high_prices = pd.concat({c: df["high"] for c, df in stock_data.items()}, axis=1)
    volumes = pd.concat({c: df["volume"] for c, df in stock_data.items()}, axis=1)
    return pd.DataFrame({
        "close": close_prices.mean(axis=1),
        "high": high_prices.mean(axis=1),
        "volume": volumes.sum(axis=1),  # 板块总成交量
    })


def batch_load_data(
    sector_name: str,
    bs_codes: List[str],
    start: str,
    end: str,
) -> Optional[Dict]:
    """批量获取并缓存板块数据"""
    os.makedirs(DATA_CACHE, exist_ok=True)
    cache_file = f"{DATA_CACHE}/{sector_name}.pkl"
    if os.path.exists(cache_file):
        print(f"  [缓存] 读取 {sector_name}...")
        return pd.read_pickle(cache_file)

    stock_data = {}
    failed = []
    total = len(bs_codes)

    for i, code in enumerate(bs_codes):
        df = get_k_data(code, start, end)
        if df is not None and len(df) > 30:
            stock_data[code] = df
        else:
            failed.append(code)
        time.sleep(0.3)

        if (i + 1) % 30 == 0:
            print(f"    {i+1}/{total}")
            bs.logout()
            login_bs()

    print(f"  成功: {len(stock_data)}, 失败: {len(failed)}")
    if not stock_data:
        return None

    sector_index = build_sector_index(stock_data)
    result = {"stock_data": stock_data, "sector_index": sector_index}
    # 缓存基本面数据（时效性要求低，随行情数据一起缓存）
    print("  [基本面] 获取市值数据...")
    fd = fetch_fundamental_data(sector_name, list(stock_data.keys()))
    if fd is not None:
        result["fundamental_data"] = fd
    pd.to_pickle(result, cache_file)
    return result


# ================================================================
# 测试逻辑
# ================================================================

def run_test(sector_name: str, data: Dict) -> pd.DataFrame:
    stock_data = data["stock_data"]
    sector_index = data["sector_index"]
    fundamental_data = data.get("fundamental_data")

    config = LeaderConfig(lookback_days=60, top_n=TOP_N, price_above_ma250=False)
    selector = SectorLeaderSelector(config)

    all_dates = sorted(set.intersection(
        *[set(df.index) for df in stock_data.values()],
        set(sector_index.index),
    ))
    test_dates = [d for d in all_dates if TEST_PERIOD[0] <= str(d.date()) <= TEST_PERIOD[1]]
    print(f"\n  {sector_name}: {len(all_dates)}交易日, 测试期{len(test_dates)}天"
          f" | 基本面数据: {'✅' if fundamental_data is not None else '❌'}")

    rows = []
    for t_idx, date in enumerate(test_dates):
        date_str = str(date.date())

        # 构建截至当日的切片
        daily_stock_data = {}
        for code, df in stock_data.items():
            hist = df.loc[:date]
            if len(hist) < 60:
                continue
            daily_stock_data[code] = hist

        daily_sector = sector_index.loc[:date]
        if len(daily_sector) < 60:
            continue

        leaders, details = selector.select_leaders(daily_stock_data, daily_sector, fundamental_data)

        for rank, ldr in enumerate(leaders[:TOP_N]):
            detail = details.get(ldr, {})
            row = {
                "日期": date_str, "板块": sector_name,
                "排名": rank + 1, "代码": ldr,
                "总分": detail.get("total_score", 0),
                "领涨分": detail.get("lead_score", 0),
                "抗跌分": detail.get("defend_score", 0),
                "带动分": detail.get("drive_score", 0),
                "实力分": detail.get("strength_score", 0),
            }

            date_idx = all_dates.index(date)
            for fwd in [5, 10, 20]:
                fwd_idx = min(date_idx + fwd, len(all_dates) - 1)
                fwd_date = all_dates[fwd_idx]
                entry = stock_data[ldr].loc[date, "close"]
                exit_ = stock_data[ldr].loc[fwd_date, "close"]
                row[f"涨跌{fwd}d"] = (exit_ - entry) / entry if entry > 0 else None

            rows.append(row)

        if (t_idx + 1) % 10 == 0:
            print(f"    {t_idx+1}/{len(test_dates)}天")

    return pd.DataFrame(rows)


# ================================================================
# 报告
# ================================================================

def fmt_pct(v):
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v:.2%}"


def safe_stat(s, fn):
    if s.empty:
        return None
    return fn(s)


def generate_report(results: Dict[str, pd.DataFrame]) -> str:
    lines = [
        "# 模块1 龙头筛选准确度测试报告",
        f"测试周期：{TEST_PERIOD[0]} ~ {TEST_PERIOD[1]}",
        f"TopN：{TOP_N}",
        "",
    ]
    for sname, df in results.items():
        lines += [f"---", f"## {sname}板块", ""]
        if df.empty:
            lines.append("无数据\n")
            continue

        lines += [
            "### 基础统计", "",
            "| 指标 | 值 |", "|------|-----|",
            f"| 总信号数 | {len(df)} |",
            f"| 交易日数 | {df['日期'].nunique()} |",
            f"| 选中不同股票数 | {df['代码'].nunique()} |", "",
        ]

        lines += ["### 后续涨跌幅统计", "",
                   "| 指标 | 5日 | 10日 | 20日 |",
                   "|------|------|-------|-------|"]
        cols = {d: df[f"涨跌{d}d"].dropna() for d in [5, 10, 20]}
        for label, fn in [("中位数", lambda s: float(s.median())),
                          ("均值", lambda s: float(s.mean())),
                          ("标准差", lambda s: float(s.std())),
                          ("胜率(>0)", lambda s: float((s > 0).sum() / len(s)))]:
            vals = [safe_stat(cols[d], fn) for d in [5, 10, 20]]
            fv = [f"{v:.2%}" if v is not None and abs(v) < 1 else (f"{v:.2f}" if v is not None else "N/A") for v in vals]
            lines.append(f"| {label} | {fv[0]} | {fv[1]} | {fv[2]} |")
        lines.append("")

        lines += ["### 排名表现", ""]
        for rank in [1, 2, 3]:
            rdf = df[df["排名"] == rank]
            if rdf.empty:
                continue
            parts = [f"**排名#{rank}**: {len(rdf)}次"]
            for d in [5, 10]:
                s = rdf[f"涨跌{d}d"].dropna()
                if not s.empty:
                    parts.append(f"{d}d中位数: {s.median():.2%}")
            lines.append(" | ".join(parts))
        lines.append("")

        lines += ["### 高频龙头（出现≥3次）", "",
                   "| 代码 | 次数 | 均分 |", "|------|------|------|"]
        freq = df.groupby("代码").agg(次数=("总分", "size"), 均分=("总分", "mean"))
        for code, r in freq[freq["次数"] >= 3].sort_values("次数", ascending=False).iterrows():
            lines.append(f"| {code} | {int(r['次数'])} | {r['均分']:.1f} |")
        lines.append("")

        lines += ["### 得分最高Top20", "",
                   "| 日期 | # | 代码 | 总分 | 领涨 | 抗跌 | 带动 | 实力 | 5d |",
                   "|------|---|------|------|------|------|------|------|-----|"]
        for _, r in df.nlargest(20, "总分").iterrows():
            lines.append(f"| {r['日期']} | #{int(r['排名'])} | {r['代码']} "
                         f"| {r['总分']:.1f} | {r['领涨分']:.0f} | {r['抗跌分']:.0f} "
                         f"| {r['带动分']:.0f} | {r['实力分']:.0f} | {fmt_pct(r.get('涨跌5d'))} |")
        lines.append("")

    return "\n".join(lines)


# ================================================================
# 主流程
# ================================================================

def main():
    print("=" * 60)
    print("龙头筛选模块 - 准确度测试")
    print("=" * 60)

    login_bs()

    start = (pd.to_datetime(TEST_PERIOD[0]) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = TEST_PERIOD[1]

    all_results = {}

    # ---- 半导体：使用手工整理成分股 ----
    print(f"\n{'='*50}")
    print(f"处理板块: 半导体 ({len(SEMICONDUCTOR_BS_CODES)}只成分股)")
    print(f"{'='*50}")
    data = batch_load_data("半导体", SEMICONDUCTOR_BS_CODES, start, end)
    if data:
        print(f"  成分股: {len(data['stock_data'])}只, 板块指数: {len(data['sector_index'])}天")
        all_results["半导体"] = run_test("半导体", data)
        print(f"  ✅ 测试完成: {len(all_results['半导体'])}条信号")

    # ---- 券商：使用 baostock J67 ----
    # 注意：industry query 会污染 session，需要重新登录
    bs.logout()
    login_bs()

    print(f"\n{'='*50}")
    print(f"处理板块: 券商 (J67分类)")
    print(f"{'='*50}")
    broker_stocks = get_bs_stocks_by_class("J67资本市场服务")
    broker_codes = [s[0] for s in broker_stocks]
    print(f"  成分股: {len(broker_codes)}只")
    data2 = batch_load_data("券商", broker_codes, start, end)
    if data2:
        print(f"  成分股: {len(data2['stock_data'])}只, 板块指数: {len(data2['sector_index'])}天")
        all_results["券商"] = run_test("券商", data2)
        print(f"  ✅ 测试完成: {len(all_results['券商'])}条信号")

    bs.logout()

    # ---- 生成报告 ----
    print(f"\n{'='*60}")
    print("生成报告...")
    report = generate_report(all_results)
    report_path = f"{PROJECT_ROOT}/notes/龙头筛选测试报告.md"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"报告已保存: {report_path}")

    # 摘要
    print(f"\n{'='*60}")
    print("摘要")
    print(f"{'='*60}")
    for sector_name, df in all_results.items():
        if df.empty:
            continue
        c5 = df["涨跌5d"].dropna()
        c10 = df["涨跌10d"].dropna()
        print(f"\n{sector_name}:")
        print(f"  交易日: {df['日期'].nunique()} | 信号: {len(df)}")
        print(f"  5d中位数: {c5.median():.2%} | 5d胜率: {(c5>0).sum()/len(c5):.1%}")
        print(f"  10d中位数: {c10.median():.2%} | 10d胜率: {(c10>0).sum()/len(c10):.1%}")

    print(f"\n✅ 测试完成")


if __name__ == "__main__":
    main()
