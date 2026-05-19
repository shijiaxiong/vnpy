"""
援军战法 全流程回测 v2
数据源：SQLite 数据库 ~/.vntrader/database.db（5分钟K线聚合日线）
回测区间：2024-01-02 ~ 2026-05-01
股票池：沪深主板（沪市主板60xxxx + 深市主板00xxxx）

测试重点：冷却打断逻辑（cooldown_interrupt_threshold=5%）
"""

import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = "/Users/zyb/go/src/github/vnpy"
sys.path.insert(0, PROJECT_ROOT)
os.environ["PYTHONPATH"] = PROJECT_ROOT

from vnpy.alpha.yuanjun.config import (
    BottomPatternConfig, EntryConfig, RiskConfig, StrategyConfig,
)
from vnpy.alpha.yuanjun.bottom_pattern import BottomPatternRecognizer
from vnpy.alpha.yuanjun.entry_signal import EntrySignalChecker
from vnpy.alpha.yuanjun.risk_manager import RiskManager
from vnpy.alpha.yuanjun.backtest import BacktestEvaluator, TradeRecord

# ---------- 配置 ----------
DB_PATH = os.path.expanduser("~/.vntrader/database.db")
DATE_START = "2024-01-02"
DATE_END = "2026-05-01"
INITIAL_CAPITAL = 1_000_000.0


def load_daily_from_db(
    date_start: str,
    date_end: str,
) -> Dict[str, pd.DataFrame]:
    """从SQLite加载日线（5分钟K线聚合，单SQL完成聚合）

    只取沪市主板(60xxxx)和深市主板(00xxxx)。

    Returns
    -------
    Dict[str, pd.DataFrame]
        {symbol: df}  df.columns=[open,high,low,close,volume], index=DatetimeIndex
    """
    t0 = time.time()
    print(f"  连接数据库: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)

    # 单条SQL完成聚合：利用窗口函数 FIRST_VALUE/LAST_VALUE
    # ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING 是关键
    query = f"""
        WITH daily AS (
            SELECT
                symbol,
                DATE(datetime) AS trade_date,
                high_price    AS high,
                low_price     AS low,
                volume,
                FIRST_VALUE(close_price) OVER (
                    PARTITION BY symbol, DATE(datetime)
                    ORDER BY datetime
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) AS open_price,
                LAST_VALUE(close_price) OVER (
                    PARTITION BY symbol, DATE(datetime)
                    ORDER BY datetime
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) AS close_price
            FROM dbbardata
            WHERE interval = '5m'
                AND datetime >= '{date_start} 09:00:00'
                AND datetime <= '{date_end} 15:10:00'
                AND (symbol LIKE '60%' OR symbol LIKE '00%')
        )
        SELECT
            symbol,
            trade_date,
            open_price  AS open,
            MAX(high)   AS high,
            MIN(low)    AS low,
            close_price AS close,
            SUM(volume) AS volume
        FROM daily
        GROUP BY symbol, trade_date
        ORDER BY symbol, trade_date
    """
    print(f"  执行聚合SQL...")
    df = pd.read_sql(query, conn)
    conn.close()
    print(f"  SQL完成: {len(df):,} 行, 耗时 {time.time()-t0:.1f}s")

    if df.empty:
        raise RuntimeError("数据库查询结果为空！")

    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 按股票拆分
    stock_data: Dict[str, pd.DataFrame] = {}
    for sym, grp in df.groupby("symbol"):
        s = grp.set_index("trade_date").sort_index()
        s = s[["open", "high", "low", "close", "volume"]]
        if len(s) >= 60:
            stock_data[sym] = s

    # 交易日列表：所有股票日期的并集（每只股独立处理停牌日）
    all_dates_set: set = set().union(*[set(d.index) for d in stock_data.values()])
    trade_dates: List[datetime] = sorted(all_dates_set)

    print(f"  股票数: {len(stock_data)} 只（排除数据不足60天）")
    print(f"  交易日: {trade_dates[0].date()} ~ {trade_dates[-1].date()}, 共 {len(trade_dates)} 天")
    print(f"  总耗时: {time.time()-t0:.1f}s")
    return stock_data


class MarketBacktest:
    """全市场回测引擎（无板块概念，所有股票竞争入场）

    每日流程：
    1. 清理冷却期过期的记录（每个 BottomPatternRecognizer 独立维护）
    2. 检查持仓退出（止损/止盈/超期）
    3. 从未持仓股票中找入场信号（最多3只/日）
    4. 记录净值
    """

    def __init__(
        self,
        stock_data: Dict[str, pd.DataFrame],
        trade_dates: List[datetime],
        initial_capital: float = 1_000_000,
    ):
        self.stock_data = stock_data
        self.trade_dates = trade_dates
        self.date_index = {d: i for i, d in enumerate(trade_dates)}

        # 每个股票独立的止跌形态识别器（含冷却打断逻辑）
        self.pattern_recognizers: Dict[str, BottomPatternRecognizer] = {
            sym: BottomPatternRecognizer(BottomPatternConfig())
            for sym in stock_data
        }

        # 风控/入场/止损
        self.config = StrategyConfig(
            bottom_config=BottomPatternConfig(),
            entry_config=EntryConfig(),
            risk_config=RiskConfig(),
            max_hold_days=10,
            max_trades_per_day=3,
        )
        self.entry_checker = EntrySignalChecker(self.config.entry_config)
        self.risk_manager = RiskManager(self.config.risk_config)

        # 账户状态
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.positions: Dict[str, Dict] = {}  # {symbol: {...}}
        self.holding_days: Dict[str, int] = {}
        self.trade_records: List[TradeRecord] = []
        self.equity_curve: List[Dict] = []

        # 调试统计
        self._stat_candidates = 0       # 扫描过候选股数
        self._stat_no_data = 0         # 数据不足
        self._stat_bottom_fail = 0     # 止跌形态未通过
        self._stat_bottom_cooldown = 0 # 止跌形态冷却中
        self._stat_entry_fail = 0       # 入场信号未通过
        self._stat_pos_zero = 0        # 仓位为0

    def run(self, date_start: str, date_end: str) -> pd.DataFrame:
        """运行回测"""
        test_dates = [
            d for d in self.trade_dates
            if date_start <= str(d.date()) <= date_end
        ]
        print(f"\n  回测区间: {date_start} ~ {date_end}, 共 {len(test_dates)} 个交易日")
        print(f"  初始资金: {self.initial_capital:,.0f}")
        print(f"  形态识别: 回调≥8%, |日涨跌|≤6%, 冷却15天, 打断阈值5%")
        print("-" * 55)

        t0 = time.time()
        for t_idx, date in enumerate(test_dates):
            self._check_exits(date)
            if not self.risk_manager.is_paused:
                self._check_entries(date)
            self._record_equity(date)

            if (t_idx + 1) % 100 == 0:
                elapsed = time.time() - t0
                rate = (t_idx + 1) / elapsed
                eta = (len(test_dates) - t_idx - 1) / rate
                print(f"    [{t_idx+1}/{len(test_dates)}] "
                      f"耗时{elapsed:.0f}s | 速度{rate:.0f}天/秒 | 预计剩余{eta:.0f}s "
                      f"| 持仓{len(self.positions)} | 交易{len(self.trade_records)}")

        print("-" * 55)
        print(f"  回测完成: {len(self.trade_records)} 笔交易, 耗时 {time.time()-t0:.1f}s")
        # 调试统计
        total = self._stat_candidates
        print(f"\n  [调试统计] 候选扫描: {total:,}")
        print(f"    数据不足:        {self._stat_no_data:>8,} ({self._stat_no_data/max(total,1):.1%})")
        print(f"    止跌形态未通过:  {self._stat_bottom_fail:>8,} ({self._stat_bottom_fail/max(total,1):.1%})")
        print(f"    止跌形态冷却中:  {self._stat_bottom_cooldown:>8,} ({self._stat_bottom_cooldown/max(total,1):.1%})")
        print(f"    入场信号未通过:  {self._stat_entry_fail:>8,} ({self._stat_entry_fail/max(total,1):.1%})")
        print(f"    仓位为0:        {self._stat_pos_zero:>8,} ({self._stat_pos_zero/max(total,1):.1%})")
        return pd.DataFrame(self.equity_curve)

    def _check_exits(self, date) -> None:
        """检查持仓退出"""
        for code, pos in list(self.positions.items()):
            df = self.stock_data.get(code)
            if df is None or date not in df.index:
                continue

            current_price = float(df.loc[date, "close"])
            hold_days = self.holding_days.get(code, 0) + 1

            # 更新移动止损最高价
            if current_price > pos.get("high_since", pos["entry_price"]):
                pos["high_since"] = current_price

            # 动态止损
            _, stop_info = self.risk_manager.calculate_stop_price(
                entry_price=pos["entry_price"],
                current_high=pos["high_since"],
            )
            dynamic_stop = stop_info["final_stop"]

            exit_reason = None
            if current_price <= dynamic_stop:
                exit_reason = "止损"
            elif current_price >= pos["target_price"]:
                exit_reason = "止盈"
            elif hold_days >= self.config.max_hold_days:
                exit_reason = "超期"

            if exit_reason:
                is_win = current_price > pos["entry_price"]
                self.capital += current_price * pos["shares"]
                self.trade_records.append(TradeRecord(
                    vt_symbol=code,
                    entry_date=pos["entry_date"],
                    exit_date=str(date.date()),
                    entry_price=pos["entry_price"],
                    exit_price=current_price,
                    shares=pos["shares"],
                    pnl=(current_price - pos["entry_price"]) * pos["shares"],
                    pnl_pct=(current_price - pos["entry_price"]) / pos["entry_price"],
                    is_win=is_win,
                    exit_reason=exit_reason,
                    hold_days=hold_days,
                    cooldown_interrupted=pos.get("cooldown_interrupted", False),
                ))
                self.risk_manager.update_after_trade(is_win)
                del self.positions[code]
                del self.holding_days[code]
            else:
                self.holding_days[code] = hold_days

    def _check_entries(self, date) -> None:
        """扫描所有未持仓股票，找止跌形态入场机会"""
        date_str = str(date.date())
        trades_today = 0

        for code in self.stock_data:
            if code in self.positions:
                continue
            if trades_today >= self.config.max_trades_per_day:
                break

            df = self.stock_data[code]
            self._stat_candidates += 1
            if date not in df.index or len(df.loc[:date]) < 60:
                self._stat_no_data += 1
                continue

            hist = df.loc[:date]

            # 止跌形态（含冷却打断逻辑）
            recognizer = self.pattern_recognizers[code]
            is_bottom, bottom_info = recognizer.is_bottom_pattern(hist)
            if not is_bottom:
                if bottom_info.get("cooldown"):
                    self._stat_bottom_cooldown += 1
                else:
                    self._stat_bottom_fail += 1
                continue

            # 止损价：用固定止损（2%），避免 look-ahead bias（用当日 high）
            # trailing stop 仅在持仓期间用
            entry_price = float(hist["close"].iloc[-1])
            stop_price = entry_price * (1 - self.config.risk_config.stop_loss_pct)

            # 入场信号
            can_enter, entry_info = self.entry_checker.can_enter(
                hist, stop_price, "14:50"
            )
            if not can_enter:
                self._stat_entry_fail += 1
                continue

            # 仓位计算（entry_price 已在上面定义）
            shares, pos_info = self.risk_manager.calculate_position_size(
                self.capital, entry_price, stop_price
            )
            if shares <= 0:
                self._stat_pos_zero += 1
                continue

            interrupted = bottom_info.get("cooldown_interrupted", False)
            cost = entry_price * shares
            if cost > self.capital:
                shares = int(self.capital / entry_price)
                if shares <= 0:
                    continue
                cost = entry_price * shares

            self.capital -= cost
            # 标记本次入场是否由冷却打断触发
            interrupted = bottom_info.get("cooldown_interrupted", False)
            self.positions[code] = {
                "entry_price": entry_price,
                "shares": shares,
                "stop_price": stop_price,
                "entry_date": date_str,
                "target_price": entry_info.get("target_price", entry_price * 1.05),
                "high_since": entry_price,
                "cooldown_interrupted": interrupted,
            }
            self.holding_days[code] = 0
            trades_today += 1

    def _record_equity(self, date) -> None:
        """记录每日净值"""
        holding_value = 0.0
        for code, pos in self.positions.items():
            df = self.stock_data.get(code)
            if df is not None and date in df.index:
                holding_value += float(df.loc[date, "close"]) * pos["shares"]
        total = self.capital + holding_value
        self.equity_curve.append({
            "date": date,
            "total_value": total,
            "cash": self.capital,
            "holding_value": holding_value,
        })


def main():
    print("=" * 55)
    print("  援军战法 全市场回测 v2")
    print("  数据源: SQLite 5分钟K线 → 日线聚合")
    print("  回测区间: 2024-01-02 ~ 2026-05-01")
    print("  股票池: 沪深主板（沪市主板60xxxx + 深市主板00xxxx）")
    print("=" * 55)

    # 1. 加载数据
    stock_data = load_daily_from_db(DATE_START, DATE_END)

    # 2. 交易日列表（所有股票日期的并集，停牌日各股独立处理）
    all_dates_set: set = set().union(*[set(df.index) for df in stock_data.values()])
    trade_dates = sorted(all_dates_set)

    # 3. 构建回测
    bt = MarketBacktest(
        stock_data=stock_data,
        trade_dates=trade_dates,
        initial_capital=INITIAL_CAPITAL,
    )

    # 4. 运行
    eq = bt.run(DATE_START, DATE_END)

    # 5. 绩效分析
    print(f"\n{'=' * 55}")
    print(f"  绩效分析")
    print(f"{'=' * 55}")
    evaluator = BacktestEvaluator(initial_capital=INITIAL_CAPITAL)
    result = evaluator.evaluate(eq, bt.trade_records)
    evaluator.print_report(result)

    # 6. 保存净值曲线
    out_path = f"{PROJECT_ROOT}/.cache/backtest_2024_2026.pkl"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pd.to_pickle(eq, out_path)

    # 7. 冷却打断统计
    interrupted_trades = [r for r in bt.trade_records if r.cooldown_interrupted]
    normal_trades = [r for r in bt.trade_records if not r.cooldown_interrupted]

    print(f"\n  冷却打断统计:")
    print(f"    普通触底入场: {len(normal_trades)} 笔")
    print(f"    打断冷却入场: {len(interrupted_trades)} 笔")
    if interrupted_trades:
        int_win = sum(1 for r in interrupted_trades if r.is_win)
        print(f"    打断入场胜率: {int_win}/{len(interrupted_trades)} = {int_win/len(interrupted_trades):.1%}")
    print(f"\n  净值曲线已保存: {out_path}")


if __name__ == "__main__":
    main()
