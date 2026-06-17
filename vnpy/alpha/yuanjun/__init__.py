from .config import (
    LeaderConfig,
    BottomPatternConfig,
    EntryConfig,
    RiskConfig,
    StrategyConfig,
    LimitUpConfig,
    BrokenBoardConfig,
)

from .selector import (
    StockSelector,
    LimitUpSelector,
    BrokenBoardSelector,
    CompositeSelector,
)

from .leader_selector import SectorLeaderSelector
from .bottom_pattern import BottomPatternRecognizer
from .entry_signal import EntrySignalChecker
from .risk_manager import RiskManager
from .backtest import (
    BacktestEvaluator,
    TradeRecord,
    PerformanceResult,
)

# Lazy import: ReliefForceAlphaStrategy depends on vnpy.alpha.AlphaStrategy
# which pulls in dataset/polars. Only load when actually needed.
_LAZY_STRATEGY = None


def _get_strategy():
    global _LAZY_STRATEGY
    if _LAZY_STRATEGY is None:
        from .strategy import (  # noqa: F811
            ReliefForceAlphaStrategy,
            YuanjunDataSource,
        )
        _LAZY_STRATEGY = ReliefForceAlphaStrategy, YuanjunDataSource
    return _LAZY_STRATEGY


def __getattr__(name):
    if name in ("ReliefForceAlphaStrategy", "YuanjunDataSource"):
        idx = 0 if name == "ReliefForceAlphaStrategy" else 1
        return _get_strategy()[idx]
    raise AttributeError(f"module 'vnpy.alpha.yuanjun' has no attribute {name!r}")


__all__ = [
    # Config
    "LeaderConfig",
    "BottomPatternConfig",
    "EntryConfig",
    "RiskConfig",
    "StrategyConfig",
    "LimitUpConfig",
    "BrokenBoardConfig",
    # Selector interface
    "StockSelector",
    "LimitUpSelector",
    "BrokenBoardSelector",
    "CompositeSelector",
    # Module 1
    "SectorLeaderSelector",
    # Module 2
    "BottomPatternRecognizer",
    # Module 3
    "EntrySignalChecker",
    # Module 4
    "RiskManager",
    # Module 5
    "ReliefForceAlphaStrategy",
    "YuanjunDataSource",
    # Module 6
    "BacktestEvaluator",
    "TradeRecord",
    "PerformanceResult",
]
