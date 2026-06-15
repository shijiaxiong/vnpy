"""
股票筛选器抽象接口 (StockSelector)

统一筛选入口，支持通过配置切换不同筛选策略：
- SectorLeaderSelector：板块龙头多维打分（现模块1）
- LimitUpSelector：涨停板个股筛选
- CompositeSelector：串联多个筛选器
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ================================================================
# 抽象基类
# ================================================================

class StockSelector(ABC):
    """股票筛选器抽象基类

    所有筛选器必须实现 select 方法，返回候选股票代码列表。
    子类可选择性重写 set_blacklist / set_whitelist 实现人工干预。
    """

    @abstractmethod
    def select(
        self,
        stock_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> Tuple[List[str], Dict]:
        """执行股票筛选

        Parameters
        ----------
        stock_data : Dict[str, pd.DataFrame]
            {股票代码: 日线DataFrame}，必须包含 open/high/low/close/volume/turnover 列
        **kwargs
            额外数据（如 sector_data、fundamental_data），各筛选器按需取用

        Returns
        -------
        Tuple[List[str], Dict]
            (候选股票代码列表, 筛选详情)
        """
        ...

    def set_blacklist(self, codes: List[str]) -> None:
        """设置人工黑名单（默认空实现，子类按需重写）"""
        return

    def set_whitelist(self, codes: List[str]) -> None:
        """设置人工白名单（默认空实现，子类按需重写）"""
        return


# ================================================================
# 涨停筛选
# ================================================================

@dataclass
class LimitUpConfig:
    """涨停板筛选配置参数"""

    limit_up_threshold: float = 0.095
    """涨停阈值（9.5%，覆盖涨停板附近未封死的票）"""

    min_volume_ratio: float = 1.0
    """最小量比（当日成交量 / 5日均量），过滤无量涨停"""

    max_consecutive_limits: int = 2
    """允许的最大连续涨停天数（排除高位连板股）"""

    min_turnover: float = 3.0
    """最小换手率（%），过滤冷门票"""

    top_n: int = 5
    """最多返回前 N 只"""


class LimitUpSelector(StockSelector):
    """涨停板个股筛选器

    筛选当日触及涨停的个股，排除连续涨停的高位品种。
    筛选优先级：封板时间早 > 量比高 > 换手率适中。

    Attributes
    ----------
    last_details : Dict[str, Dict]
        最近一次筛选详情，用于调试
    """

    def __init__(self, config: Optional[LimitUpConfig] = None) -> None:
        self.config = config or LimitUpConfig()
        self.last_details: Dict[str, Dict] = {}

    def select(
        self,
        stock_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> Tuple[List[str], Dict]:
        """执行涨停筛选

        流程：
        1. 遍历所有股票，计算当日涨幅
        2. 过滤出当日收盘涨幅 >= limit_up_threshold 的股票
        3. 排除连续涨停数超过 max_consecutive_limits 的股票
        4. 按封板力度（涨幅/量比/换手率）排序取前 N

        Parameters
        ----------
        stock_data : Dict[str, pd.DataFrame]
            {股票代码: 日线DataFrame}

        Returns
        -------
        Tuple[List[str], Dict]
            (涨停候选代码列表, 各候选详情)
        """
        cfg = self.config
        candidates: Dict[str, Dict] = {}
        self.last_details = {}

        for code, df in stock_data.items():
            if len(df) < 5:
                continue

            close = df["close"].iloc[-1]
            prev_close = df["close"].iloc[-2] if len(df) >= 2 else close
            if prev_close <= 0:
                continue

            daily_return = (close - prev_close) / prev_close
            if daily_return < cfg.limit_up_threshold:
                continue

            # 换手率过滤
            turnover = float(df["turnover"].iloc[-1])
            if turnover < cfg.min_turnover:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"换手率{turnover:.1f}% < 阈值{cfg.min_turnover}%",
                }
                continue

            # 量比（当日量 / 5日均量）
            volume = float(df["volume"].iloc[-1])
            avg_volume_5d = float(df["volume"].iloc[-6:-1].mean()) if len(df) >= 6 else volume
            volume_ratio = volume / avg_volume_5d if avg_volume_5d > 0 else 1.0
            if volume_ratio < cfg.min_volume_ratio:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"量比{volume_ratio:.2f} < 阈值{cfg.min_volume_ratio:.1f}",
                }
                continue

            # 连续涨停天数
            consecutive_limits = self._count_consecutive_limits(df)
            if consecutive_limits > cfg.max_consecutive_limits:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"连续涨停{consecutive_limits}天 > 阈值{cfg.max_consecutive_limits}天",
                }
                continue

            # 封板力度得分（涨幅越高越好，最大10%）
            limit_strength = min(daily_return / 0.10, 1.0)

            # 综合评分: 封板力度*0.5 + 量比归一化*0.3 + 换手率适中*0.2
            # 量比归一化：量比2以上视为充分，给予满分
            vol_score = min(volume_ratio / 2.0, 1.0)
            # 换手率适中：3%-15%区间最优
            if 3 <= turnover <= 15:
                turnover_score = 1.0
            elif turnover < 3:
                turnover_score = turnover / 3.0
            else:
                turnover_score = max(0, 1 - (turnover - 15) / 15)

            total_score = limit_strength * 0.5 + vol_score * 0.3 + turnover_score * 0.2

            detail = {
                "passed": True,
                "daily_return": round(daily_return, 4),
                "turnover": round(turnover, 2),
                "volume_ratio": round(volume_ratio, 2),
                "consecutive_limits": consecutive_limits,
                "total_score": round(total_score, 4),
            }
            self.last_details[code] = detail
            candidates[code] = detail

        # 按综合评分排序取前 N
        sorted_codes = sorted(
            candidates,
            key=lambda c: candidates[c]["total_score"],
            reverse=True,
        )
        selected = sorted_codes[: cfg.top_n]

        # 整理返回详情
        details = {
            code: self.last_details.get(code, {})
            for code in selected
        }
        return selected, details

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _count_consecutive_limits(self, df: pd.DataFrame) -> int:
        """计算连续涨停天数（含当前日）"""
        count = 0
        threshold = self.config.limit_up_threshold
        for i in range(len(df) - 1, -1, -1):
            if i == 0:
                count += 1
                break
            close = float(df["close"].iloc[i])
            prev_close = float(df["close"].iloc[i - 1])
            if prev_close <= 0:
                break
            ret = (close - prev_close) / prev_close
            if ret >= threshold:
                count += 1
            else:
                break
        return count


# ================================================================
# 串联筛选器
# ================================================================

class CompositeSelector(StockSelector):
    """串联多个筛选器

    按顺序执行筛选器链，前一个的输出作为后一个的输入。
    例如：[LimitUpSelector, SectorLeaderSelector] 先涨停后龙头打分。

    Parameters
    ----------
    selectors : List[StockSelector]
        筛选器链，按顺序执行
    """

    def __init__(self, selectors: List[StockSelector]) -> None:
        if not selectors:
            raise ValueError("selectors 不能为空")
        self.selectors = selectors

    def select(
        self,
        stock_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> Tuple[List[str], Dict]:
        """串联执行筛选器链

        第一个筛选器用全量 stock_data，后续筛选器从前一个的输出中
        裁剪 stock_data 再传递给下一个。
        """
        codes: List[str] = list(stock_data.keys())
        composite_details: Dict = {"chain": []}

        for i, sel in enumerate(self.selectors):
            filtered = {c: stock_data[c] for c in codes if c in stock_data}
            if not filtered:
                break

            stage_codes, stage_details = sel.select(filtered, **kwargs)
            composite_details["chain"].append({
                "selector": sel.__class__.__name__,
                "input_count": len(filtered),
                "output_count": len(stage_codes),
                "details": stage_details,
            })
            codes = stage_codes

        return codes, composite_details

    def set_blacklist(self, codes: List[str]) -> None:
        for sel in self.selectors:
            sel.set_blacklist(codes)

    def set_whitelist(self, codes: List[str]) -> None:
        for sel in self.selectors:
            sel.set_whitelist(codes)
