"""
模块4：风控管理模块 (RiskManager)

管理每笔交易的仓位计算、止损线管理、移动止损和连亏熔断。
核心原则：本金安全优先，预设亏损可控。

设计要点：
  - 独立模块，可单独测试调参
  - 所有阈值通过 @dataclass 配置
  - 支持固定止损 / ATR止损 / 移动止损三种模式
  - 内置连亏熔断机制
"""

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .config import RiskConfig


class RiskManager:
    """风控管理器

    负责仓位计算、动态止损和全局风险控制。

    Parameters
    ----------
    config : RiskConfig
        风控配置参数
    """

    def __init__(self, config: RiskConfig) -> None:
        self.config = config

        # 内部状态
        self.consecutive_losses: int = 0
        """当前连续亏损次数"""
        self.is_paused: bool = False
        """是否因连亏熔断而暂停"""
        self.total_losses: int = 0
        """累计亏损次数"""
        self.total_wins: int = 0
        """累计盈利次数"""
        self.trade_count: int = 0
        """总交易次数"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
    ) -> Tuple[int, Dict]:
        """根据最大可承受亏损计算仓位

        Parameters
        ----------
        equity : float
            当前总资产
        entry_price : float
            入场价格
        stop_price : float
            止损价格

        Returns
        -------
        Tuple[int, Dict]
            (买入股数, 计算详情)
        """
        info: Dict = {
            "equity": equity,
            "entry_price": entry_price,
            "stop_price": stop_price,
        }

        risk_per_share = entry_price - stop_price
        info["risk_per_share"] = round(risk_per_share, 4)

        if risk_per_share <= 0:
            info["shares"] = 0
            info["reason"] = "止损价 >= 入场价，无法计算仓位"
            return 0, info

        max_loss_amount = equity * self.config.max_loss_per_trade
        info["max_loss_amount"] = round(max_loss_amount, 2)

        shares = int(max_loss_amount / risk_per_share)
        info["shares"] = shares
        info["cost"] = round(entry_price * shares, 2)
        info["actual_risk"] = round(risk_per_share * shares / equity, 4)

        return shares, info

    def calculate_stop_price(
        self,
        entry_price: float,
        atr: Optional[float] = None,
        current_high: Optional[float] = None,
    ) -> Tuple[float, Dict]:
        """计算动态止损价格（撤军线）

        取三种止损方式中的最高值作为最终止损价：
          1. 固定止损：入场价 × (1 - stop_loss_pct)
          2. ATR止损：入场价 - ATR × atr_multiplier（需要提供ATR）
          3. 移动止损：最高价 × (1 - trailing_stop_pct)（需要提供持仓期间最高价）

        Parameters
        ----------
        entry_price : float
            入场价格
        atr : Optional[float], optional
            ATR值，若不提供则仅使用固定止损
        current_high : Optional[float], optional
            持仓期间最高价，若不提供则使用入场价

        Returns
        -------
        Tuple[float, Dict]
            (止损价格, 计算详情)
        """
        info: Dict = {"entry_price": entry_price}

        # 固定止损
        fixed_stop = entry_price * (1 - self.config.stop_loss_pct)
        info["fixed_stop"] = round(fixed_stop, 4)

        # ATR止损
        if atr is not None and atr > 0:
            atr_stop = entry_price - self.config.atr_multiplier * atr
            info["atr"] = round(atr, 4)
            info["atr_stop"] = round(atr_stop, 4)
        else:
            atr_stop = 0.0
            info["atr_stop"] = None

        # 移动止损
        high_price = current_high if current_high is not None else entry_price
        trailing_stop = high_price * (1 - self.config.trailing_stop_pct)
        info["current_high"] = high_price
        info["trailing_stop"] = round(trailing_stop, 4)

        # 取最高值
        candidates = [fixed_stop]
        if atr_stop > 0:
            candidates.append(atr_stop)
        if trailing_stop > fixed_stop:
            candidates.append(trailing_stop)

        final_stop = max(candidates)
        info["final_stop"] = round(final_stop, 4)
        info["method"] = "fixed"
        if atr_stop == final_stop:
            info["method"] = "atr"
        if trailing_stop == final_stop:
            info["method"] = "trailing"

        return final_stop, info

    def update_after_trade(self, is_win: bool) -> Dict:
        """交易结束后更新风控状态

        Parameters
        ----------
        is_win : bool
            本次交易是否盈利

        Returns
        -------
        Dict
            更新后的风控状态
        """
        self.trade_count += 1

        if is_win:
            self.consecutive_losses = 0
            self.total_wins += 1
        else:
            self.consecutive_losses += 1
            self.total_losses += 1

        if 0 < self.config.max_consecutive_losses <= self.consecutive_losses:
            self.is_paused = True

        return self.get_status()

    def reset_pause(self) -> None:
        """重置熔断暂停状态（需人工确认后调用）"""
        self.is_paused = False
        self.consecutive_losses = 0

    def get_status(self) -> Dict:
        """获取当前风控状态详情

        Returns
        -------
        Dict
            风控状态，含是否暂停、连亏次数、总交易次数等
        """
        return {
            "is_paused": self.is_paused,
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.config.max_consecutive_losses,
            "trade_count": self.trade_count,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate": (
                round(self.total_wins / self.trade_count, 4)
                if self.trade_count > 0
                else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        """计算ATR（平均真实波幅）

        Parameters
        ----------
        df : pd.DataFrame
            日线数据，需包含 high/low/close 列
        period : int, optional
            ATR计算周期，默认14

        Returns
        -------
        float
            ATR值
        """
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = np.zeros(len(df))
        for i in range(1, len(df)):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hc, lc)

        return float(pd.Series(tr).rolling(period).mean().iloc[-1])
