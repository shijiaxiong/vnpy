"""
从历史数据中提取"有效底部"特征，辅助参数校准

方法：
  1. 找历史上跌幅≥5%后5日内反弹≥5%的"成功底部"
  2. 分析这些成功底部的共同特征分布
  3. 用特征分布指导 BottomPatternConfig 参数设置
"""

import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
sys.path.insert(0, PROJECT_ROOT)
os.environ["PYTHONPATH"] = PROJECT_ROOT

DATA_CACHE = f"{PROJECT_ROOT}/.cache/leader_test_data"


def find_bottom_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """从个股日线中找出所有可能的"底部"候选

    底部定义：当日收盘价是从近20日高点回落≥5%的位置，
    且后续5日反弹≥5%（成功）或继续下跌（失败）

    Returns
    -------
    pd.DataFrame
        每行一个底部候选
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    rows = []
    for i in range(60, len(df) - 10):
        # 20日最高点
        high_20 = high.iloc[i-20:i].max()
        if high_20 <= 0:
            continue

        # 从高点回撤幅度
        decline = (high_20 - close.iloc[i]) / high_20
        if decline < 0.03:  # 至少跌3%才考虑
            continue

        # 后续5日涨幅
        future_5d = close.iloc[i+5] / close.iloc[i] - 1 if i+5 < len(close) else 0

        # 连续下跌天数（从i往前数）
        ret = close.iloc[max(0,i-10):i+1].pct_change()
        cons_down = 0
        for j in range(len(ret)-1, 0, -1):
            if pd.notna(ret.iloc[j]) and ret.iloc[j] < 0:
                cons_down += 1
            else:
                break

        # 不创新低天数（当前最低点出现在几天前）
        lookback = 7
        recent_lows = low.iloc[i-lookback+1:i+1] if i >= lookback-1 else low.iloc[:i+1]
        min_low_idx = recent_lows.idxmin()
        days_since_min_low = (i - recent_lows.index.get_loc(min_low_idx)) if min_low_idx in recent_lows.index else 0

        # 前期均量（前20日）
        prev_vol = volume.iloc[i-40:i-5].mean() if i >= 45 else volume.iloc[:i].mean()
        # 下跌期均量（最近5日）
        down_vol = volume.iloc[i-4:i+1].mean() if i >= 4 else volume.iloc[:i+1].mean()
        vol_shrink = down_vol / prev_vol if prev_vol > 0 else 1.0

        # 5日均量
        ma5_vol = volume.iloc[i-4:i+1].mean() if i >= 4 else volume.mean()
        # 企稳期均量（最近2日）
        stable_vol = volume.iloc[i-1:i+1].mean() if i >= 1 else volume.iloc[i]
        vol_expand = stable_vol / ma5_vol if ma5_vol > 0 else 0.0

        # 最近10日最大阳线
        recent_close = close.iloc[i-9:i+1]
        max_up = 0
        for j in range(1, len(recent_close)):
            pct = recent_close.iloc[j] / recent_close.iloc[j-1] - 1
            if pct > max_up:
                max_up = pct

        rows.append({
            "date": str(df.index[i].date()),
            "close": float(close.iloc[i]),
            "decline_pct": decline,
            "cons_down_days": cons_down,
            "days_since_min_low": days_since_min_low,
            "vol_shrink_ratio": vol_shrink,
            "vol_expand_ratio": vol_expand,
            "max_big_yang_pct": max_up,
            "future_5d_ret": future_5d,
            "is_success": future_5d >= 0.03,  # 5日涨≥3%算成功
        })

    return pd.DataFrame(rows)


def analyze_sector(sector_name: str):
    """分析一个板块的底部特征"""
    path = f"{DATA_CACHE}/{sector_name}.pkl"
    if not os.path.exists(path):
        print(f"  ⚠️ {sector_name} 无缓存")
        return

    data = pd.read_pickle(path)
    all_candidates = []
    for code, df in data["stock_data"].items():
        cand = find_bottom_candidates(df)
        if not cand.empty:
            cand["code"] = code
            all_candidates.append(cand)

    if not all_candidates:
        print(f"  {sector_name}: 未找到底部候选")
        return

    full = pd.concat(all_candidates, ignore_index=True)
    success = full[full["is_success"]]
    fail = full[~full["is_success"]]

    print(f"\n  {sector_name}: 总候选{len(full)}, 成功{len(success)}({len(success)/len(full):.1%}), 失败{len(fail)}")

    # 各特征对比（成功 vs 失败）
    features = [
        ("decline_pct", "回调幅度", True),
        ("cons_down_days", "连跌天数", False),
        ("days_since_min_low", "不创新低天数", False),
        ("vol_shrink_ratio", "缩量比", True),
        ("vol_expand_ratio", "放量比", True),
        ("max_big_yang_pct", "最大阳线", True),
    ]

    print(f"\n  {'特征':<14} {'成功均值':>10} {'失败均值':>10} {'差值':>10}")
    print(f"  {'-'*46}")
    for col, label, is_pct in features:
        sv = float(success[col].mean()) if col in success else 0
        fv = float(fail[col].mean()) if col in fail else 0
        diff = sv - fv
        if is_pct:
            print(f"  {label:<14} {sv:>10.2%} {fv:>10.2%} {diff:>+10.2%}")
        else:
            print(f"  {label:<14} {sv:>10.2f} {fv:>10.2f} {diff:>+10.2f}")

    # 推荐参数
    print(f"\n  【推荐参数】(基于成功底部的25%/50%/75%分位)")
    pctiles = {"P25": 0.25, "P50": 0.50, "P75": 0.75}
    rec = {
        "down_amplitude_min": success["decline_pct"],
        "volume_shrink_ratio": success["vol_shrink_ratio"],
        "volume_expand_ratio": success["vol_expand_ratio"],
    }
    print(f"  {'参数':<20} {'P25':>8} {'P50':>8} {'P75':>8}")
    print(f"  {'-'*46}")
    for param, series in rec.items():
        vals = [f"{series.quantile(p):.2%}" if "ratio" not in param else f"{series.quantile(p):.2f}" for p in [0.25, 0.50, 0.75]]
        print(f"  {param:<20} {vals[0]:>8} {vals[1]:>8} {vals[2]:>8}")

    return full, success, fail


def main():
    print("=" * 60)
    print("  历史底部特征分析 - 参数校准")
    print("=" * 60)

    for sector in ["半导体", "券商"]:
        analyze_sector(sector)

    print(f"\n✅ 分析完成")


if __name__ == "__main__":
    main()
