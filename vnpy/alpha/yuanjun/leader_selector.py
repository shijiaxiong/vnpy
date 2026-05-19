"""
模块1：龙头筛选模块 (SectorLeaderSelector)

通过四大维度（领涨、抗跌、带动板块、综合实力）对板块内股票打分，
识别出具有"龙头"特征的股票。

设计要点：
  - 独立模块，可单独测试调参
  - 每个判断函数返回 (bool, Dict) 便于调试
  - 支持人工黑/白名单干预筛选结果
  - 预留数据源接口，可接入不同数据源
"""

from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import pandas as pd

from .config import LeaderConfig

if TYPE_CHECKING:
    from scripts.flow_factor import ScoreAdjuster


class SectorLeaderSelector:
    """板块龙头识别器

    对板块内所有股票进行多维度打分排序，筛选出龙头候选。

    Parameters
    ----------
    config : LeaderConfig
        龙头筛选配置参数

    Attributes
    ----------
    manual_blacklist : Set[str]
        人工黑名单（排除的股票代码），用于人工干预
    manual_whitelist : Set[str]
        人工白名单（强制纳入的股票代码），用于人工干预
    last_scores : Dict[str, float]
        最近一次打分结果，用于调试
    last_details : Dict[str, Dict]
        最近一次各维度详细得分，用于调试
    """

    def __init__(
        self,
        config: LeaderConfig,
        score_adjuster: Optional["ScoreAdjuster"] = None,
    ) -> None:
        self.config = config
        self.score_adjuster = score_adjuster
        """可选评分调整器（如资金流向因子），None 时不生效"""

        # 人工干预集合
        self.manual_blacklist: Set[str] = set()
        """人工排除的黑名单股票代码集合，set后筛选自动跳过"""
        self.manual_whitelist: Set[str] = set()
        """人工纳入的白名单股票代码集合，强制作为龙头候选"""

        # 调试信息缓存
        self.last_scores: Dict[str, float] = {}
        """最近一次打分的总分"""
        self.last_details: Dict[str, Dict[str, float]] = {}
        """最近一次打分的各维度详细得分"""

        # 冷却状态
        self._selection_streak: Dict[str, int] = {}
        """连续当选计数 {code: streak_count}"""
        self._cooldown_until: Dict[str, int] = {}
        """冷却截止日期索引 {code: cooldown_until_date_index}"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def select_leaders(
        self,
        stock_data: Dict[str, pd.DataFrame],
        sector_data: pd.DataFrame,
        fundamental_data: Optional[pd.DataFrame] = None,
    ) -> Tuple[List[str], Dict[str, Dict]]:
        """执行龙头筛选

        Parameters
        ----------
        stock_data : Dict[str, pd.DataFrame]
            {股票代码: 日线DataFrame}，必须包含 open/high/low/close/volume/turnover 列
        sector_data : pd.DataFrame
            板块指数日线DataFrame，必须包含 close 列
        fundamental_data : Optional[pd.DataFrame], optional
            基本面数据，需包含 mkt_cap_percentile / roe_percentile 列
            index为股票代码

        Returns
        -------
        Tuple[List[str], Dict[str, Dict]]
            (龙头代码列表, 各股票详细得分信息)
        """
        scores: Dict[str, float] = {}
        details: Dict[str, Dict] = {}
        sector_return: pd.Series = sector_data["close"].pct_change(self.config.lookback_days)

        # 获取当前日期，用于冷却判断
        current_dates = [df.index[-1] for df in stock_data.values() if len(df) > 0]
        if not current_dates:
            return [], {}
        current_date = max(current_dates)

        # 清理已过期的冷却状态
        for code in list(self._cooldown_until.keys()):
            if self._cooldown_until[code] <= current_date.timestamp():
                del self._cooldown_until[code]
                if code in self._selection_streak:
                    del self._selection_streak[code]

        for code, df in stock_data.items():
            # 人工黑名单过滤
            if code in self.manual_blacklist:
                continue

            if len(df) < self.config.lookback_days:
                continue

            # 冷却机制：检查是否在冷却期
            if code in self._cooldown_until:
                if self._cooldown_until[code] > current_date.timestamp():
                    details[code] = {"cooldown": True, "skip_reason": f"冷却中（至{self._cooldown_until[code]}）"}
                    continue
                else:
                    del self._cooldown_until[code]

            # 基础筛选（快速过滤）
            passed, filter_info = self._pass_basic_filter(df, fundamental_data, code)
            if not passed:
                continue

            # 四个维度打分
            lead_score, lead_info = self._calc_lead_score(df, sector_data)
            defend_score, defend_info = self._calc_defend_score(df, sector_data)
            drive_score, drive_info = self._calc_drive_score(df, sector_data)
            strength_score, strength_info = self._calc_strength_score(df, fundamental_data, code)

            # 加权总分
            total_score = (
                self.config.weight_lead * lead_score
                + self.config.weight_defend * defend_score
                + self.config.weight_drive * drive_score
                + self.config.weight_strength * strength_score
            )

            scores[code] = total_score
            details[code] = {
                "total_score": round(total_score, 2),
                "lead_score": round(lead_score, 2),
                "defend_score": round(defend_score, 2),
                "drive_score": round(drive_score, 2),
                "strength_score": round(strength_score, 2),
                "lead_info": lead_info,
                "defend_info": defend_info,
                "drive_info": drive_info,
                "strength_info": strength_info,
                "filter_info": filter_info,
            }

        # 人工白名单强制纳入
        for code in self.manual_whitelist:
            if code not in scores and code in stock_data:
                scores[code] = 0.0

        # 可选因子调整（如资金流向）
        if self.score_adjuster is not None and scores:
            adj = self.score_adjuster.get_adjustments(list(scores.keys()))
            for code in scores:
                if code in adj:
                    scores[code] += adj[code]
                    details[code]["flow_adjustment"] = round(adj[code], 2)

        # 排序取Top N
        sorted_codes = sorted(scores, key=scores.get, reverse=True)
        leader_codes = sorted_codes[: self.config.top_n]

        # 更新冷却状态
        cooldown_days_sec = self.config.cooldown_days * 86400  # 交易日转为秒数（近似）
        for code in leader_codes:
            self._selection_streak[code] = self._selection_streak.get(code, 0) + 1
            if self._selection_streak[code] >= self.config.cooldown_consecutive:
                self._cooldown_until[code] = current_date.timestamp() + cooldown_days_sec
                self._selection_streak[code] = 0  # 重置计数

        # 未当选的股票重置计数
        for code in scores:
            if code not in leader_codes:
                self._selection_streak[code] = 0

        self.last_scores = scores
        self.last_details = details

        return leader_codes, {c: details[c] for c in leader_codes if c in details}

    def set_blacklist(self, codes: List[str]) -> None:
        """设置人工黑名单

        Parameters
        ----------
        codes : List[str]
            需要排除的股票代码列表
        """
        self.manual_blacklist = set(codes)

    def set_whitelist(self, codes: List[str]) -> None:
        """设置人工白名单

        Parameters
        ----------
        codes : List[str]
            强制作为龙头候选的股票代码列表
        """
        self.manual_whitelist = set(codes)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _pass_basic_filter(
        self,
        df: pd.DataFrame,
        fundamental_data: Optional[pd.DataFrame],
        code: str = "",
    ) -> Tuple[bool, Dict]:
        """基础门槛过滤

        Parameters
        ----------
        df : pd.DataFrame
            个股日线数据
        fundamental_data : pd.DataFrame | None
            基本面数据，index为股票代码，需含 mkt_cap_percentile / roe_percentile 列
        code : str
            股票代码，用于从 fundamental_data 中按行索引

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, 过滤详情)
        """
        info: Dict = {}
        current_price = df["close"].iloc[-1]

        # 换手率条件
        turnover = df["turnover"].iloc[-1]
        info["turnover"] = float(turnover)
        if turnover < self.config.min_turnover_pct:
            return False, {**info, "reason": f"换手率{turnover:.1f}% < 阈值{self.config.min_turnover_pct}%"}

        # 年线条件
        if self.config.price_above_ma250:
            if len(df) < 250:
                return False, {**info, "reason": f"历史数据不足250天（仅{len(df)}天）"}
            ma250 = df["close"].rolling(250).mean().iloc[-1]
            info["ma250"] = float(ma250)
            if current_price < ma250:
                return False, {**info, "reason": f"当前价{current_price:.2f} < 年线{ma250:.2f}"}
            distance = abs(current_price / ma250 - 1)
            if distance > self.config.max_distance_to_ma250:
                return False, {
                    **info,
                    "reason": f"偏离年线{distance:.1%} > 阈值{self.config.max_distance_to_ma250:.1%}",
                }

        # 基本面条件（如果有数据且提供了股票代码）
        if fundamental_data is not None and code and code in fundamental_data.index:
            mkt_cap_pct = float(fundamental_data.loc[code, "mkt_cap_percentile"])
            roe_pct = float(fundamental_data.loc[code, "roe_percentile"])
            info["mkt_cap_percentile"] = float(mkt_cap_pct)
            info["roe_percentile"] = float(roe_pct)
            if mkt_cap_pct < self.config.min_market_cap_pct:
                return False, {
                    **info,
                    "reason": f"市值百分位{mkt_cap_pct:.0%} < " f"阈值{self.config.min_market_cap_pct:.0%}",
                }
            if roe_pct < self.config.min_roe_pct:
                return False, {
                    **info,
                    "reason": f"ROE百分位{roe_pct:.0%} < 阈值{self.config.min_roe_pct:.0%}",
                }

        info["current_price"] = float(current_price)
        info["passed"] = True
        return True, info

    def _calc_lead_score(
        self,
        stock_df: pd.DataFrame,
        sector_df: pd.DataFrame,
    ) -> Tuple[float, Dict]:
        """维度1：领涨得分（0-100）

        使用多周期动量加权评估领涨能力：
          - 短期 (5d) 超额 × 0.5
          - 中期 (10d) 超额 × 0.3
          - 长期 (20d) 超额 × 0.2
        外加启动领先度和上涨参与率。
        """
        limit = min(self.config.lookback_days, len(stock_df) - 1, len(sector_df) - 1)
        info: Dict = {}

        # 多周期动量加权
        momentum_score = 0.0
        for period, weight in [(5, 0.5), (10, 0.3), (20, 0.2)]:
            if limit < period:
                continue
            stock_ret = float(stock_df["close"].pct_change(period).iloc[-1])
            sector_ret = float(sector_df["close"].pct_change(period).iloc[-1])
            excess = stock_ret - sector_ret
            # 映射 ±50% → 0~100
            p_score = min(100, max(0, (excess + 0.5) / 1.0 * 100))
            momentum_score += p_score * weight
            info[f"excess_{period}d"] = round(excess, 4)
        info["momentum_score"] = round(momentum_score, 2)

        # 启动领先：首次突破20日高点的相对时间
        try:
            stock_high20 = stock_df["high"].rolling(20).max()
            sector_high20 = sector_df["high"].rolling(20).max()
            stock_break_idx = (
                (stock_high20 > stock_high20.shift(1)).idxmax()
                if (stock_high20 > stock_high20.shift(1)).any()
                else None
            )
            sector_break_idx = (
                (sector_high20 > sector_high20.shift(1)).idxmax()
                if (sector_high20 > sector_high20.shift(1)).any()
                else None
            )
            if stock_break_idx is not None and sector_break_idx is not None:
                lead_days = (sector_break_idx - stock_break_idx).days
                lead_score = min(100, max(0, 100 - lead_days * 10))
            else:
                lead_days = 0
                lead_score = 50
        except Exception:
            lead_days = 0
            lead_score = 50
        info["lead_days"] = int(lead_days)
        info["lead_score"] = round(lead_score, 2)

        # 上涨参与率（板块上涨日个股上涨的比例）
        sector_ret_daily = sector_df["close"].pct_change()
        # 对齐个股和板块的日期索引
        common_idx = stock_df["close"].dropna().index.intersection(sector_ret_daily.dropna().index)
        if len(common_idx) < 5:
            up_participation = 50.0
        else:
            stock_aligned = stock_df.loc[common_idx, "close"].pct_change()
            sector_aligned = sector_ret_daily.loc[common_idx]
            up_days = sector_aligned > 0
            if up_days.sum() > 0:
                up_participation = float((stock_aligned[up_days] > 0).mean() * 100)
            else:
                up_participation = 50.0
        info["up_participation"] = round(up_participation, 2)

        total = momentum_score * 0.5 + lead_score * 0.3 + up_participation * 0.2
        return total, info

    def _calc_defend_score(
        self,
        stock_df: pd.DataFrame,
        sector_df: pd.DataFrame,
    ) -> Tuple[float, Dict]:
        """维度2：抗跌得分（0-100）

        评估股票在市场下跌时的防御能力：
          - 相对回撤比：板块大跌日个股跌幅 / 板块跌幅的比率
          - Beta系数
          - 下跌偏离度
        """
        limit = min(self.config.lookback_days, len(stock_df), len(sector_df))
        info: Dict = {}

        stock_close = stock_df["close"]
        sector_close = sector_df["close"]

        # 相对回撤比（板块大跌日的个股相对跌幅）
        stock_ret_daily = stock_close.pct_change().dropna()
        sector_ret_daily = sector_close.pct_change().dropna()
        aligned = pd.concat([stock_ret_daily, sector_ret_daily], axis=1).dropna()
        aligned.columns = ["stock", "sector"]

        # 定义"大跌日" = 板块日跌幅超过2%
        bad_days = aligned[aligned["sector"] < -0.02]
        info["bad_day_count"] = len(bad_days)
        if len(bad_days) >= 3:
            drop_ratios = bad_days["stock"] / bad_days["sector"]
            avg_drop_ratio = float(drop_ratios.mean())
            # 比率=1: 跟跌一样 → 50分; 比率=0: 完全不跌 → 100分; 比率=2: 跌两倍 → 0分
            drop_ratio_score = max(0, min(100, 50 * (2 - avg_drop_ratio)))
        else:
            avg_drop_ratio = 0.0
            drop_ratio_score = 50.0
        info["avg_drop_ratio"] = round(avg_drop_ratio, 4)
        info["drop_ratio_score"] = round(drop_ratio_score, 2)

        # Beta系数
        import numpy as np

        if len(aligned) > 10:
            beta = float(
                np.cov(aligned["stock"], aligned["sector"])[0, 1]
                / np.var(aligned["sector"])
            )
            beta_score = (1 - min(1, max(0, beta / 2))) * 100
        else:
            beta = 0.0
            beta_score = 50.0
        info["beta"] = round(beta, 4)
        info["beta_score"] = round(beta_score, 2)

        # 下跌偏离度（板块下跌日，个股跑赢/跑输板块的程度）
        down_days = aligned[aligned["sector"] < 0]
        if len(down_days) > 0:
            down_deviation = float((down_days["stock"] - down_days["sector"]).mean())
            deviation_score = min(100, max(0, 100 + down_deviation * 200))
        else:
            down_deviation = 0.0
            deviation_score = 50.0
        info["down_deviation"] = round(down_deviation, 4)
        info["deviation_score"] = round(deviation_score, 2)

        total = drop_ratio_score * 0.4 + beta_score * 0.3 + deviation_score * 0.3
        return total, info

    def _calc_drive_score(
        self,
        stock_df: pd.DataFrame,
        sector_df: pd.DataFrame,
    ) -> Tuple[float, Dict]:
        """维度3：带动板块得分（0-100）

        评估个股对板块的带动能力和影响力。
        """
        period = min(60, len(stock_df) - 1, len(sector_df) - 1)
        info: Dict = {}

        # 成交额占比（反映市场关注度）
        avg_volume_stock = float(stock_df["volume"].iloc[-period:].mean())
        avg_volume_sector = float(sector_df["volume"].iloc[-period:].mean())
        volume_ratio = avg_volume_stock / avg_volume_sector if avg_volume_sector > 0 else 0
        volume_score = min(100, volume_ratio * 100)
        info["volume_ratio"] = round(volume_ratio, 4)
        info["volume_score"] = round(volume_score, 2)

        # 领先效应（个股今日涨幅对明日的板块涨幅相关性）
        stock_ret = stock_df["close"].pct_change()
        sector_ret = sector_df["close"].pct_change()
        if len(stock_ret) > 10:
            lead_corr = float(stock_ret.shift(1).corr(sector_ret))
            corr_score = max(0, min(100, (lead_corr + 0.5) * 100))
        else:
            lead_corr = 0.0
            corr_score = 50.0
        info["lead_correlation"] = round(lead_corr, 4)
        info["corr_score"] = round(corr_score, 2)

        total = volume_score * 0.5 + corr_score * 0.5
        return total, info

    def _calc_strength_score(
        self,
        stock_df: pd.DataFrame,
        fundamental_data: Optional[pd.DataFrame],
        code: str = "",
    ) -> Tuple[float, Dict]:
        """维度4：综合实力得分（0-100）

        评估股票的基本面实力和技术面回报。
        """
        info: Dict = {}

        # 市值/ROE实力
        if fundamental_data is not None and code and code in fundamental_data.index:
            mkt_score = float(fundamental_data.loc[code, "mkt_cap_percentile"]) * 100
            roe_score = float(fundamental_data.loc[code, "roe_percentile"]) * 100
        else:
            mkt_score = 50.0
            roe_score = 50.0
        info["mkt_score"] = round(mkt_score, 2)
        info["roe_score"] = round(roe_score, 2)

        # 技术实力：过去一年总回报率
        if len(stock_df) >= 250:
            tech_ret = float(stock_df["close"].iloc[-1] / stock_df["close"].iloc[-250] - 1)
            tech_score = min(100, max(0, (tech_ret + 0.5) / 1.5 * 100))
        else:
            tech_ret = 0.0
            tech_score = 50.0
        info["tech_return"] = round(tech_ret, 4)
        info["tech_score"] = round(tech_score, 2)

        total = mkt_score * 0.4 + roe_score * 0.4 + tech_score * 0.2
        return total, info
