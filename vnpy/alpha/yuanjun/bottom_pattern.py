"""
模块2：形态识别模块 (BottomPatternRecognizer)

止跌形态识别（四条件版）。

四个条件：
1. 回调幅度 ≥ 阈值（默认8%，可调至15%）
2. 股价站上250日均线（年线，过滤下跌趋势股）
3. MACD BAR 连续回升（空头力量减弱确认，可通过 enable_macd_convergence 关闭）
4. 子板块相对强度排名前50%（可通过 enable_rel_strength_filter 关闭）

已移除条件：
- 近3日价格平稳（|日涨幅| ≤ 6%）：断板日本身波动大，前3日限制无意义
- 窄幅筑底（股价在撤军线上方 ≤ 5%）：回调足够深即可，不要求紧贴止损线
"""

from typing import Dict, Tuple

import pandas as pd

from .config import BottomPatternConfig


class BottomPatternRecognizer:
    """止跌形态识别器（四条件版）

    逐条检查四个条件：
    1. 回调幅度 ≥ 阈值
    2. 股价站上250日均线（年线，过滤下跌趋势股）
    3. MACD BAR 连续回升（空头力量减弱确认）
    4. 子板块相对强度排名前50%（过滤弱势股）
    """

    def __init__(self, config: BottomPatternConfig) -> None:
        self.config = config
        # 改用日期追踪而非行号，避免每日切片长度变化导致冷却计算错误
        self._last_triggered_date: pd.Timestamp = pd.NaT  # 上次触发日
        self._last_triggered_amplitude: float = 0.0  # 触发时的回调幅度，用于冷却打断判断

    def reset(self) -> None:
        """重置冷却期状态，换股票池时调用"""
        self._last_triggered_date = pd.NaT
        self._last_triggered_amplitude = 0.0

    def is_bottom_pattern(
        self, df: pd.DataFrame, stop_loss_price: float = 0.0,
        rel_strength_rank_pct: float = 0.0,
    ) -> Tuple[bool, Dict]:
        """判断是否形成止跌形态

        采用"事件模式"：每个回调周期只报最早触发日一次，
        之后进入冷却期（默认15个交易日），冷却期满后再报下一周期。

        Parameters
        ----------
        df : pd.DataFrame
            个股日线数据，需包含 open/high/low/close/volume 列
        stop_loss_price : float, optional
            撤军线（止损线），用于窄幅筑底检查

        Returns
        -------
        Tuple[bool, Dict]
            (是否满足止跌形态, 详细信息)
        """
        result: Dict = {}

        if len(df) < 20:
            return False, {"error": f"数据不足20天（仅{len(df)}天）"}

        # 冷却期检查（仅 enable_cooldown=True 时生效）
        cooldown_interrupted = False
        interrupt_reason = ""
        if self.config.enable_cooldown and pd.notna(self._last_triggered_date):
            current_date = df.index[-1]  # DataFrame 最后一行对应的日期
            # 在当前 DataFrame 中查找上次触发日的行号
            triggered_positions = df.index.get_indexer([self._last_triggered_date], method="pad")
            if triggered_positions[0] >= 0:
                bars_since_trigger = len(df) - 1 - triggered_positions[0]
                if bars_since_trigger < self.config.cooldown_days:
                    # 计算当前幅度，看是否需要打断冷却
                    current_amp, _ = self._calc_down_amplitude(df)
                    amplitude_expansion = current_amp - self._last_triggered_amplitude
                    if amplitude_expansion >= self.config.cooldown_interrupt_threshold:
                        cooldown_interrupted = True
                        interrupt_reason = (
                            f"幅度扩大{amplitude_expansion:.2%}（触发时{self._last_triggered_amplitude:.2%}→当前{current_amp:.2%}），打断冷却"
                        )
                    else:
                        return False, {
                            "cooldown": True,
                            "bars_remaining": self.config.cooldown_days - bars_since_trigger,
                            "reason": (
                                f"冷却期内，还有 {self.config.cooldown_days - bars_since_trigger} 天"
                            ),
                        }

        # 条件1：回调幅度达标（≥阈值）
        passed, amp_info = self._check_down_amplitude(df)

        # 涨停次日过滤：近2天内有涨停的不买（涨停反弹已兑现，断板是追高）
        has_recent_limit = self._has_limit_up_in_recent(df, days=2)
        if has_recent_limit:
            result.update(amp_info)
            result["reason"] = "近2天内有涨停，反弹已兑现，不追"
            return False, result

        result.update(amp_info)
        if not passed:
            return False, result

        # 条件2：股价站上250日均线（年线，过滤下跌趋势股）
        if self.config.above_ma250:
            passed, ma250_info = self._check_above_ma250(df)
            result.update(ma250_info)
            if not passed:
                return False, result
        else:
            result["ma250"] = "disabled"

        # 条件3：MACD BAR收敛（空头力量减弱确认）
        passed, macd_info = self._check_macd_convergence(df)
        result.update(macd_info)
        if not passed:
            return False, result

        # 条件4：子板块相对强度（过滤排名后50%的弱势股）
        passed, rank_info = self._check_relative_strength(rel_strength_rank_pct)
        result.update(rank_info)
        if not passed:
            return False, result

        # 所有条件均满足 → 触发，记录触发日期和幅度，进入冷却期
        result["is_bottom"] = True
        if cooldown_interrupted:
            result["cooldown_interrupted"] = True
            result["interrupt_reason"] = interrupt_reason
        self._last_triggered_date = df.index[-1]
        self._last_triggered_amplitude = amp_info["down_amplitude"]
        return True, result

    # ------------------------------------------------------------------
    # 条件1：回调幅度检查
    # ------------------------------------------------------------------

    def _calc_down_amplitude(self, df: pd.DataFrame) -> Tuple[float, float]:
        """纯计算：返回 (amplitude, period_high)

        period_high 从至少5天前的K线中取（不含最近5天和当天），
        避免把上升趋势中的新高误判为"从高点回调"。
        例如：股票持续上涨时，period_high 取自较远日期，period_low 取自近5日，
        近5日低点反而高于远日高点，amplitude 为负，不满足回调条件。

        供冷却打断判断使用，不含任何阈值判断逻辑。
        """
        lookback = 10
        lookback_start = 2  # 排除昨天（通常是涨停日），拿前天及之前的最高点
        recent = df.tail(lookback)

        if len(recent) <= lookback_start:
            # 数据不足，用全量
            period_high = recent["high"].max()
        else:
            period_high = recent.iloc[:-lookback_start]["high"].max()

        period_low = df["low"].iloc[-5:].min()
        amplitude = (period_high - period_low) / period_high if period_high > 0 else 0
        return amplitude, period_high

    def _check_down_amplitude(self, df: pd.DataFrame) -> Tuple[bool, Dict]:
        """计算最近一波下跌幅度，找最近N天内高点到低点，计算回调幅度。

        要求当前价必须低于 period_high（确认处于回调中，而非上升趋势），
        避免把持续上涨的股票误判为"从高点回调"。

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"down_amplitude": float})
        """
        amplitude, period_high = self._calc_down_amplitude(df)
        current_price = float(df["close"].iloc[-1])

        info = {"down_amplitude": round(amplitude, 4), "period_high": round(period_high, 2)}

        # 当前价必须低于 period_high（排除昨天涨停日），确保处于回调而非上升趋势
        if current_price >= period_high:
            info["reason"] = (
                f"当前价{current_price:.2f} ≥ period_high{period_high:.2f}，处于上升趋势非回调"
            )
            return False, info

        passed = amplitude >= self.config.down_amplitude_min
        if not passed:
            info["reason"] = (
                f"回调幅度{amplitude:.2%} < 阈值{self.config.down_amplitude_min:.2%}"
            )
        return passed, info

    # ------------------------------------------------------------------
    # 涨停次日过滤
    # ------------------------------------------------------------------

    def _has_limit_up_in_recent(self, df: pd.DataFrame, days: int = 2) -> bool:
        """检查近N天内（不含当日）是否有涨停（≥9.5%）

        涨停代表反弹已基本兑现。只有当日收盘价仍高于涨停日收盘价时才拦截
        （说明还在追高），已经跌回涨停线下方的可以放行。
        """
        if len(df) < days + 1:
            return False
        recent = df.iloc[-(days + 1):-1]  # 不含当天
        current_close = float(df["close"].iloc[-1])
        for i in range(1, len(recent)):
            prev_close = float(recent["close"].iloc[i - 1])
            cur_close = float(recent["close"].iloc[i])
            if prev_close > 0 and (cur_close - prev_close) / prev_close >= 0.095:
                # 涨停了，检查当前价是否仍在涨停收盘价之上
                if current_close > cur_close:
                    return True
        return False

    # ------------------------------------------------------------------
    # 条件2：价格平稳性检查（大阳线 + 大阴线）
    # ------------------------------------------------------------------

    def _check_stable_price(self, df: pd.DataFrame) -> Tuple[bool, Dict]:
        """检查最近3日内价格是否平稳

        同时排除大阳线（>6%）和大阴线（<-6%），
        两者都是底部不稳定的信号。

        注意：跳过最新日（当前交易日），因为断板日本身就可能有大波动，检查前3日即可。

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"has_price_spike": bool, "spike_max_abs_pct": float,
                        "spike_type": str})
        """
        recent = df.iloc[-4:-1] if len(df) >= 4 else df.iloc[:-1]
        max_abs_pct = 0.0
        spike_type = ""  # "" | "yang" | "yin"

        for i in range(1, len(recent)):
            prev_close = recent["close"].iloc[i - 1]
            cur_close = recent["close"].iloc[i]
            if prev_close > 0:
                pct = (cur_close - prev_close) / prev_close
                abs_pct = abs(pct)
                if abs_pct > max_abs_pct:
                    max_abs_pct = abs_pct
                    if pct > 0:
                        spike_type = "yang"
                    else:
                        spike_type = "yin"

        has_spike = max_abs_pct > self.config.price_spike_threshold
        info = {
            "has_price_spike": has_spike,
            "spike_max_abs_pct": round(max_abs_pct, 4),
            "spike_type": spike_type if has_spike else "",
        }
        passed = not has_spike
        if not passed:
            label = "大阳线" if spike_type == "yang" else "大阴线"
        return passed, info

    # ------------------------------------------------------------------
    # 条件4：MACD BAR收敛检查（空头力量减弱确认）
    # ------------------------------------------------------------------

    def _check_macd_convergence(self, df: pd.DataFrame) -> Tuple[bool, Dict]:
        """检查MACD BAR是否连续回升（空头力量在减弱）

        要求：MACD BAR连续N日不创新低且在回升。
        BAR仍为负值（空头主导），但方向已转向多头。

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"macd_bar": float, "macd_bar_converging": bool, ...})
        """
        if not self.config.enable_macd_convergence:
            return True, {"macd_convergence": "disabled"}

        if len(df) < 30:
            return False, {"reason": f"数据不足30天（仅{len(df)}天），无法计算MACD"}

        close = df["close"].values
        # EMA12
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        dif = ema12 - ema26
        dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
        bar = 2 * (dif - dea)  # MACD柱

        current_bar = float(bar[-1])
        info = {
            "macd_bar": round(current_bar, 4),
            "macd_dif": round(float(dif[-1]), 4),
            "macd_dea": round(float(dea[-1]), 4),
        }

        # 检查连续N日BAR回升
        if len(bar) < self.config.macd_convergence_days + 1:
            return False, {"reason": "MACD数据不足，无法判断收敛", **info}

        converging = True
        for i in range(self.config.macd_convergence_days):
            if bar[-(i + 1)] <= bar[-(i + 2)]:
                converging = False
                break

        info["macd_bar_converging"] = converging
        if not converging:
            info["reason"] = (
                f"MACD BAR连续{self.config.macd_convergence_days}日未回升，空头仍未衰竭"
            )
            return False, info

        return True, info

    # ------------------------------------------------------------------
    # 条件5：子板块相对强度检查（过滤弱势股）
    # ------------------------------------------------------------------

    def _check_relative_strength(self, rank_pct: float) -> Tuple[bool, Dict]:
        """检查个股在子板块内的排名是否在前N%

        Parameters
        ----------
        rank_pct : float
            排名百分位（0.0=第1名, 1.0=最后一名），由调用方传入

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"rel_strength_rank_pct": float, ...})
        """
        if not self.config.enable_rel_strength_filter:
            return True, {"rel_strength_filter": "disabled"}

        if rank_pct <= 0:
            return True, {"rel_strength_filter": "no_peers", "rel_strength_rank_pct": 0.0}

        info = {"rel_strength_rank_pct": round(rank_pct, 4)}
        passed = rank_pct <= self.config.rel_strength_top_pct

        if not passed:
            info["reason"] = (
                f"子板块排名{rank_pct:.0%} > 阈值{self.config.rel_strength_top_pct:.0%}"
            )
        return passed, info

    # ------------------------------------------------------------------
    # 条件3：窄幅筑底检查
    # ------------------------------------------------------------------

    def _check_near_bottom(self, df: pd.DataFrame, stop_loss_price: float = 0.0) -> Tuple[bool, Dict]:
        """检查股价是否在撤军线（止损线）附近（窄幅区间内）

        股价在撤军线上方不超过撤军线*near_bottom_max_pct%，
        确认当前价格已接近止损位，处于底部边缘。

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"stop_loss_price": float, "distance_from_stop": float, ...})
        """
        if stop_loss_price <= 0:
            return False, {"reason": "撤军线价格为0，无法检查"}

        current_price = float(df["close"].iloc[-1])
        distance = (current_price - stop_loss_price) / stop_loss_price

        info = {
            "stop_loss_price": round(stop_loss_price, 2),
            "distance_from_stop": round(distance, 4),
        }

        if distance < 0:
            info["reason"] = f"当前价{current_price:.2f}已跌破撤军线{stop_loss_price:.2f}"
            return False, info

        if distance > self.config.near_bottom_max_pct:
            info["reason"] = (
                f"距撤军线{distance:.2%} > 阈值{self.config.near_bottom_max_pct:.0%}，离止损太远"
            )
            return False, info

        return True, info

    # ------------------------------------------------------------------
    # 条件4：年线以上检查
    # ------------------------------------------------------------------

    def _check_above_ma250(self, df: pd.DataFrame) -> Tuple[bool, Dict]:
        """检查股价是否在250日均线上方运行

        过滤处于长期下跌趋势的股票。

        Returns
        -------
        Tuple[bool, Dict]
            (是否通过, {"ma250": float, "above_ma250": bool, ...})
        """
        if len(df) < 250:
            return False, {"reason": f"数据不足250天（仅{len(df)}天），无法计算250日均线"}

        ma250 = float(df["close"].rolling(250).mean().iloc[-1])
        current_price = float(df["close"].iloc[-1])

        info = {"ma250": round(ma250, 2), "above_ma250": current_price > ma250}
        passed = current_price > ma250
        if not passed:
            info["reason"] = f"当前价{current_price:.2f} < 250日均线{ma250:.2f}"
        return passed, info
