"""
模块6：回测评估模块 (BacktestEvaluator)

对回测结果进行绩效分析和报告生成。
不包含回测引擎本身（复用 vnpy BacktestingEngine），
而是提供回测结果的数据分析、指标计算和可视化能力。

设计要点：
  - 独立模块，可单独计算任意交易记录的绩效
  - 输出完整绩效报告（收益/风险/交易/风控四类指标）
  - 净值曲线和交易信号可视化
  - 支持多组参数对比
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class TradeRecord:
    """单笔交易记录"""

    vt_symbol: str = ""
    """股票代码"""
    entry_date: str = ""
    """入场日期"""
    exit_date: str = ""
    """出场日期"""
    entry_price: float = 0.0
    """入场价格"""
    exit_price: float = 0.0
    """出场价格"""
    shares: int = 0
    """交易数量"""
    pnl: float = 0.0
    """盈亏金额"""
    pnl_pct: float = 0.0
    """盈亏比例"""
    is_win: bool = False
    """是否盈利"""
    exit_reason: str = ""
    """出场原因（止损/止盈/超期）"""
    hold_days: int = 0
    """持仓天数"""
    cooldown_interrupted: bool = False
    """入场是否由冷却打断触发（幅度扩大打断冷却后触发止跌形态）"""


@dataclass
class PerformanceResult:
    """绩效分析结果"""

    # 基础信息
    total_trades: int = 0
    """总交易次数"""
    start_date: str = ""
    """回测开始日"""
    end_date: str = ""
    """回测结束日"""
    initial_capital: float = 1_000_000
    """初始资金"""

    # 收益指标
    total_return: float = 0.0
    """总收益率"""
    annual_return: float = 0.0
    """年化收益率"""
    cumulative_return: float = 0.0
    """累计收益率"""

    # 风险指标
    max_drawdown: float = 0.0
    """最大回撤率"""
    sharpe_ratio: float = 0.0
    """夏普比率"""
    calmar_ratio: float = 0.0
    """卡玛比率"""
    volatility: float = 0.0
    """年化波动率"""

    # 交易指标
    win_rate: float = 0.0
    """胜率"""
    avg_profit: float = 0.0
    """平均盈利"""
    avg_loss: float = 0.0
    """平均亏损"""
    profit_loss_ratio: float = 0.0
    """盈亏比"""
    avg_hold_days: float = 0.0
    """平均持仓天数"""
    max_consecutive_wins: int = 0
    """最大连续盈利次数"""
    max_consecutive_losses: int = 0
    """最大连续亏损次数"""

    # 风控指标
    max_loss_per_trade: float = 0.0
    """单笔最大亏损"""
    max_loss_percent: float = 0.0
    """单笔最大亏损比例"""

    # 详细数据
    equity_curve: Optional[pd.DataFrame] = None
    """净值曲线"""
    trade_records: List[TradeRecord] = field(default_factory=list)
    """交易明细"""
    monthly_returns: Optional[pd.DataFrame] = None
    """月度收益率"""


class BacktestEvaluator:
    """回测绩效评估器

    对交易记录和净值曲线进行全面的绩效分析。

    Parameters
    ----------
    initial_capital : float, optional
        初始资金，默认 1,000,000
    """

    def __init__(self, initial_capital: float = 1_000_000):
        self.initial_capital = initial_capital

    def evaluate(
        self,
        equity_curve: pd.DataFrame,
        trade_records: List[TradeRecord],
        risk_free_rate: float = 0.02,
        annual_days: int = 240,
    ) -> PerformanceResult:
        """对回测结果进行完整绩效评估

        Parameters
        ----------
        equity_curve : pd.DataFrame
            净值曲线，需包含 date 和 total_value 列
        trade_records : List[TradeRecord]
            交易记录列表
        risk_free_rate : float, optional
            无风险利率，默认2%
        annual_days : int, optional
            年化交易天数，默认240

        Returns
        -------
        PerformanceResult
            绩效分析结果
        """
        result = PerformanceResult(
            initial_capital=self.initial_capital,
            trade_records=trade_records,
        )

        if equity_curve is None or equity_curve.empty:
            return result

        # ---- 基础信息 ----
        result.equity_curve = equity_curve
        result.total_trades = len(trade_records)
        result.start_date = str(equity_curve["date"].iloc[0].date())
        result.end_date = str(equity_curve["date"].iloc[-1].date())

        # ---- 收益指标计算 ----
        self._calc_return_metrics(result, risk_free_rate, annual_days)

        # ---- 风险指标计算 ----
        self._calc_risk_metrics(result, risk_free_rate, annual_days)

        # ---- 交易指标计算 ----
        self._calc_trade_metrics(result)

        # ---- 风控指标 ----
        self._calc_risk_control_metrics(result)

        return result

    # ------------------------------------------------------------------
    # 收益指标
    # ------------------------------------------------------------------

    def _calc_return_metrics(
        self,
        result: PerformanceResult,
        risk_free_rate: float,
        annual_days: int,
    ) -> None:
        """计算收益类指标"""
        eq = result.equity_curve
        initial = result.initial_capital

        # 累计收益
        final_value = float(eq["total_value"].iloc[-1])
        result.cumulative_return = (final_value - initial) / initial

        # 日收益率序列
        eq["daily_return"] = eq["total_value"].pct_change()
        daily_returns = eq["daily_return"].dropna()

        # 年化收益
        total_days = len(daily_returns)
        if total_days > 0:
            result.annual_return = (1 + result.cumulative_return) ** (annual_days / total_days) - 1

        result.total_return = result.cumulative_return

        # 月度收益率
        eq["month"] = pd.to_datetime(eq["date"]).dt.to_period("M")
        monthly_ret = eq.groupby("month")["daily_return"].apply(
            lambda x: (1 + x).prod() - 1
        )
        result.monthly_returns = monthly_ret.reset_index()
        result.monthly_returns.columns = ["month", "return"]

    # ------------------------------------------------------------------
    # 风险指标
    # ------------------------------------------------------------------

    def _calc_risk_metrics(
        self,
        result: PerformanceResult,
        risk_free_rate: float,
        annual_days: int,
    ) -> None:
        """计算风险类指标"""
        eq = result.equity_curve
        daily_returns = eq["daily_return"].dropna()

        if len(daily_returns) < 2:
            return

        # 最大回撤
        cumulative = (1 + daily_returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        result.max_drawdown = float(drawdown.min())

        # 年化波动率
        daily_vol = float(daily_returns.std())
        result.volatility = daily_vol * np.sqrt(annual_days)

        # 夏普比率
        daily_rf = risk_free_rate / annual_days
        excess_returns = daily_returns - daily_rf
        if daily_vol > 0:
            result.sharpe_ratio = float(
                excess_returns.mean() / daily_vol * np.sqrt(annual_days)
            )

        # 卡玛比率
        if result.max_drawdown < 0:
            result.calmar_ratio = result.annual_return / abs(result.max_drawdown)
        else:
            result.calmar_ratio = 0.0

    # ------------------------------------------------------------------
    # 交易指标
    # ------------------------------------------------------------------

    def _calc_trade_metrics(self, result: PerformanceResult) -> None:
        """计算交易类指标"""
        trades = result.trade_records
        if not trades:
            return

        pnl_pcts = [t.pnl_pct for t in trades]
        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]

        # 胜率
        result.win_rate = len(wins) / len(trades) if trades else 0.0

        # 平均盈亏
        result.avg_profit = np.mean([t.pnl_pct for t in wins]) if wins else 0.0
        result.avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0.0

        # 盈亏比
        avg_win = result.avg_profit if wins else 0
        avg_loss = abs(result.avg_loss) if losses else 1
        result.profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        # 平均持仓天数
        result.avg_hold_days = np.mean([t.hold_days for t in trades]) if trades else 0.0

        # 最大连续盈利/亏损
        result.max_consecutive_wins = self._calc_max_consecutive(trades, True)
        result.max_consecutive_losses = self._calc_max_consecutive(trades, False)

    def _calc_max_consecutive(self, trades: List[TradeRecord], is_win: bool) -> int:
        """计算最大连续盈利/亏损次数"""
        max_count = 0
        current_count = 0
        for t in trades:
            if t.is_win == is_win:
                current_count += 1
                max_count = max(max_count, current_count)
            else:
                current_count = 0
        return max_count

    # ------------------------------------------------------------------
    # 风控指标
    # ------------------------------------------------------------------

    def _calc_risk_control_metrics(self, result: PerformanceResult) -> None:
        """计算风控类指标"""
        trades = result.trade_records
        if not trades:
            return

        pnl_pcts = [t.pnl_pct for t in trades]
        pnl_values = [t.pnl for t in trades]

        result.max_loss_per_trade = min(pnl_values) if pnl_values else 0.0
        result.max_loss_percent = min(pnl_pcts) if pnl_pcts else 0.0

    # ------------------------------------------------------------------
    # 报告输出
    # ------------------------------------------------------------------

    def print_report(self, result: PerformanceResult) -> None:
        """打印绩效报告到控制台

        Parameters
        ----------
        result : PerformanceResult
            绩效分析结果
        """
        print(f"\n{'='*55}")
        print(f"  援军战法回测绩效报告")
        print(f"  周期: {result.start_date} → {result.end_date}")
        print(f"{'='*55}")

        print(f"\n  【收益指标】")
        print(f"  {'总收益率':<16} {result.total_return:>8.2%}")
        print(f"  {'年化收益率':<16} {result.annual_return:>8.2%}")
        print(f"  {'累计收益率':<16} {result.cumulative_return:>8.2%}")

        print(f"\n  【风险指标】")
        print(f"  {'最大回撤':<16} {result.max_drawdown:>8.2%}")
        print(f"  {'夏普比率':<16} {result.sharpe_ratio:>8.2f}")
        print(f"  {'卡玛比率':<16} {result.calmar_ratio:>8.2f}")
        print(f"  {'年化波动率':<16} {result.volatility:>8.2%}")

        print(f"\n  【交易指标】")
        print(f"  {'总交易次数':<16} {result.total_trades:>8d}")
        print(f"  {'胜率':<16} {result.win_rate:>8.2%}")
        print(f"  {'平均盈利':<16} {result.avg_profit:>8.2%}")
        print(f"  {'平均亏损':<16} {result.avg_loss:>8.2%}")
        print(f"  {'盈亏比':<16} {result.profit_loss_ratio:>8.2f}")
        print(f"  {'平均持仓天数':<16} {result.avg_hold_days:>8.1f}")
        print(f"  {'最大连续盈利':<16} {result.max_consecutive_wins:>8d}")
        print(f"  {'最大连续亏损':<16} {result.max_consecutive_losses:>8d}")

        print(f"\n  【风控指标】")
        print(f"  {'最大单笔亏损':<16} {result.max_loss_percent:>8.2%}")
        print(f"{'='*55}\n")

    def compare_results(
        self,
        results: Dict[str, PerformanceResult],
    ) -> pd.DataFrame:
        """多组参数对比分析

        Parameters
        ----------
        results : Dict[str, PerformanceResult]
            {参数名称: 绩效结果} 的字典

        Returns
        -------
        pd.DataFrame
            对比表格
        """
        rows = []
        for name, r in results.items():
            rows.append({
                "参数": name,
                "总收益率": f"{r.total_return:.2%}",
                "年化收益": f"{r.annual_return:.2%}",
                "最大回撤": f"{r.max_drawdown:.2%}",
                "夏普比率": f"{r.sharpe_ratio:.2f}",
                "卡玛比率": f"{r.calmar_ratio:.2f}",
                "胜率": f"{r.win_rate:.2%}",
                "盈亏比": f"{r.profit_loss_ratio:.2f}",
                "交易次数": r.total_trades,
                "平均持仓": f"{r.avg_hold_days:.1f}天",
            })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # 可视化
    # ------------------------------------------------------------------

    def plot_equity_curve(self, result: PerformanceResult, title: str = "援军战法 - 净值曲线") -> None:
        """绘制净值曲线（简易版，使用matplotlib）

        Parameters
        ----------
        result : PerformanceResult
            绩效结果
        title : str, optional
            图表标题
        """
        if result.equity_curve is None or result.equity_curve.empty:
            print("无净值数据")
            return

        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
        except ImportError:
            print("需要安装 matplotlib: pip install matplotlib")
            return

        eq = result.equity_curve
        dates = pd.to_datetime(eq["date"])

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})
        fig.suptitle(title, fontsize=14, fontweight="bold")

        # 净值曲线
        ax1.plot(dates, eq["total_value"], label="净值", color="#e74c3c", linewidth=1.5)
        ax1.axhline(y=result.initial_capital, color="gray", linestyle="--", alpha=0.5, label="初始资金")
        ax1.set_ylabel("总资产（元）")
        ax1.legend(loc="upper left")
        ax1.grid(True, alpha=0.3)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

        # 回撤曲线
        cumulative = eq["total_value"] / result.initial_capital
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        ax2.fill_between(dates, 0, drawdown * 100, color="red", alpha=0.3, label="回撤")
        ax2.set_ylabel("回撤（%）")
        ax2.set_xlabel("日期")
        ax2.legend(loc="lower left")
        ax2.grid(True, alpha=0.3)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

        plt.tight_layout()
        plt.show()

    def build_results_dataframe(
        self,
        trade_records: List[TradeRecord],
    ) -> pd.DataFrame:
        """将交易记录转为 DataFrame

        Parameters
        ----------
        trade_records : List[TradeRecord]
            交易记录列表

        Returns
        -------
        pd.DataFrame
            交易记录表格
        """
        if not trade_records:
            return pd.DataFrame()

        rows = []
        for t in trade_records:
            rows.append({
                "股票代码": t.vt_symbol,
                "入场日期": t.entry_date,
                "出场日期": t.exit_date,
                "入场价": t.entry_price,
                "出场价": t.exit_price,
                "数量": t.shares,
                "盈亏": t.pnl_pct,
                "是否盈利": "✅" if t.is_win else "❌",
                "出场原因": t.exit_reason,
                "持仓天数": t.hold_days,
            })
        return pd.DataFrame(rows)

    @staticmethod
    def trades_from_strategy(strategy: "ReliefForceAlphaStrategy") -> List[TradeRecord]:
        """从策略实例提取交易记录

        Parameters
        ----------
        strategy : ReliefForceAlphaStrategy
            已运行的策略实例

        Returns
        -------
        List[TradeRecord]
            转换后的交易记录
        """
        records = []
        for t in getattr(strategy, "trade_log", []):
            record = TradeRecord(
                vt_symbol=t.get("vt_symbol", ""),
                entry_date=t.get("entry_date", ""),
                exit_price=t.get("exit_price", 0.0),
                entry_price=t.get("entry_price", 0.0),
                shares=t.get("shares", 0),
                pnl_pct=t.get("pnl_pct", 0.0),
                is_win=t.get("is_win", False),
                exit_reason=t.get("exit_reason", ""),
            )
            record.pnl = (record.exit_price - record.entry_price) * record.shares
            records.append(record)
        return records
