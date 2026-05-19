"""
消融实验：逐维度关断，诊断各维度贡献
用已缓存的数据重跑，不改代码只改权重
"""

import os
import sys
from copy import deepcopy
from typing import Dict

import pandas as pd

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
sys.path.insert(0, PROJECT_ROOT)
os.environ["PYTHONPATH"] = PROJECT_ROOT

from vnpy.alpha.yuanjun.config import LeaderConfig
from vnpy.alpha.yuanjun.leader_selector import SectorLeaderSelector


DATA_CACHE = f"{PROJECT_ROOT}/.cache/leader_test_data"
TEST_PERIOD = ("2024-10-01", "2024-12-31")


def load_cached(sector_name: str) -> Dict:
    path = f"{DATA_CACHE}/{sector_name}.pkl"
    if not os.path.exists(path):
        raise FileNotFoundError(f"缓存文件 {path} 不存在，请先运行 test_leader_selector.py")
    return pd.read_pickle(path)


def run_ablation(sector_name: str, data: Dict, config: LeaderConfig) -> pd.DataFrame:
    """用指定 config 跑一轮测试"""
    stock_data = data["stock_data"]
    sector_index = data["sector_index"]
    fundamental_data = data.get("fundamental_data")

    selector = SectorLeaderSelector(config)

    all_dates = sorted(set.intersection(
        *[set(df.index) for df in stock_data.values()],
        set(sector_index.index),
    ))
    test_dates = [d for d in all_dates if TEST_PERIOD[0] <= str(d.date()) <= TEST_PERIOD[1]]

    rows = []
    for date in test_dates:
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

        date_idx = all_dates.index(date)
        for rank, ldr in enumerate(leaders[:3]):
            detail = details.get(ldr, {})
            row = {
                "日期": str(date.date()), "板块": sector_name,
                "排名": rank + 1, "代码": ldr,
                "总分": detail.get("total_score", 0),
            }
            for fwd in [5, 10]:
                fwd_idx = min(date_idx + fwd, len(all_dates) - 1)
                fwd_date = all_dates[fwd_idx]
                entry = stock_data[ldr].loc[date, "close"]
                exit_ = stock_data[ldr].loc[fwd_date, "close"]
                row[f"涨跌{fwd}d"] = (exit_ - entry) / entry if entry > 0 else None
            rows.append(row)

    return pd.DataFrame(rows)


def calc_metrics(df: pd.DataFrame, label: str) -> Dict:
    """计算一组测试的关键指标"""
    c5 = df["涨跌5d"].dropna()
    c10 = df["涨跌10d"].dropna()
    top1 = df[df["排名"] == 1]
    t1_5 = top1["涨跌5d"].dropna()

    return {
        "实验": label,
        "信号数": len(df),
        "交易日": df["日期"].nunique(),
        "选股数": df["代码"].nunique(),
        "5d中位数": c5.median() if not c5.empty else None,
        "5d胜率": (c5 > 0).sum() / len(c5) if not c5.empty else None,
        "10d中位数": c10.median() if not c10.empty else None,
        "标准差": c5.std() if not c5.empty else None,
        "Top1_5d中位数": t1_5.median() if not t1_5.empty else None,
    }


def build_config(weights: Dict[str, float]) -> LeaderConfig:
    """用指定权重构建config"""
    return LeaderConfig(
        lookback_days=60, top_n=3, price_above_ma250=False,
        weight_lead=weights.get("领涨", 0.25),
        weight_defend=weights.get("抗跌", 0.25),
        weight_drive=weights.get("带动", 0.30),
        weight_strength=weights.get("实力", 0.20),
        cooldown_consecutive=2, cooldown_days=5,
    )


ABLATIONS = [
    ("A. 全维度(基准)", {"领涨": 0.25, "抗跌": 0.25, "带动": 0.30, "实力": 0.20}),
    ("B. 关领涨",     {"领涨": 0.00, "抗跌": 0.35, "带动": 0.40, "实力": 0.25}),
    ("C. 关抗跌",     {"领涨": 0.35, "抗跌": 0.00, "带动": 0.40, "实力": 0.25}),
    ("E. 关实力",     {"领涨": 0.35, "抗跌": 0.30, "带动": 0.35, "实力": 0.00}),
    ("F. 等权",       {"领涨": 0.25, "抗跌": 0.25, "带动": 0.25, "实力": 0.25}),
]

SECTORS = ["半导体", "券商"]


def main():
    print("=" * 60)
    print("消融实验：逐维度关断诊断")
    print("=" * 60)

    for sector in SECTORS:
        print(f"\n{'='*50}")
        print(f"板块：{sector}")
        print(f"{'='*50}")

        data = load_cached(sector)
        has_fd = "fundamental_data" in data
        print(f"  缓存数据: {len(data['stock_data'])}只 | 基本面: {'✅' if has_fd else '❌'}")

        results = []
        for label, weights in ABLATIONS:
            # E组关实力但需要基本面数据辅助判断
            config = build_config(weights)
            df = run_ablation(sector, data, config)
            metrics = calc_metrics(df, label)
            results.append(metrics)
            print(f"  {label}: {metrics['信号数']}信号 | 5d中位数{fmt(metrics['5d中位数'])} | "
                  f"胜率{fmt(metrics['5d胜率'])} | 选股{metrics['选股数']}只")

        # 表格输出
        print(f"\n  汇总对比：")
        print(f"  {'实验':<20} {'信号':>5} {'选股':>4} {'5d中位数':>10} {'5d胜率':>8} {'10d中位数':>10} {'标准差':>8} {'Top1_5d':>8}")
        print(f"  {'-'*20} {'-'*5} {'-'*4} {'-'*10} {'-'*8} {'-'*10} {'-'*8} {'-'*8}")
        for r in results:
            print(f"  {r['实验']:<20} {r['信号数']:>5} {r['选股数']:>4} "
                  f"{fmt(r['5d中位数']):>10} {fmt(r['5d胜率']):>8} {fmt(r['10d中位数']):>10} "
                  f"{fmt(r['标准差']):>8} {fmt(r['Top1_5d中位数']):>8}")

    print(f"\n✅ 消融实验完成")


def fmt(v):
    if v is None:
        return "N/A"
    return f"{v:.2%}" if abs(v) < 1 else f"{v:.2f}"


if __name__ == "__main__":
    main()
