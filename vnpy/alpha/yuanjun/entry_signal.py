"""
模块3：入场时机模块 (EntrySignalChecker)

在满足止跌形态的基础上，检查当前是否适合入场。
条件包括：尾盘时间校验、价格位置评估、盈亏比计算。

设计要点：
  - 独立模块，可单独测试调参
  - 每个判断函数返回 (bool, Dict) 便于调试
  - 所有阈值通过 @dataclass 配置
"""

from typing import Dict, Tuple

import pandas as pd

from .config import EntryConfig


class EntrySignalChecker:
    """入场信号检查器

    在龙头股已确认止跌形态后，检查是否满足入场条件。
    必须同时满足：尾盘时间 + 价格在止损线上方1%-3% + 盈亏比≥2:1。

    Parameters
    ----------
    config : EntryConfig
        入场时机配置参数
    """

    def __init__(self, config: EntryConfig) -> None:
        self.config = config

    def can_enter(
        self,
        df: pd.DataFrame,
        stop_loss_price: float,
        current_time: str = "14:50",
    ) -> Tuple[bool, Dict]:
        """检查是否满足入场条件

        Parameters
        ----------
        df : pd.DataFrame
            个股日线数据，需包含 close/high 列
        stop_loss_price : float
            止损线（撤军线）价格
        current_time : str, optional
            当前时间 HH:MM 格式，默认 14:50

        Returns
        -------
        Tuple[bool, Dict]
            (是否可以入场, 详细信息)
        """
        result: Dict = {}

        # 条件1：尾盘时间检查
        time_ok, time_info = self._check_tail_time(current_time)
        result.update(time_info)
        if not time_ok:
            return False, result

        if stop_loss_price <= 0:
            result["stop_loss"] = 0
            result["reason"] = "止损价为0或负数"
            return False, result

        current_price = float(df["close"].iloc[-1])
        result["current_price"] = current_price
        result["stop_loss"] = stop_loss_price

        # 条件2：价格位置检查（距止损线1%-3%）
        dist_ok, dist_info = self._check_price_distance(current_price, stop_loss_price)
        result.update(dist_info)
        if not dist_ok:
            return False, result

        # 条件3：盈亏比检查（≥2:1）
        target_price = self._estimate_target_price(df)
        result["target_price"] = target_price

        rr_ok, rr_info = self._check_risk_reward(current_price, stop_loss_price, target_price)
        result.update(rr_info)
        if not rr_ok:
            return False, result

        result["can_enter"] = True
        result["entry_price"] = current_price
        return True, result

    # ------------------------------------------------------------------
    # 条件1：尾盘时间检查
    # ------------------------------------------------------------------

    def _check_tail_time(self, current_time: str) -> Tuple[bool, Dict]:
        """检查当前是否为尾盘交易时间

        Returns
        -------
        Tuple[bool, Dict]
            (是否在尾盘时段, {"is_tail_time": bool, ...})
        """
        is_tail = self.config.entry_time_start <= current_time <= self.config.entry_time_end
        info = {
            "is_tail_time": is_tail,
            "current_time": current_time,
            "entry_time_start": self.config.entry_time_start,
            "entry_time_end": self.config.entry_time_end,
        }
        if not is_tail:
            info["reason"] = (
                f"当前时间{current_time}不在尾盘窗口"
                f"[{self.config.entry_time_start}, {self.config.entry_time_end}]"
            )
        return is_tail, info

    # ------------------------------------------------------------------
    # 条件2：价格位置检查
    # ------------------------------------------------------------------

    def _check_price_distance(
        self,
        current_price: float,
        stop_loss_price: float,
    ) -> Tuple[bool, Dict]:
        """检查当前价格距离止损线是否在合理区间

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"distance_to_stop": float, ...})
        """
        if stop_loss_price <= 0:
            info = {"distance_to_stop": float('inf'), "reason": "止损价为0"}
            return False, info

        distance_to_stop = (current_price - stop_loss_price) / stop_loss_price
        info = {"distance_to_stop": round(distance_to_stop, 4)}

        if distance_to_stop < self.config.min_distance_to_stop:
            info["reason"] = (
                f"距止损线{distance_to_stop:.2%} < 阈值{self.config.min_distance_to_stop:.0%}，过近"
            )
            return False, info

        if distance_to_stop > self.config.max_distance_to_stop:
            info["reason"] = (
                f"距止损线{distance_to_stop:.2%} > 阈值{self.config.max_distance_to_stop:.0%}，过远"
            )
            return False, info

        return True, info

    # ------------------------------------------------------------------
    # 条件3：盈亏比检查
    # ------------------------------------------------------------------

    def _check_risk_reward(
        self,
        current_price: float,
        stop_loss_price: float,
        target_price: float,
    ) -> Tuple[bool, Dict]:
        """检查盈亏比是否达标

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"risk_reward_ratio": float, ...})
        """
        risk_per_share = current_price - stop_loss_price
        if risk_per_share <= 0:
            info = {
                "risk_reward_ratio": 0.0,
                "reason": "当前价已低于止损价，无风险收益空间",
            }
            return False, info

        reward_per_share = target_price - current_price
        if reward_per_share <= 0:
            info = {
                "risk_reward_ratio": 0.0,
                "reason": "目标价低于当前价，无盈利空间",
            }
            return False, info

        ratio = reward_per_share / risk_per_share
        info = {"risk_reward_ratio": round(ratio, 4)}

        if ratio < self.config.min_risk_reward_ratio:
            info["reason"] = (
                f"盈亏比{ratio:.2f} < 阈值{self.config.min_risk_reward_ratio:.1f}"
            )
            return False, info

        return True, info

    # ------------------------------------------------------------------
    # 阻力位估算
    # ------------------------------------------------------------------

    def _estimate_target_price(self, df: pd.DataFrame) -> float:
        """估算目标价位（阻力位）

        优先级：前期高点 > MA60 > 保守估计
        最终取三者最低，确保保守。

        Returns
        -------
        float
            目标价
        """
        current_price = float(df["close"].iloc[-1])
        candidates: list[float] = []

        # 方法1：前期高点阻力
        high_60 = float(df["high"].rolling(self.config.target_resistance_lookback).max().iloc[-1])
        if pd.notna(high_60) and high_60 > current_price * 1.02:
            candidates.append(high_60)

        # 方法2：60日均线阻力
        if len(df) >= 60:
            ma60 = float(df["close"].rolling(60).mean().iloc[-1])
            if pd.notna(ma60) and ma60 > current_price * 1.02:
                candidates.append(ma60)

        # 取最低的阻力位
        if candidates:
            target = min(candidates)
        else:
            target = current_price * 1.05

        return round(target, 2)
