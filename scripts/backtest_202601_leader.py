"""
龙头援军回测 — 2026年1月
数据源：SQLite 数据库（逐symbol查询，利用复合索引）
策略：leader_style（连板后断板 → 止跌4条件 → 尾盘入场 → 止损/止盈7%/超期5天）
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = "/Users/shijiaxiong/go/src/github.com/vnpy"
sys.path.insert(0, PROJECT_ROOT)
os.environ["PYTHONPATH"] = PROJECT_ROOT

from vnpy.alpha.yuanjun.config import (
    BottomPatternConfig, EntryConfig, RiskConfig, StrategyConfig,
)
from vnpy.alpha.yuanjun.bottom_pattern import BottomPatternRecognizer
from vnpy.alpha.yuanjun.entry_signal import EntrySignalChecker
from vnpy.alpha.yuanjun.risk_manager import RiskManager
from vnpy.alpha.yuanjun.selector import BrokenBoardConfig, BrokenBoardSelector
from vnpy.alpha.yuanjun.backtest import TradeRecord, PerformanceResult, BacktestEvaluator

# ---------- 配置 ----------
DB_PATH = os.path.expanduser("~/.vntrader/database_2026.db")  # 主数据库（含回测窗口数据）
DB_PATH_HISTORY = os.path.expanduser("~/.vntrader/database_2025.db")  # 历史数据库（含MA250计算所需回看数据）
NAME_CACHE = os.path.join(PROJECT_ROOT, ".cache/stock_names.json")  # 股票名称缓存
STOCK_CACHE = os.path.join(PROJECT_ROOT, ".cache/stocks_akshare.json")
DATE_START = "2026-06-01"
DATE_END = "2026-06-17"
DATE_LOOKBACK = "2024-01-01"  # 2.5年历史，确保MA250/MACD可用（实际通过2025+2026库覆盖）
INITIAL_CAPITAL = 1_000_000.0
MAX_SYMBOLS = 500  # 500只样本，平衡速度和覆盖度


def get_stock_symbols(max_n: int = MAX_SYMBOLS) -> List[str]:
    """从缓存获取股票代码列表（沪深主板）"""
    with open(STOCK_CACHE, "r") as f:
        all_symbols = json.load(f)

    # 过滤沪深主板：sh.60xxxx, sz.00xxxx
    main_board = []
    for s in all_symbols:
        if s.startswith("sh.60") or s.startswith("sz.00") or s.startswith("sz.000"):
            code = s.split(".")[1]
            if len(code) == 6 and (code.startswith("60") or code.startswith("00")):
                main_board.append(code)

    print(f"  沪深主板股票总数: {len(main_board)}")

    # 随机抽样加速测试（可改为全量）
    if max_n > 0 and len(main_board) > max_n:
        np.random.seed(42)
        main_board = sorted(np.random.choice(main_board, max_n, replace=False))
        print(f"  抽样 {max_n} 只用于回测")
    return main_board


def query_symbol(symbol: str, conn: sqlite3.Connection, 
                 date_start: str, date_end: str,
                 use_history: bool = False) -> Optional[pd.DataFrame]:
    """查询单只股票的日线（利用复合索引，逐symbol查询避免全表扫描）
    
    当 use_history=True 时，通过跨库 UNION ALL 从主库和历史库合并数据。
    """
    if use_history:
        query = """
            WITH daily AS (
                SELECT
                    DATE(datetime) AS trade_date,
                    high_price    AS high,
                    low_price     AS low,
                    volume,
                    turnover,
                    FIRST_VALUE(close_price) OVER (
                        PARTITION BY DATE(datetime)
                        ORDER BY datetime
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                    ) AS open_price,
                    LAST_VALUE(close_price) OVER (
                        PARTITION BY DATE(datetime)
                        ORDER BY datetime
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                    ) AS close_price
                FROM (
                    SELECT * FROM main.dbbardata
                    WHERE symbol = ?
                        AND exchange IN ('SSE', 'SZSE')
                        AND interval = '5m'
                        AND datetime >= ? || ' 09:00:00'
                        AND datetime <= ? || ' 15:10:00'
                    UNION ALL
                    SELECT * FROM history.dbbardata
                    WHERE symbol = ?
                        AND exchange IN ('SSE', 'SZSE')
                        AND interval = '5m'
                        AND datetime >= ? || ' 09:00:00'
                        AND datetime <= ? || ' 15:10:00'
                )
            )
            SELECT
                trade_date,
                open_price  AS open,
                MAX(high)   AS high,
                MIN(low)    AS low,
                close_price AS close,
                SUM(volume) AS volume,
                SUM(turnover) AS turnover
            FROM daily
            GROUP BY trade_date
            ORDER BY trade_date
        """
        params = (symbol, date_start, date_end, symbol, date_start, date_end)
    else:
        query = """
            WITH daily AS (
                SELECT
                    DATE(datetime) AS trade_date,
                    high_price    AS high,
                    low_price     AS low,
                    volume,
                    turnover,
                    FIRST_VALUE(close_price) OVER (
                        PARTITION BY DATE(datetime)
                        ORDER BY datetime
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                    ) AS open_price,
                    LAST_VALUE(close_price) OVER (
                        PARTITION BY DATE(datetime)
                        ORDER BY datetime
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                    ) AS close_price
                FROM dbbardata
                WHERE symbol = ?
                    AND exchange IN ('SSE', 'SZSE')
                    AND interval = '5m'
                    AND datetime >= ? || ' 09:00:00'
                    AND datetime <= ? || ' 15:10:00'
            )
            SELECT
                trade_date,
                open_price  AS open,
                MAX(high)   AS high,
                MIN(low)    AS low,
                close_price AS close,
                SUM(volume) AS volume,
                SUM(turnover) AS turnover
            FROM daily
            GROUP BY trade_date
            ORDER BY trade_date
        """
        params = (symbol, date_start, date_end)
    try:
        df = pd.read_sql(query, conn, params=params)
        if len(df) < 60:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date").sort_index()
        return df[["open", "high", "low", "close", "volume", "turnover"]]
    except Exception as e:
        return None


def load_stock_data(symbols: List[str]) -> Tuple[Dict[str, pd.DataFrame], List[datetime]]:
    """逐只加载日线数据（跨2025+2026两库查询以确保MA250计算可用）"""
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    # ATTACH 历史数据库（2025年），提供MA250回看数据
    if os.path.exists(DB_PATH_HISTORY):
        conn.execute(f"ATTACH DATABASE '{DB_PATH_HISTORY}' AS history")
        union_all = True
        print(f"  已挂载历史库: {DB_PATH_HISTORY}")
    else:
        union_all = False
        print(f"  警告: 历史库不存在 {DB_PATH_HISTORY}, MA250计算可能不准确")

    stock_data: Dict[str, pd.DataFrame] = {}
    failed = 0
    for i, sym in enumerate(symbols):
        df = query_symbol(sym, conn, DATE_LOOKBACK, DATE_END, use_history=union_all)
        if df is not None:
            stock_data[sym] = df
        else:
            failed += 1
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f"    [{i+1}/{len(symbols)}] 加载中... 成功{len(stock_data)} 失败{failed} 耗时{elapsed:.0f}s")

    conn.close()
    elapsed = time.time() - t0
    print(f"  加载完成: {len(stock_data)} 只有效股票, {failed} 只失败, 耗时 {elapsed:.0f}s")

    if not stock_data:
        raise RuntimeError("未获取到任何有效数据！")

    # 交易日列表：仅回测窗口内的日期
    test_start = pd.Timestamp(DATE_START)
    test_end = pd.Timestamp(DATE_END)
    all_dates = sorted(set().union(*[set(d.index) for d in stock_data.values()]))
    trade_dates: List[datetime] = [
        d for d in all_dates if test_start <= d <= test_end
    ]
    print(f"  交易日: {trade_dates[0].date()} ~ {trade_dates[-1].date()}, 共 {len(trade_dates)} 天（回测窗口）")
    return stock_data, trade_dates


class LeaderBacktest:
    """龙头援军回测引擎

    每日流程：
    1. 清理过期持仓（止损/止盈7%/超期5天）
    2. 筛选断板候选（前N日涨停+今日不涨停）
    3. 止跌形态识别（4条件）
    4. 入场信号检查（尾盘+盈亏比）
    5. 记录净值
    """

    def __init__(self, stock_data: Dict[str, pd.DataFrame], trade_dates: List[datetime]):
        self.stock_data = stock_data
        self.trade_dates = trade_dates

        # 断板筛选器
        board_cfg = BrokenBoardConfig()
        self.board_selector = BrokenBoardSelector(board_cfg)

        # 策略配置（龙头援军风格）
        self.strategy_config = StrategyConfig.leader_style()
        self.bottom_config = self.strategy_config.bottom_config
        self.entry_config = self.strategy_config.entry_config
        self.risk_config = self.strategy_config.risk_config

        # 每只股票独立止跌识别器
        self.pattern_recognizers = {
            sym: BottomPatternRecognizer(self.bottom_config)
            for sym in stock_data
        }

        self.entry_checker = EntrySignalChecker(self.entry_config)
        self.risk_manager = RiskManager(self.risk_config)

        # 账户
        self.capital = INITIAL_CAPITAL
        self.initial_capital = INITIAL_CAPITAL
        self.positions: Dict[str, Dict] = {}
        self.holding_days: Dict[str, int] = {}
        self.trade_records: List[TradeRecord] = []
        self.equity_points: List[Dict] = []

        # 统计
        self.stats = {
            "board_candidates": 0,
            "bottom_pass": 0,
            "entry_pass": 0,
            "trades_executed": 0,
        }

    def run(self) -> pd.DataFrame:
        t0 = time.time()
        for t_idx, date in enumerate(self.trade_dates):
            self._process_exits(date)
            if not self.risk_manager.is_paused:
                self._process_entries(date)
            self._record_equity(date)

            if (t_idx + 1) % 5 == 0 or t_idx == 0:
                et = time.time() - t0
                print(f"  [{t_idx+1}/{len(self.trade_dates)}] {date.date()} "
                      f"| 持仓{len(self.positions)} | 交易{len(self.trade_records)} | 净值{self._net_value(date):.2f} | 耗时{et:.0f}s")

        elapsed = time.time() - t0
        print(f"\n回测完成，总耗时 {elapsed:.0f}s")
        return pd.DataFrame(self.equity_points)

    def _process_exits(self, date: datetime):
        """处理持仓退出"""
        for sym, pos in list(self.positions.items()):
            df = self.stock_data.get(sym)
            if df is None or date not in df.index:
                continue

            bar = df.loc[date]
            current_price = float(bar["close"])
            self.holding_days[sym] = self.holding_days.get(sym, 0) + 1
            hold_days = self.holding_days[sym]

            # 更新移动止损最高价
            if current_price > pos.get("high_since_entry", pos["entry_price"]):
                pos["high_since_entry"] = current_price

            entry_price = pos["entry_price"]
            high_since = pos.get("high_since_entry", entry_price)

            # 动态止损
            fixed_stop = entry_price * (1 - self.risk_config.stop_loss_pct)
            atr_stop = entry_price * (1 - self.risk_config.stop_loss_pct * self.risk_config.atr_multiplier)
            trailing_stop = high_since * (1 - self.risk_config.trailing_stop_pct)
            dynamic_stop = max(fixed_stop, atr_stop, trailing_stop)

            exit_reason = None
            if current_price <= dynamic_stop:
                exit_reason = "止损"
            elif current_price >= pos["target_price"]:
                exit_reason = "止盈"
            elif hold_days >= self.strategy_config.max_hold_days:
                exit_reason = "超期平仓"

            if exit_reason:
                is_win = current_price > entry_price
                pnl_pct = (current_price - entry_price) / entry_price
                pnl_amount = pos["shares"] * (current_price - entry_price)
                self.trade_records.append(TradeRecord(
                    vt_symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=str(date.date()),
                    entry_price=entry_price,
                    exit_price=current_price,
                    shares=pos.get("shares", 0),
                    pnl=round(pnl_amount, 2),
                    pnl_pct=round(pnl_pct * 100, 2),
                    exit_reason=exit_reason,
                    is_win=is_win,
                    hold_days=hold_days,
                ))
                self.capital += pos.get("cost", pos["shares"] * entry_price) + pnl_amount  # 归还开仓成本 + 盈亏
                self.risk_manager.update_after_trade(is_win)
                del self.positions[sym]
                del self.holding_days[sym]

    def _process_entries(self, date: datetime):
        """处理入场"""
        date_str = str(date.date())

        # 获取当天有数据且未持仓的股票
        candidates = {}
        for sym, df in self.stock_data.items():
            if sym in self.positions:
                continue
            if date not in df.index:
                continue
            candidates[sym] = df.loc[:date]

        if not candidates:
            return

        # 模块1：断板筛选
        board_codes, _ = self.board_selector.select(candidates)
        self.stats["board_candidates"] += len(board_codes)

        entered_today = 0
        for code in board_codes:
            if entered_today >= self.strategy_config.max_trades_per_day:
                break

            df = candidates[code]
            recognizer = self.pattern_recognizers[code]

            # 模块2：止跌形态
            is_bottom, bottom_info = recognizer.is_bottom_pattern(df)
            if not is_bottom:
                continue
            self.stats["bottom_pass"] += 1

            # 止损价
            _, stop_info = self.risk_manager.calculate_stop_price(
                entry_price=float(df["close"].iloc[-1]),
                current_high=float(df["high"].iloc[-1]),
            )
            stop_price = stop_info["final_stop"]

            # 模块3：入场信号
            current_price = float(df["close"].iloc[-1])
            can_enter, entry_info = self.entry_checker.can_enter(
                df, stop_price, current_time="14:50"
            )
            if not can_enter:
                continue
            self.stats["entry_pass"] += 1

            # 仓位（计算后按可用资金截断）
            position_size, _ = self.risk_manager.calculate_position_size(
                self._net_value(date), current_price, stop_price
            )
            max_shares = int(self.capital * 0.25 / current_price)  # 单票最多25%仓位
            position_size = min(position_size, max_shares)
            if position_size < 100:  # A股最小1手
                continue

            # 入场
            cost = position_size * current_price
            self.positions[code] = {
                "entry_price": current_price,
                "shares": position_size,
                "stop_price": stop_price,
                "entry_date": date_str,
                "target_price": round(current_price * (1 + self.risk_config.take_profit_pct), 2),
                "high_since_entry": current_price,
                "cost": cost,
            }
            self.capital -= cost  # 从可用资金扣除开仓成本
            self.holding_days[code] = 0
            self.stats["trades_executed"] += 1
            entered_today += 1

    def _net_value(self, date: datetime = None) -> float:
        """当前总净值（基于指定日期的收盘价）"""
        val = self.capital
        for sym, pos in self.positions.items():
            df = self.stock_data.get(sym)
            if df is not None and date is not None and date in df.index:
                val += pos["shares"] * float(df.loc[date, "close"])
            elif df is not None and len(df) > 0:
                val += pos["shares"] * float(df["close"].iloc[-1])
        return val

    def _record_equity(self, date: datetime):
        """记录每日净值"""
        self.equity_points.append({
            "date": date,
            "equity": self._net_value(date),
            "positions": len(self.positions),
        })


def print_summary(records: List[TradeRecord], equity_df: pd.DataFrame, names: Dict[str, str] = None):
    """打印回测摘要"""
    if not records:
        print("\n  无交易记录，检查策略条件是否过严")
        return

    wins = [r for r in records if r.is_win]
    losses = [r for r in records if not r.is_win]
    win_rate = len(wins) / len(records) if records else 0

    pnls = [r.pnl_pct for r in records]
    total_return = (equity_df["equity"].iloc[-1] / INITIAL_CAPITAL - 1) * 100

    print("\n" + "=" * 60)
    print("回测结果摘要")
    print("=" * 60)
    print(f"  总交易笔数: {len(records)}")
    print(f"  盈利笔数:   {len(wins)}")
    print(f"  亏损笔数:   {len(losses)}")
    print(f"  胜率:       {win_rate:.1%}")
    print(f"  平均盈亏:   {np.mean(pnls):.2f}%")
    if wins:
        print(f"  平均盈利:   {np.mean([r.pnl_pct for r in wins]):.2f}%")
    if losses:
        print(f"  平均亏损:   {np.mean([r.pnl_pct for r in losses]):.2f}%")
    print(f"  最大盈利:   {max(pnls):.2f}%")
    print(f"  最小亏损:   {min(pnls):.2f}%")
    print(f"  总收益率:   {total_return:.2f}%")
    print(f"  最终净值:   {equity_df['equity'].iloc[-1]:,.2f}")
    print("=" * 60)
    print("\n按退出原因:")
    for reason in ["止盈", "止损", "超期平仓"]:
        rs = [r for r in records if r.exit_reason == reason]
        if rs:
            wr = len([r for r in rs if r.is_win]) / len(rs) * 100
            avg = np.mean([r.pnl_pct for r in rs])
            print(f"  {reason}: {len(rs)}笔, 胜率{wr:.0f}%, 平均盈亏{avg:.2f}%")

    if names is None:
        names = {}
    print("\n交易明细:")
    print(f"  {'入场日':>12}  {'出场日':>12}  {'代码':>12}  {'入场价':>8}  {'出场价':>8}  {'盈亏':>8}  {'原因':>6}")
    print(f"  {'-'*85}")
    for r in records[:20]:
        code = r.vt_symbol
        name = names.get(code, "")
        display = f"{code} {name}" if name else code
        print(f"  {r.entry_date:>12}  {r.exit_date:>12}  {display:<12}  {r.entry_price:>8.2f}  {r.exit_price:>8.2f}  {r.pnl_pct:>7.2f}%  {r.exit_reason:>6}")


def main():
    print("=" * 60)
    print("龙头援军回测 — 2026年6月")
    print(f"策略: leader_style | 止盈+7% | 止损-5% | 最大持仓5天 | 日上限4笔")
    print("=" * 60)

    # 加载股票名称映射
    names: Dict[str, str] = {}
    if os.path.exists(NAME_CACHE):
        with open(NAME_CACHE, "r") as f:
            names = json.load(f)
        print(f"  已加载 {len(names)} 只股票名称映射")

    # 1. 加载股票列表
    print("\n[1/3] 加载股票列表...")
    symbols = get_stock_symbols(MAX_SYMBOLS)
    if not symbols:
        print("无可用股票")
        return

    # 2. 加载日线数据
    print(f"\n[2/3] 加载日线数据 (回溯 {DATE_LOOKBACK} ~ {DATE_END})...")
    stock_data, trade_dates = load_stock_data(symbols)

    # 3. 运行回测
    print(f"\n[3/3] 运行回测...")
    bt = LeaderBacktest(stock_data, trade_dates)
    equity_df = bt.run()

    # 结果
    print(f"\n  断板候选: {bt.stats['board_candidates']} | "
          f"止跌通过: {bt.stats['bottom_pass']} | "
          f"入场通过: {bt.stats['entry_pass']} | "
          f"实际交易: {bt.stats['trades_executed']}")

    print_summary(bt.trade_records, equity_df, names)

    # 保存
    out_dir = os.path.join(PROJECT_ROOT, "output")
    os.makedirs(out_dir, exist_ok=True)
    equity_df.to_csv(os.path.join(out_dir, "equity_202601_leader.csv"), index=False)
    trade_df = pd.DataFrame([{
        "symbol": r.vt_symbol,
        "name": names.get(r.vt_symbol, "") if names else "",
        "entry_date": r.entry_date, "exit_date": r.exit_date,
        "entry_price": r.entry_price, "exit_price": r.exit_price,
        "pnl_pct": r.pnl_pct, "reason": r.exit_reason, "is_win": r.is_win,
    } for r in bt.trade_records])
    trade_df.to_csv(os.path.join(out_dir, "trades_202601_leader.csv"), index=False)
    print(f"\n结果已保存到 output/ 目录")


if __name__ == "__main__":
    main()
