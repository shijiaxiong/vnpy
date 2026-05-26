"""
板块趋势过滤器 (SectorTrendFilter)

第一层增强：基于板块等权指数的 MA5 趋势过滤。
核心逻辑：板块 5 日均线不再创新低 → 板块下跌趋势已衰竭 → 允许入场。

与日涨幅前50%过滤（SectorBacktest 中）互补：
- 日涨幅：当日板块是否强势（短线热度）
- MA5趋势：板块中期是否止跌（趋势方向）
"""

from typing import Dict, List, Tuple
import numpy as np


class SectorTrendFilter:
    """板块MA5趋势过滤器

    维护每个子板块的滚动等权指数，判断MA5是否仍在创新低。
    调用方需在每个交易日为每个子板块传入当日的等权均价。

    Parameters
    ----------
    window : int
        MA周期，默认5
    lookback_days : int
        比较窗口：当前MA5与前N日前的MA5比较，默认5
    tolerance : float
        容忍度：允许MA5小幅下跌仍视为走平，默认0.2%
    """

    def __init__(
        self,
        window: int = 5,
        lookback_days: int = 5,
        tolerance: float = 0.002,
    ) -> None:
        self.window = window
        self.lookback_days = lookback_days
        self.tolerance = tolerance
        # {sector: [avg_close_1, avg_close_2, ...]}
        self._cache: Dict[str, List[float]] = {}
        self._max_cache = window + lookback_days + 5

    def feed(self, sector: str, avg_close: float) -> None:
        """喂入当日板块等权均价"""
        self._cache.setdefault(sector, []).append(avg_close)
        if len(self._cache[sector]) > self._max_cache:
            self._cache[sector] = self._cache[sector][-self._max_cache:]

    def is_trend_stable(self, sector: str) -> bool:
        """判断板块趋势是否稳定（MA5不创新低）

        Returns
        -------
        bool
            True = 板块趋势稳定或回升，允许入场
            False = 板块仍在加速下跌，禁止入场
        """
        cache = self._cache.get(sector, [])
        min_len = self.window + self.lookback_days
        if len(cache) < min_len:
            return True  # 数据不足放行

        # 当前 MA5
        ma5_now = np.mean(cache[-self.window:])
        # N日前的 MA5
        ma5_prev = np.mean(cache[-(self.window + self.lookback_days):-self.lookback_days])

        # 不创新低（允许 tolerance 的小幅下跌）
        return ma5_now >= ma5_prev - ma5_prev * self.tolerance

    def get_ma5_values(self, sector: str) -> Tuple[float, float]:
        """返回 (当前MA5, N日前MA5)，用于调试"""
        cache = self._cache.get(sector, [])
        if len(cache) < self.window + self.lookback_days:
            return (0.0, 0.0)
        ma5_now = np.mean(cache[-self.window:])
        ma5_prev = np.mean(cache[-(self.window + self.lookback_days):-self.lookback_days])
        return (round(float(ma5_now), 4), round(float(ma5_prev), 4))

    def reset(self) -> None:
        """清空缓存（换股票池时调用）"""
        self._cache.clear()
