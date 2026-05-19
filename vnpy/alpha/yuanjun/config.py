from dataclasses import dataclass, field


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

    精简版：去掉无区分度的缩量/放量/不创新低等条件。
    保留+新增有实效的四个条件：
    - 回调幅度（8% → 可调至15%等）
    - 无大阳线/大阴线（6%以上视为不稳定）
    - 窄幅筑底（股价在10日最低价上方不超过阈值）
    - 年线以上运行（站上250日均线，过滤下跌趋势股）
    
    参考 analyze_bottoms.py 历史数据分析结论：
    - 缩量比/放量比：无区分度（差值<0.11）
    - 不创新低天数：无区分度（差值=-1天）
    """

    down_amplitude_min: float = 0.08
    """回调最小幅度（8%）"""
    price_spike_threshold: float = 0.06
    """禁止出现|日涨幅|>6%的K线（大阳线和大阴线均视为不稳定信号）"""
    near_bottom_max_pct: float = 0.02
    """股价在最近10日最低价上方不超过此比例（当前用于确认窄幅筑底，2%）"""
    above_ma250: bool = True
    """是否要求股价站上250日均线（年线）"""
    cooldown_days: int = 15
    """触发后沉默多少个交易日不重复报底"""
    cooldown_interrupt_threshold: float = 0.05
    """冷却期内若幅度扩大超过此阈值，打断冷却重新报底（5%）"""


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
    min_distance_to_stop: float = 0.01
    """距离止损线至少1%才考虑"""
    max_distance_to_stop: float = 0.03
    """距离止损线超过3%则放弃"""
    min_risk_reward_ratio: float = 2.0
    """最小盈亏比2:1"""
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
    max_consecutive_losses: int = 3
    """最大连续亏损次数后暂停"""


@dataclass
class StrategyConfig:
    """策略总配置

    聚合四个子模块的配置，同时包含策略级别的参数。
    所有阈值均可通过构造参数调整，无需修改代码。
    """

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
