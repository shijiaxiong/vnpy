"""
模块5：主策略引擎 (ReliefForceAlphaStrategy)

vnpy AlphaStrategy 子类，装配4个业务模块完成完整策略逻辑。
预留 YuanjunDataSource 接口供外部行情数据接入。

数据流：
  on_bars(bars)
    → 累积K线到 bar_cache（历史数据容器）
    → 退出检查（已有持仓）
    → 龙头筛选（模块1）
    → 止跌形态检查（模块2）
    → 入场信号检查（模块3）
    → 仓位计算（模块4）
    → 下单执行

设计要点：
  - 继承 vnpy AlphaStrategy，完全兼容 vnpy 回测流程
  - bar_cache 自动累积每日K线，满足形态识别对历史数据的需求
  - 预留 YuanjunDataSource 抽象接口，可接入实时行情
  - 支持主观干预：手动设置黑/白名单
  - 每次决策存储详情，便于赛后复盘
"""

from abc import ABCMeta, abstractmethod
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from vnpy.alpha import AlphaStrategy
from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData, TradeData

from .bottom_pattern import BottomPatternRecognizer
from .config import StrategyConfig
from .entry_signal import EntrySignalChecker
from .leader_selector import SectorLeaderSelector
from .risk_manager import RiskManager
from .selector import StockSelector, LimitUpSelector, LimitUpConfig, BrokenBoardSelector, BrokenBoardConfig, CompositeSelector


class YuanjunDataSource(metaclass=ABCMeta):
    """援军战法外部数据源接口

    预留扩展位，用于接入真实行情数据。
    实现此接口后注入 ReliefForceAlphaStrategy 即可切换数据源。

    Examples
    --------
    class RealTimeSource(YuanjunDataSource):
        def get_sector_data(self, date: str) -> pd.DataFrame: ...

        def get_fundamental_data(self, date: str) -> pd.DataFrame | None: ...
    """

    @abstractmethod
    def get_sector_data(self, date: str) -> pd.DataFrame:
        """获取板块指数数据"""
        ...

    def get_fundamental_data(self, date: str) -> Optional[pd.DataFrame]:
        """获取基本面数据（可选重写）"""
        return None


_MAX_BARS_HELD: int = 300
"""bar_cache 最大保留天数，避免无限膨胀"""


class ReliefForceAlphaStrategy(AlphaStrategy):
    """援军战法主策略

    继承 AlphaStrategy，在 on_bars() 中完成完整策略逻辑。

    Parameters
    ----------
    strategy_engine : BacktestingEngine
        vnpy 回测引擎
    strategy_name : str
        策略名称
    vt_symbols : list[str]
        交易标的列表
    setting : dict
        配置字典，支持以下键：
        - strategy_config : StrategyConfig — 策略总配置（推荐）
        - 或各子配置键 {leader_config, bottom_config, entry_config, risk_config}

    Attributes
    ----------
    manual_blacklist : list[str]
        手工排除的股票代码（set 后部署）
    manual_whitelist : list[str]
        手工强制纳入的股票代码（set 后部署）
    data_source : YuanjunDataSource | None
        外部数据源（预留扩展位）
    """

    # ================================================================
    # 类属性（可通过 setting 覆盖）
    # ================================================================

    strategy_config: StrategyConfig = StrategyConfig()
    """策略总配置"""

    manual_blacklist: list = []
    """手工排除股票代码列表"""

    manual_whitelist: list = []
    """手工纳入股票代码列表（强制作为龙头候选）"""

    data_source: Optional[YuanjunDataSource] = None
    """外部数据源，接入后可获取板块/基本面数据"""

    # ================================================================
    # 生命周期
    # ================================================================

    def on_init(self) -> None:
        """策略初始化"""

        cfg = self.strategy_config

        # 子模块
        self.selector: StockSelector = self._build_selector(cfg)
        self.pattern_recognizer: BottomPatternRecognizer = BottomPatternRecognizer(cfg.bottom_config)
        self.entry_checker: EntrySignalChecker = EntrySignalChecker(cfg.entry_config)
        self.risk_manager: RiskManager = RiskManager(cfg.risk_config)

        # bar 缓存 {vt_symbol: [BarData, ...]} — 累积历史数据供形态识别
        self.bar_cache: Dict[str, list] = defaultdict(list)

        # 持仓与状态
        self.positions: Dict[str, Dict] = {}
        """{vt_symbol: {entry_price, shares, stop_price, entry_date, target_price, high_since_entry}}"""
        self.holding_days: Dict[str, int] = defaultdict(int)
        """持仓天数"""

        # 日志
        self.decision_log: List[Dict] = []
        """每日决策日志"""
        self.trade_log: List[Dict] = []
        """历史交易记录"""

        self._apply_manual_filters()
        self.write_log("援军战法策略初始化完成")

    # ================================================================
    # on_bars：每日核心入口
    # ================================================================

    def on_bars(self, bars: Dict[str, BarData]) -> None:
        """每日K线回调，执行策略逻辑

        流程：
          1. 累积新K线到 bar_cache
          2. 处理持仓退出（止损/止盈/超期）
          3. 熔断检查
          4. 龙头筛选 → 止跌形态 → 入场信号 → 下单
        """
        today = self._get_trade_date(bars)
        self._update_bar_cache(bars)

        log_entry: Dict = {"date": str(today), "actions": [], "signals": []}

        # ---- 步骤1：处理卖出 ----
        self._process_exits(bars, today, log_entry)

        # ---- 步骤2：熔断检查 ----
        if self.risk_manager.is_paused:
            log_entry["paused"] = True
            log_entry["reason"] = "连亏熔断，暂停交易"
            self.decision_log.append(log_entry)
            return

        # ---- 步骤3：检查尾盘时间 ----
        current_time = today.strftime("%H:%M")
        if not (
            self.strategy_config.entry_config.entry_time_start
            <= current_time
            <= self.strategy_config.entry_config.entry_time_end
        ):
            log_entry["reason"] = f"非尾盘时段（{current_time}），跳过买入"
            self.decision_log.append(log_entry)
            return

        # ---- 步骤4：龙头筛选 ----
        stock_data = self._build_stock_dataframes()
        if not stock_data:
            log_entry["reason"] = "无足够历史K线数据"
            self.decision_log.append(log_entry)
            return

        leaders, leader_details = self._run_leader_selection(stock_data, today)
        log_entry["leader_details"] = leader_details

        if not leaders:
            log_entry["reason"] = "未识别出龙头股"
            self.decision_log.append(log_entry)
            return

        # ---- 步骤5：入场检查 + 下单 ----
        trade_count = 0
        for vt_symbol in leaders:
            if trade_count >= self.strategy_config.max_trades_per_day:
                break
            if vt_symbol in self.positions:
                continue

            bar = bars.get(vt_symbol)
            if bar is None or bar.close_price <= 0:
                continue

            df = stock_data.get(vt_symbol)
            if df is None or len(df) < 20:
                continue

            signal_info = self._evaluate_entry(vt_symbol, df, bar, today)
            log_entry["signals"].append(signal_info)

            if signal_info["decision"] == "buy":
                self.set_target(vt_symbol, signal_info["shares"])
                trade_count += 1

        self.decision_log.append(log_entry)

        # ---- 步骤6：执行下单 ----
        self.execute_trading(bars, price_add=0.0)

    def on_trade(self, trade: TradeData) -> None:
        """交易成交回调"""
        if trade.direction == Direction.SHORT:
            self.holding_days.pop(trade.vt_symbol, None)

    # ================================================================
    # bar_cache 维护
    # ================================================================

    def _update_bar_cache(self, bars: Dict[str, BarData]) -> None:
        """将当前K线追加到缓存中"""
        for vt_symbol, bar in bars.items():
            self.bar_cache[vt_symbol].append(bar)
            # 限制缓存大小
            if len(self.bar_cache[vt_symbol]) > _MAX_BARS_HELD:
                self.bar_cache[vt_symbol] = self.bar_cache[vt_symbol][-_MAX_BARS_HELD:]

    def _build_stock_dataframes(self) -> Dict[str, pd.DataFrame]:
        """将 bar_cache 转换为模块需要的 DataFrame 格式

        Returns
        -------
        Dict[str, pd.DataFrame]
            {vt_symbol: 日线DataFrame}，包含 open/high/low/close/volume/turnover 列
        """
        result: Dict[str, pd.DataFrame] = {}
        for vt_symbol, bars in self.bar_cache.items():
            if len(bars) < 20:
                continue
            rows = []
            for b in bars:
                rows.append({
                    "open": b.open_price,
                    "high": b.high_price,
                    "low": b.low_price,
                    "close": b.close_price,
                    "volume": b.volume,
                    "turnover": b.turnover,
                    "datetime": b.datetime,
                })
            df = pd.DataFrame(rows).set_index("datetime")
            result[vt_symbol] = df
        return result

    # ================================================================
    # 内部：退出检查
    # ================================================================

    def _process_exits(
        self,
        bars: Dict[str, BarData],
        today: datetime,
        log_entry: Dict,
    ) -> None:
        """处理持仓退出（止损 > 止盈 > 超期）"""
        for vt_symbol, pos in list(self.positions.items()):
            bar = bars.get(vt_symbol)
            if bar is None:
                continue

            current_price = bar.close_price
            hold_days = self.holding_days.get(vt_symbol, 0) + 1

            # 更新移动止损所需最高价
            if current_price > pos.get("high_since_entry", pos["entry_price"]):
                pos["high_since_entry"] = current_price

            # 动态止损计算（不使用ATR时只算固定+移动）
            _, stop_info = self.risk_manager.calculate_stop_price(
                entry_price=pos["entry_price"],
                current_high=pos.get("high_since_entry", pos["entry_price"]),
            )
            dynamic_stop = stop_info["final_stop"]

            exit_reason: Optional[str] = None
            if current_price <= dynamic_stop:
                exit_reason = "止损"
            elif current_price >= pos["target_price"]:
                exit_reason = "止盈"
            elif hold_days >= self.strategy_config.max_hold_days:
                exit_reason = "超期平仓"

            if exit_reason:
                is_win = current_price > pos["entry_price"]
                self.set_target(vt_symbol, 0)
                self._record_trade(vt_symbol, pos, current_price, exit_reason, is_win)

                log_entry["actions"].append({
                    "type": "exit",
                    "vt_symbol": vt_symbol,
                    "reason": exit_reason,
                    "entry_price": pos["entry_price"],
                    "exit_price": current_price,
                    "profit_pct": round((current_price - pos["entry_price"]) / pos["entry_price"], 4),
                    "hold_days": hold_days,
                })

            self.holding_days[vt_symbol] = hold_days

    # ================================================================
    # 内部：龙头筛选
    # ================================================================

    def _run_leader_selection(
        self,
        stock_data: Dict[str, pd.DataFrame],
        today: datetime,
    ) -> Tuple[List[str], Dict]:
        """执行股票筛选（通过 StockSelector 统一接口）"""
        date_str = today.strftime("%Y-%m-%d")

        sector_data = self._get_sector_data(date_str)
        fundamental_data = self._get_fundamental_data(date_str)

        return self.selector.select(
            stock_data,
            sector_data=sector_data,
            fundamental_data=fundamental_data,
        )

    # ================================================================
    # 内部：入场评估
    # ================================================================

    def _evaluate_entry(
        self,
        vt_symbol: str,
        df: pd.DataFrame,
        bar: BarData,
        today: datetime,
    ) -> Dict:
        """评估个股入场信号（模块2+模块3+模块4串联）"""
        info: Dict = {"vt_symbol": vt_symbol, "decision": "skip"}

        # 步骤A：止跌形态检查（模块2）
        is_bottom, bottom_info = self.pattern_recognizer.is_bottom_pattern(df)
        info["bottom_pattern"] = bottom_info
        if not is_bottom:
            return info

        # 步骤B：计算止损价格（模块4）
        stop_price, stop_info = self.risk_manager.calculate_stop_price(
            entry_price=bar.close_price,
            current_high=float(df["high"].iloc[-1]),
        )
        info["stop_loss"] = stop_info

        # 步骤C：入场信号检查（模块3）
        current_time = today.strftime("%H:%M")
        can_enter, entry_info = self.entry_checker.can_enter(
            df, stop_price, current_time
        )
        info["entry_signal"] = entry_info
        if not can_enter:
            return info

        # 步骤D：仓位计算（模块4）
        portfolio_value = self.get_portfolio_value()
        shares, pos_info = self.risk_manager.calculate_position_size(
            portfolio_value, bar.close_price, stop_price
        )
        info["position"] = pos_info
        if shares <= 0:
            return info

        # 记录持仓
        self.positions[vt_symbol] = {
            "entry_price": bar.close_price,
            "shares": shares,
            "stop_price": stop_price,
            "entry_date": today.strftime("%Y-%m-%d"),
            "target_price": round(bar.close_price * (1 + self.strategy_config.risk_config.take_profit_pct), 2),
            "high_since_entry": bar.close_price,
        }
        self.holding_days[vt_symbol] = 0

        info.update({
            "decision": "buy",
            "entry_price": bar.close_price,
            "shares": shares,
            "target_price": entry_info.get("target_price"),
            "risk_reward_ratio": entry_info.get("risk_reward_ratio"),
        })
        return info

    # ================================================================
    # 内部：交易记录
    # ================================================================

    def _record_trade(
        self, vt_symbol: str, pos: Dict, exit_price: float, exit_reason: str, is_win: bool
    ) -> None:
        """记录交易并更新风控"""
        pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"]
        self.trade_log.append({
            "vt_symbol": vt_symbol,
            "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "shares": pos["shares"],
            "pnl_pct": round(pnl_pct, 4),
            "is_win": is_win,
            "exit_reason": exit_reason,
        })
        self.risk_manager.update_after_trade(is_win)
        self.positions.pop(vt_symbol, None)

    # ================================================================
    # 筛选器工厂
    # ================================================================

    def _build_selector(self, cfg: StrategyConfig) -> StockSelector:
        """根据配置构建 StockSelector 实例

        cfg.selector_type:
          - "leader"（默认）: SectorLeaderSelector，板块龙头多维打分
          - "limit_up": LimitUpSelector，涨停板个股筛选
          - "broken_board": BrokenBoardSelector，涨停断板个股筛选
          - "composite": CompositeSelector，串联多个筛选器
            （需设置 cfg.selector_chain 为 ["broken_board", "leader"] 等）
        """
        stype = getattr(cfg, "selector_type", "leader")

        if stype == "limit_up":
            lc = getattr(cfg, "limit_up_config", LimitUpConfig())
            return LimitUpSelector(lc)

        if stype == "broken_board":
            bc = getattr(cfg, "broken_board_config", BrokenBoardConfig())
            return BrokenBoardSelector(bc)

        if stype == "composite":
            chain_types = getattr(cfg, "selector_chain", [])
            selectors: List[StockSelector] = []
            for t in chain_types:
                if t == "limit_up":
                    lc = getattr(cfg, "limit_up_config", LimitUpConfig())
                    selectors.append(LimitUpSelector(lc))
                elif t == "broken_board":
                    bc = getattr(cfg, "broken_board_config", BrokenBoardConfig())
                    selectors.append(BrokenBoardSelector(bc))
                elif t == "leader":
                    selectors.append(SectorLeaderSelector(cfg.leader_config))
                else:
                    raise ValueError(f"未知筛选器类型: {t}")
            return CompositeSelector(selectors)

        # 默认：板块龙头筛选（向后兼容）
        return SectorLeaderSelector(cfg.leader_config)

    # ================================================================
    # 数据源
    # ================================================================

    def _get_trade_date(self, bars: Dict[str, BarData]) -> datetime:
        """获取当前交易日"""
        for bar in bars.values():
            return bar.datetime
        return datetime.now()

    def _get_sector_data(self, date_str: str) -> pd.DataFrame:
        """获取板块数据"""
        if self.data_source is not None:
            try:
                return self.data_source.get_sector_data(date_str)
            except Exception:
                pass
        return pd.DataFrame({"close": []})

    def _get_fundamental_data(self, date_str: str) -> Optional[pd.DataFrame]:
        """获取基本面数据"""
        if self.data_source is not None:
            try:
                return self.data_source.get_fundamental_data(date_str)
            except Exception:
                pass
        return None

    # ================================================================
    # 手工干预
    # ================================================================

    def _apply_manual_filters(self) -> None:
        """应用人工黑/白名单"""
        if self.manual_blacklist:
            self.selector.set_blacklist(self.manual_blacklist)
        if self.manual_whitelist:
            self.selector.set_whitelist(self.manual_whitelist)

    def exclude_symbols(self, codes: List[str]) -> None:
        """手工排除标的

        对已跑出的结果，手工剔除"不像龙头"的票。
        已持仓的立即平仓。

        Parameters
        ----------
        codes : List[str]
            需排除的股票代码列表
        """
        existing = list(self.manual_blacklist) if isinstance(self.manual_blacklist, list) else []
        self.manual_blacklist = list(set(existing + codes))
        if hasattr(self, "selector"):
            self.selector.set_blacklist(self.manual_blacklist)
        for code in codes:
            if code in self.positions:
                self.set_target(code, 0)

    def include_symbols(self, codes: List[str]) -> None:
        """手工强制纳入标的

        Parameters
        ----------
        codes : List[str]
            强制纳入的股票代码列表
        """
        existing = list(self.manual_whitelist) if isinstance(self.manual_whitelist, list) else []
        self.manual_whitelist = list(set(existing + codes))
        if hasattr(self, "selector"):
            self.selector.set_whitelist(self.manual_whitelist)

    def connect_data_source(self, source: YuanjunDataSource) -> None:
        """连接外部数据源（预留扩展位）

        可接入实时行情、板块数据等，替代回测数据源。

        Parameters
        ----------
        source : YuanjunDataSource
            实现了抽象接口的外部数据源实例
        """
        self.data_source = source
        self.write_log(f"已连接外部数据源: {source.__class__.__name__}")

    # ================================================================
    # 调试与复盘
    # ================================================================

    def get_decision_log(self) -> List[Dict]:
        """获取完整决策日志"""
        return self.decision_log

    def get_trade_log(self) -> List[Dict]:
        """获取交易记录"""
        return self.trade_log

    def get_risk_status(self) -> Dict:
        """获取当前风控状态"""
        return self.risk_manager.get_status()

    def print_summary(self) -> None:
        """打印策略运行摘要"""
        cfg = self.strategy_config
        print(f"\n{'=' * 55}")
        print(f"  援军战法策略摘要")
        print(f"{'=' * 55}")
        print(f"  筛选类型: {cfg.selector_type}")
        print(f"  筛选详情: {self.selector.__class__.__name__}")
        print(f"  形态识别: 回调≥{cfg.bottom_config.down_amplitude_min:.0%}, |日涨跌|≤{cfg.bottom_config.price_spike_threshold:.0%}, 冷却{cfg.bottom_config.cooldown_days}天, 打断阈值{cfg.bottom_config.cooldown_interrupt_threshold:.0%}")
        print(f"  入场条件: 盈亏比≥{cfg.entry_config.min_risk_reward_ratio}")
        print(f"  风控止损: {cfg.risk_config.stop_loss_pct:.0%}")
        print(f"  连亏熔断: {cfg.risk_config.max_consecutive_losses}次")
        print(f"  ──────────────────────")
        print(f"  交易次数: {self.risk_manager.trade_count}")
        print(f"  胜率:     {self.risk_manager.get_status()['win_rate']:.1%}")
        print(f"  当前持仓: {list(self.positions.keys())}")
        print(f"{'=' * 55}")
