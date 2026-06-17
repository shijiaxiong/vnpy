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
# 断板筛选（前N日连板+今日不再涨停）
# ================================================================

@dataclass
class BrokenBoardConfig:
    """断板筛选配置

    断板定义：前N日连续涨停（≥limit_up_line），今日收盘不再涨停（收盘涨幅<limit_up_line）。
    即"连板后断板"，代表连板动能中断，回调止跌后有机会二次启动。

    与旧版"冲板未封"的区别：
    - 旧：当日最高触及涨停但未封死，不要求前日涨停
    - 新：前N日必须连续涨停，今日不再涨停即可（不要求冲板）
    """

    limit_up_line: float = 0.095
    """涨停线（9.5%，判断涨停/断板的分界线）"""

    min_consecutive_limits_before: int = 1
    """断板前最少连续涨停天数，默认1=≥1板，0板走B方案兜底"""

    max_consecutive_limits_before: int = -1
    """断板前最多连续涨停天数，-1=不限制。如设3则排除4连板以上的高位股"""

    min_recent_gain_pct: float = 0.05
    """近N天累计涨幅阈值（5%），替代涨停要求，捕获无涨停的反弹票"""

    recent_gain_days: int = 3
    """统计近期涨幅的天数"""

    # 窗口涨停计数（非连续）
    limit_window_days: int = 7
    """涨停计数窗口（天）。统计最近N个交易日内的涨停天数（含当日），默认7天"""

    min_limits_in_window: int = 0
    """窗口内最少涨停天数，0=不限制。如设4则要求"7天4板"以上的高位股"""

    max_limits_in_window: int = -1
    """窗口内最多涨停天数，-1=不限制。如设3则排除"7天4板"以上过热股"""

    min_turnover: float = 0.0
    """最小换手率（%），默认0=关闭。连续涨停后可能一字闷杀，换手率归零也要捕获"""

    min_volume_ratio: float = 0.0
    """最小量比（当日量/5日均量），默认0=关闭。回测后根据效果再决定是否启用"""

    top_n: int = 10
    """最多返回前N只"""

    exclude_st: bool = True
    """是否排除ST股票，默认True。ST股涨跌停幅度为5%，不适用援军战法"""

    exclude_star: bool = True
    """是否排除科创板(688)，默认True。科创板涨跌停20%"""

    exclude_bse: bool = True
    """是否排除北交所(83/87/43/46开头)，默认True。北交所涨跌停30%"""

    board_cooling_days: int = 1
    """断板后最少冷却天数。默认1=断板后至少等1天才能入场（跳过涨停次日）。"""

    board_window_days: int = 15
    """涨停回溯窗口。从今天往前最多看多少个交易日找最近一次涨停。
    找到后，只要涨停之后到昨天为止没有再涨停，今天就是候选入场日。
    这意味着断板后的第2~15天每天都会被检查。"""


class BrokenBoardSelector(StockSelector):
    """断板个股筛选器

    筛选"前N日连续涨停 + 今日不再涨停"的个股。
    排除连板数过多的过热品种，按涨幅/留存度/量比/换手率排序。

    Attributes
    ----------
    last_details : Dict[str, Dict]
        最近一次筛选详情，用于调试
    """

    def __init__(self, config: Optional[BrokenBoardConfig] = None, name_map: Optional[Dict[str, str]] = None) -> None:
        self.config = config or BrokenBoardConfig()
        self.name_map = name_map or {}
        self.last_details: Dict[str, Dict] = {}

    def select(
        self,
        stock_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> Tuple[List[str], Dict]:
        """执行断板筛选

        流程：
        1. 检查前N日是否连续涨停（≥min_consecutive_limits_before）
        2. 检查今日是否不再涨停（收盘涨幅 < limit_up_line）
        3. 排除连板数超过 max_consecutive_limits_before 的过热股（-1=不限制）
        4. 窗口内涨停天数（min/max_limits_in_window，0/-1=不限制）
        5. 换手率/量比过滤
        6. 按得分排序

        Parameters
        ----------
        stock_data : Dict[str, pd.DataFrame]
            {股票代码: 日线DataFrame}

        Returns
        -------
        Tuple[List[str], Dict]
            (断板候选代码列表, 各候选详情)
        """
        cfg = self.config
        candidates: Dict[str, Dict] = {}
        self.last_details = {}

        for code, df in stock_data.items():
            if len(df) < 5:
                continue

            prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else 0.0
            if prev_close <= 0:
                continue

            # ST 过滤：ST/*ST 股票涨跌停 5%，不适用 10% 涨停线
            if cfg.exclude_st and self.name_map:
                name = self.name_map.get(code, "")
                if "ST" in name:
                    self.last_details[code] = {
                        "passed": False,
                        "reason": f"ST股票({name})，涨跌停5%不适用援军战法",
                    }
                    continue

            # 科创板过滤：688xxx 涨跌停 20%
            if cfg.exclude_star and (code.startswith("688") or code.startswith("689")):
                self.last_details[code] = {
                    "passed": False,
                    "reason": "科创板(688)，涨跌停20%不适用10%涨停线",
                }
                continue

            # 北交所过滤：83/87/43/46 开头，涨跌停 30%
            if cfg.exclude_bse and (code.startswith(("83", "87", "43", "46"))):
                self.last_details[code] = {
                    "passed": False,
                    "reason": "北交所，涨跌停30%不适用10%涨停线",
                }
                continue

            today_open = float(df["open"].iloc[-1])
            today_high = float(df["high"].iloc[-1])
            today_close = float(df["close"].iloc[-1])

            limit_up_price = prev_close * 1.10  # A股涨停价（10%）
            close_return = (today_close - prev_close) / prev_close
            upper_shadow = (today_high - max(today_close, today_open)) / today_high if today_high > 0 else 0

            # 条件1：涨停回溯 — 在最近 board_window_days 天内找到涨停
            # 找到最近一次涨停，确认涨停结束至今没有新涨停
            limit_up_idx = self._find_recent_limit_up(df, cfg.board_window_days)

            if limit_up_idx < 0:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"近{cfg.board_window_days}个交易日内无涨停",
                }
                continue

            # 涨停连续个数（用于 max_consecutive 检查）
            consecutive_before = self._count_consecutive_at(df, limit_up_idx)

            # 检查涨停结束后到今天为止，中间是否有新的涨停（断板信号作废）
            has_new_limit = False
            for i in range(limit_up_idx + 1, len(df) - 1):
                if i < 1:
                    continue
                close_i = float(df["close"].iloc[i])
                prev_i = float(df["close"].iloc[i - 1])
                if prev_i > 0 and (close_i - prev_i) / prev_i >= cfg.limit_up_line:
                    has_new_limit = True
                    self.last_details[code] = {
                        "passed": False,
                        "reason": f"涨停后出现新涨停(第{i - limit_up_idx}天)，断板信号不成立",
                    }
                    break
            if has_new_limit:
                continue

            # 冷却检查：至少冷却 days 个交易日
            days_since_limit = len(df) - 1 - limit_up_idx
            if days_since_limit <= cfg.board_cooling_days:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"涨停后仅{days_since_limit}天 < 最少冷却{cfg.board_cooling_days + 1}天（含断板日）",
                }
                continue

            # 条件2：今日不再涨停（收盘涨幅 < 涨停线）
            if close_return >= cfg.limit_up_line:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"收盘涨幅{close_return:.2%} >= 涨停线{cfg.limit_up_line:.0%}，仍在涨停非断板",
                }
                continue

            # 条件2b：今日不是暴跌（跌幅 ≥ 7% 直接排除，防止跌停日接飞刀）
            if close_return <= -0.07:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"当日跌幅{close_return:.1%}，可能是崩盘非回调，不追",
                }
                continue

            # 条件3：连续涨停天数上限检查
            # -1=不限制，允许任何天数
            if cfg.max_consecutive_limits_before >= 0 and consecutive_before > cfg.max_consecutive_limits_before:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"已连续涨停{consecutive_before}天 > 上限{cfg.max_consecutive_limits_before}天，过热排除",
                }
                continue

            # 条件4b：窗口内涨停天数（非连续，如"7天4板"）
            limits_in_window = self._count_limits_in_window(df)
            if cfg.min_limits_in_window > 0 and limits_in_window < cfg.min_limits_in_window:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"近{cfg.limit_window_days}天仅{limits_in_window}板 < 最少{cfg.min_limits_in_window}板",
                }
                continue
            if cfg.max_limits_in_window >= 0 and limits_in_window > cfg.max_limits_in_window:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"近{cfg.limit_window_days}天{limits_in_window}板 > 最多{cfg.max_limits_in_window}板",
                }
                continue

            # 条件5：换手率
            turnover = float(df["turnover"].iloc[-1])
            if turnover < cfg.min_turnover:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"换手率{turnover:.1f}% < 阈值{cfg.min_turnover}%",
                }
                continue

            # 条件6：量比
            volume = float(df["volume"].iloc[-1])
            avg_vol_5d = float(df["volume"].iloc[-6:-1].mean()) if len(df) >= 6 else volume
            volume_ratio = volume / avg_vol_5d if avg_vol_5d > 0 else 1.0
            if volume_ratio < cfg.min_volume_ratio:
                self.last_details[code] = {
                    "passed": False,
                    "reason": f"量比{volume_ratio:.2f} < 阈值{cfg.min_volume_ratio:.1f}",
                }
                continue

            # ---- 综合评分（五维） ----
            # 今日涨幅得分（close_return / 10%，越高越好，上限1.0）
            return_score = min(close_return / 0.10, 1.0)
            # 收盘留存度（close越接近high越好，说明没被砸太惨）
            close_retention = max(0, 1.0 - upper_shadow / 0.10)
            # 量比得分
            vol_score = min(volume_ratio / 3.0, 1.0)
            # 换手适中得分（5%-20%区间最优）
            if 5 <= turnover <= 20:
                turnover_score = 1.0
            elif turnover < 5:
                turnover_score = turnover / 5.0
            else:
                turnover_score = max(0, 1 - (turnover - 20) / 20)
            # 连板人气得分（consecutive_before / 3，3板满分）
            # 连板人气得分 — 非线性跳变，拉开连板差距
            if consecutive_before <= 0:
                board_score = 0.0
            elif consecutive_before == 1:
                board_score = 0.10
            elif consecutive_before == 2:
                board_score = 0.50
            else:
                board_score = 1.00

            total_score = (
                return_score * 0.15
                + close_retention * 0.15
                + vol_score * 0.15
                + turnover_score * 0.15
                + board_score * 0.40
            )

            detail = {
                "passed": True,
                "prev_close": round(prev_close, 2),
                "limit_up_price": round(limit_up_price, 2),
                "close_return": round(close_return, 4),
                "turnover": round(turnover, 2),
                "volume_ratio": round(volume_ratio, 2),
                "consecutive_before": consecutive_before,
                "limits_in_window": limits_in_window,
                "board_score": round(board_score, 4),
                "total_score": round(total_score, 4),
            }
            self.last_details[code] = detail
            candidates[code] = detail

        # 按得分排序取前N
        sorted_codes = sorted(candidates, key=lambda c: candidates[c]["total_score"], reverse=True)
        selected = sorted_codes[: cfg.top_n]

        details = {code: self.last_details.get(code, {}) for code in selected}
        return selected, details

    def _find_recent_limit_up(self, df: pd.DataFrame, max_lookback: int) -> int:
        """在最近 max_lookback 个交易日内找最近一次涨停的索引

        从昨天往前扫，找到最近一个涨幅≥9.5%的涨停日。

        Parameters
        ----------
        df : pd.DataFrame
            日线数据
        max_lookback : int
            最多向前看多少个交易日

        Returns
        -------
        int
            涨停日索引，未找到返回 -1
        """
        lookback = min(max_lookback, len(df) - 2)
        for offset in range(lookback):
            i = len(df) - 2 - offset  # 从昨天开始往前
            if i <= 0:
                break
            close = float(df["close"].iloc[i])
            prev = float(df["close"].iloc[i - 1])
            if prev > 0 and (close - prev) / prev >= self.config.limit_up_line:
                return i
        return -1

    def _count_consecutive_at(self, df: pd.DataFrame, end_idx: int) -> int:
        """计算以 end_idx 为结束的连续涨停天数"""
        count = 0
        threshold = self.config.limit_up_line
        for i in range(end_idx, 0, -1):
            close = float(df["close"].iloc[i])
            prev = float(df["close"].iloc[i - 1])
            if prev <= 0:
                break
            if (close - prev) / prev >= threshold:
                count += 1
            else:
                break
        return count

    def _count_consecutive_limits_before(self, df: pd.DataFrame, skip_days: int = 1) -> int:
        """计算连续涨停天数。

        Parameters
        ----------
        df : pd.DataFrame
            日线数据
        skip_days : int
            跳过最后N天。1=从倒数第2天开始数（默认，断板日当天）；
            2=从倒数第3天开始数（冷却1天，断板日后第2天）。

        Returns
        -------
        int
            连续涨停天数
        """
        count = 0
        threshold = self.config.limit_up_line
        for i in range(len(df) - 1 - skip_days, -1, -1):
            close = float(df["close"].iloc[i])
            prev = float(df["close"].iloc[i - 1]) if i > 0 else 0.0
            if prev <= 0:
                break
            ret = (close - prev) / prev
            if ret >= threshold:
                count += 1
            else:
                break
        return count

    def _calc_recent_gain(self, df: pd.DataFrame, days: int) -> float:
        """计算近N天内（不含当日）的累计涨幅

        Returns
        -------
        float
            累计涨幅（小数），如 0.05 = 5%
        """
        if len(df) < days + 2:
            return 0
        start_close = float(df["close"].iloc[-(days + 2)])
        end_close = float(df["close"].iloc[-2])  # 到昨天为止
        if start_close <= 0:
            return 0
        return (end_close - start_close) / start_close

    def _count_limits_in_window(self, df: pd.DataFrame) -> int:
        """统计近N个交易日内涨停天数（含当日，非连续）

        例如"板-板-板-涨5%-板-跌-板"在7天内 = 5板。

        Returns
        -------
        int
            窗口内涨停天数
        """
        window = self.config.limit_window_days
        threshold = self.config.limit_up_line
        recent = df.iloc[-window:] if len(df) >= window else df
        count = 0
        for i in range(1, len(recent)):
            prev = float(recent["close"].iloc[i - 1])
            cur = float(recent["close"].iloc[i])
            if prev <= 0:
                continue
            ret = (cur - prev) / prev
            if ret >= threshold:
                count += 1
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
