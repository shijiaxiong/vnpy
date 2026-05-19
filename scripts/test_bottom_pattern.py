"""
模块2：止跌形态识别器测试（精简版）

仅两个条件：
1. 回调幅度 ≥ 8%
2. 最近10日无6%+大阳线
"""
import os, sys; PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
sys.path.insert(0, PROJECT_ROOT); os.environ["PYTHONPATH"] = PROJECT_ROOT
from typing import Dict, List
import pandas as pd
from vnpy.alpha.yuanjun.config import BottomPatternConfig
from vnpy.alpha.yuanjun.bottom_pattern import BottomPatternRecognizer

def build_df(prices, volumes):
    """构建测试DataFrame"""
    return pd.DataFrame({
        "open": prices,
        "high": [x*1.015 for x in prices],
        "low": [x*0.985 for x in prices],
        "close": prices,
        "volume": volumes
    })

def test_synthetic():
    cfg = BottomPatternConfig()
    rec = BottomPatternRecognizer(cfg)
    p, t = 0, 0

    def chk(name, prices, volumes, expect):
        nonlocal p, t
        t += 1
        ok, det = rec.is_bottom_pattern(build_df(prices, volumes))
        s = "✅" if ok == expect else "❌"
        msg = f"{s} {name}: expect={expect}, got={ok}"
        if ok != expect:
            msg += f" | {det.get('reason', '')}"
        print(msg)
        if ok == expect:
            p += 1

    print(f"\n{'='*55}\n  合成测试\n{'='*55}")

    # 基准：前20天横盘在10元
    base = [10.0] * 20

    # ✅ 通过1：回调12%，无大阳线
    # 前10天10元，后10天从10跌到8.8（回调12%）
    ok1_prices = base + [10.0, 9.8, 9.6, 9.4, 9.3, 9.2, 9.1, 9.0, 8.9, 8.8]
    ok1_vol = [100] * 30
    chk("通过1: 回调12%✅, 无大阳线✅", ok1_prices, ok1_vol, True)

    # ❌ 不通过1：回调幅度不足
    fail1_prices = base + [10.0, 9.9, 9.85, 9.8, 9.78, 9.76, 9.75, 9.74, 9.73, 9.72]
    # 回调：(10-9.72)/10 = 2.8% < 8%
    chk("失败1: 回调2.8% < 8%", fail1_prices, [100]*30, False)

    # ❌ 不通过2：有6%+大阳线
    fail2_prices = base + [10.0, 9.5, 9.0, 9.7, 9.65, 9.6, 9.55, 9.5, 9.45, 9.4]
    # 9.0 → 9.7 涨幅 = 7.8% > 6% ❌
    chk("失败2: 有7.8%大阳线", fail2_prices, [100]*30, False)

    # ✅ 通过2：回调20%，有小阳线（<6%）
    ok2_prices = base + [10.0, 9.0, 8.5, 8.0, 8.2, 8.3, 8.4, 8.5, 8.55, 8.6]
    # 8.5 → 8.55 涨幅 = 0.6% < 6% ✅
    chk("通过2: 回调20%✅, 最大涨幅0.6%✅", ok2_prices, [100]*30, True)

    # ❌ 不通过3：回调足够但有8%大阳线
    fail3_prices = base + [10.0, 9.0, 8.0, 8.8, 8.5, 8.4, 8.3, 8.2, 8.1, 8.0]
    # 8.0 → 8.8 涨幅 = 10% > 6% ❌
    chk("失败3: 回调20%但有10%大阳线", fail3_prices, [100]*30, False)

    print(f"\n  合成测试: {p}/{t} ✅" if p == t else f"\n  合成测试: {p}/{t} ❌")
    return p == t

def test_real():
    cfg = BottomPatternConfig()
    rec = BottomPatternRecognizer(cfg)

    print(f"\n{'='*55}\n  真实数据测试\n{'='*55}")
    for sector in ["半导体", "券商"]:
        path = f"{PROJECT_ROOT}/.cache/leader_test_data/{sector}.pkl"
        if not os.path.exists(path):
            print(f"  {sector}: 数据不存在，跳过")
            continue

        data = pd.read_pickle(path)
        ok_all, tot, fail = 0, 0, {}
        for df in data["stock_data"].values():
            if len(df) < 20:
                continue
            for idx in range(20, len(df)):
                tot += 1
                ok, det = rec.is_bottom_pattern(df.iloc[:idx+1])
                if ok:
                    ok_all += 1
                else:
                    r = det.get("reason", "其他")
                    for kw in ["回调幅度", "大阳线"]:
                        if kw in r:
                            fail[kw] = fail.get(kw, 0) + 1
                            break
                    else:
                        fail["其他"] = fail.get("其他", 0) + 1

        print(f"\n  {sector}: {ok_all}/{tot} ({ok_all/tot:.3%}) 通过率")
        for r, c in sorted(fail.items(), key=lambda x: -x[1]):
            print(f"    {r}: {c/tot:.1%}")

if __name__ == "__main__":
    print("="*55 + "\n  模块2：止跌形态识别器（精简版）\n" + "="*55)
    test_synthetic()
    test_real()
    print("\n" + "="*55 + "\n  完成\n" + "="*55)
