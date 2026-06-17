from dataclasses import dataclass, field
from typing import List, Optional

from .selector import LimitUpConfig, BrokenBoardConfig


# ================================================================
# 援军分类预设
# ================================================================

_YUANJUN_STYLES: dict = {
    "band": {
        "selector_type": "leader",
        "comment": "波段援军：板块龙头回调止跌后波段介入",
    },
    "leader": {
        "selector_type": "broken_board",
        "comment": "龙头援军：连板后断板个股回调止跌介入",
    },
}
"""援军战法风格预设。band=波段援军(原版), leader=龙头援军(新版)"""


@dataclass
class LeaderConfig:
    """龙头筛选配置参数

    控制龙头筛选模块的筛选标准和打分权重。
    所有阈值均可通过构造参数调整，无需修改代码。
    """

    # 基础筛选
    lookback_days: int = 60
    """回顾周期（天）"""
    top_n: int = 3
    """取前N名为龙头候选"""
    min_market_cap_pct: float = 0.7
    """市值至少板块内前70%"""
    min_roe_pct: float = 0.6
    """ROE至少板块内前60%"""
    max_beta: float = 1.5
    """Beta系数上限"""
    min_turnover_pct: float = 3.0
    """最小换手率（%）"""
    price_above_ma250: bool = True
    """是否要求站上年线"""
    max_distance_to_ma250: float = 0.15
    """离年线最大偏离度（15%）"""

    # 冷却机制
    cooldown_consecutive: int = 2
    """连续当选N天后触发冷却"""
    cooldown_days: int = 5
    """冷却M个交易日"""

    # 可选因子
    enable_flow_adjustment: bool = False
    """是否启用资金流向评分调整（默认关闭，回测不生效）"""
    flow_adjustment_max: float = 10.0
    """资金流向调整最大幅度（±分）"""

    # 维度权重
    weight_lead: float = 0.30
    """领涨维度权重"""
    weight_defend: float = 0.10
    """抗跌维度权重"""
    weight_drive: float = 0.30
    """带动板块维度权重"""
    weight_strength: float = 0.30
    """综合实力维度权重"""


@dataclass
class BottomPatternConfig:
    """止跌形态识别配置参数

    当前生效条件（四条件）：
    - 回调幅度（8% → 可调至15%等）
    - 年线以上运行（站上250日均线，过滤下跌趋势股）
    - MACD BAR收敛（空头力量减弱确认）
    - 子板块相对强度（排名前50%）

    已移除条件（配置保留，底部识别中不再检查）：
    - price_spike_threshold（近3日无剧烈波动）：断板日本身波动大，前3日限制无意义
    - near_bottom_max_pct（窄幅筑底）：回调足够深即可，不要求紧贴止损线
    """

    down_amplitude_min: float = 0.08
    """回调最小幅度（8%）"""
    use_post_board_peak: bool = True
    """是否使用断板后阶段性顶部计算回调幅度。

    True（默认）：找到最后一个涨停日→断板日→找出此后不再创新高的阶段性顶部，
    从该顶部计算回落幅度。更精确地定位回调深度。

    False：使用旧版滚动窗口法（近10天高点配合近5天低点），适合波段援军。
    """
    price_spike_threshold: float = 0.06
    """[已移除] 禁止出现|日涨幅|>6%的K线"""
    near_bottom_max_pct: float = 0.05
    """[已移除] 股价在最近10日最低价上方不超过此比例"""
    above_ma250: bool = True
    """是否要求股价站上250日均线（年线）"""
    enable_cooldown: bool = False
    """是否启用冷却期，默认False。关闭后每日都可触发，不会因上次触发而沉默"""
    cooldown_days: int = 15
    """[仅enable_cooldown=True时生效] 触发后沉默多少个交易日不重复报底"""
    cooldown_interrupt_threshold: float = 0.05
    """[仅enable_cooldown=True时生效] 冷却期内若幅度扩大超过此阈值，打断冷却重新报底"""
    enable_macd_convergence: bool = False
    """是否启用MACD BAR收敛检查（空头力量减弱确认），默认关闭"""
    macd_convergence_days: int = 2
    """MACD BAR连续回升天数（要求BAR连续N日不创新低且回升）"""
    enable_rel_strength_filter: bool = True
    """是否启用子板块相对强度过滤（过滤排名后50%）"""
    rel_strength_top_pct: float = 0.5
    """子板块内排名前N%才通过（0.5=前50%）"""
    enable_no_new_low: bool = True
    """是否要求入场日不再创新低。

    True（默认）：断板日后，只有当今日最低价 ≥ 涨停日以来最低价时才算止跌确认。
    防止在断板日后股价还在持续探底时接飞刀。
    """


@dataclass
class EntryConfig:
    """入场时机配置参数

    控制入场信号检查的条件。
    所有阈值均可通过构造参数调整，无需修改代码。
    """

    entry_time_start: str = "14:40"
    """开始买入时间"""
    entry_time_end: str = "14:55"
    """结束买入时间"""
    min_distance_to_stop: float = 0.0
    """距离止损线最低0%（紧贴撤军线也可入场）"""
    max_distance_to_stop: float = 0.02
    """距离止损线超过2%则放弃"""
    min_risk_reward_ratio: float = 2.0
    """[已移除] 最小盈亏比，入场检查中不再使用"""
    target_resistance_lookback: int = 60
    """阻力位回顾周期（天）"""


@dataclass
class RiskConfig:
    """风控管理配置参数

    控制仓位计算、止损策略和熔断机制。
    所有阈值均可通过构造参数调整，无需修改代码。
    """

    stop_loss_pct: float = 0.02
    """固定止损比例（2%）"""
    max_loss_per_trade: float = 0.02
    """单笔最大亏损占总资金比例（2%）"""
    trailing_stop_pct: float = 0.03
    """移动止损回撤比例（3%）"""
    atr_multiplier: float = 2.0
    """ATR止损倍数"""
    take_profit_pct: float = 0.07
    """止盈比例（7%），买入价上涨超过此比例考虑止盈"""
    max_consecutive_losses: int = 3
    """最大连续亏损次数后暂停"""


@dataclass
class StrategyConfig:
    """策略总配置

    聚合子模块配置和策略级别参数。
    支持通过 strategy_style 快速切换援军分类。

    strategy_style:
      - "band"（默认）: 波段援军 — 板块龙头回调止跌后波段介入，selector=leader
      - "leader": 龙头援军 — 连板后断板回调止跌介入，selector=broken_board
    """

    # 援军分类
    strategy_style: str = "band"
    """援军分类: "band"=波段援军(原版) | "leader"=龙头援军(新版)"""

    # 子模块配置

    leader_config: LeaderConfig = field(default_factory=LeaderConfig)
    """龙头筛选配置"""
    bottom_config: BottomPatternConfig = field(default_factory=BottomPatternConfig)
    """形态识别配置"""
    entry_config: EntryConfig = field(default_factory=EntryConfig)
    """入场时机配置"""
    risk_config: RiskConfig = field(default_factory=RiskConfig)
    """风控管理配置"""
    max_hold_days: int = 10
    """最大持仓天数"""
    max_trades_per_day: int = 3
    """每日最多交易次数"""
    selector_type: str = "leader"
    """筛选器类型: "leader" | "limit_up" | "broken_board" | "composite" """
    limit_up_config: LimitUpConfig = field(default_factory=LimitUpConfig)
    """涨停筛选配置（selector_type="limit_up" 或 chain 中包含 "limit_up" 时生效）"""
    broken_board_config: BrokenBoardConfig = field(default_factory=BrokenBoardConfig)
    """断板筛选配置（selector_type="broken_board" 或 chain 中包含 "broken_board" 时生效）"""
    selector_chain: List[str] = field(default_factory=list)
    """串联筛选器链配置（selector_type="composite" 时生效），如 ["broken_board", "leader"]"""

    # ------------------------------------------------------------------
    # 工厂方法与风格预设
    # ------------------------------------------------------------------

    @classmethod
    def band_style(cls, **overrides) -> "StrategyConfig":
        """波段援军预设（原版）

        板块龙头多维打分 → 止跌形态 → 尾盘入场。
        适合中低频波段交易，持仓天数较长。
        """
        cfg = cls(strategy_style="band", selector_type="leader")
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    @classmethod
    def leader_style(cls, **overrides) -> "StrategyConfig":
        """龙头援军预设（新版）

        连板后断板筛选 → 止跌形态 → 尾盘入场。
        适合短线追涨，持仓天数较短。
        """
        cfg = cls(
            strategy_style="leader",
            selector_type="broken_board",
            max_hold_days=5,
            max_trades_per_day=4,
        )
        cfg.risk_config.stop_loss_pct = 0.05  # 龙头援军止损放宽至5%
        cfg.risk_config.max_consecutive_losses = 0  # 0=禁用连亏熔断
        cfg.entry_config.max_distance_to_stop = 0.03  # 距止损线≤3%才入场
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg
