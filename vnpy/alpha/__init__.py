from .logger import logger

# Lazy imports to avoid hard dependency on optional packages (e.g., polars)
# These are only needed when using AlphaDataset/AlphaModel/AlphaStrategy/AlphaLab


def __getattr__(name):
    if name == "AlphaDataset":
        from .dataset import AlphaDataset
        return AlphaDataset
    if name == "Segment":
        from .dataset import Segment
        return Segment
    if name == "to_datetime":
        from .dataset import to_datetime
        return to_datetime
    if name == "AlphaModel":
        from .model import AlphaModel
        return AlphaModel
    if name == "AlphaStrategy":
        from .strategy import AlphaStrategy
        return AlphaStrategy
    if name == "BacktestingEngine":
        from .strategy import BacktestingEngine
        return BacktestingEngine
    if name == "AlphaLab":
        from .lab import AlphaLab
        return AlphaLab
    raise AttributeError(f"module 'vnpy.alpha' has no attribute {name!r}")


__all__ = [
    "logger",
    "AlphaDataset",
    "Segment",
    "to_datetime",
    "AlphaModel",
    "AlphaStrategy",
    "BacktestingEngine",
    "AlphaLab"
]
