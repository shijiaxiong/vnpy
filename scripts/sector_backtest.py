"""
援军战法 板块池回测 v5（D+C组合）
D - 超期退出改造：持仓30天 + ATR*3 自适应目标价
C - 相对强势选股：板块内5日涨幅排序，只取前50%
"""

import os
import sqlite3
import sys
import time
import json
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Dict, List, Optional, Tuple

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
SECTOR_PATH = os.path.join(PROJECT_ROOT, ".cache/sector_stocks.json")
DATE_START = "2024-01-02"
DATE_END = "2026-05-01"
INITIAL_CAPITAL = 1_000_000.0

# 板块池参数（D+C组合版）
COOLDOWN_DAYS = 0           # 完全去掉冷却期
RR_RATIO = 0.5             # 盈亏比门槛（0.5:1，仅作为底线）
MAX_TRADES_PER_DAY = 20    # 每日最多（候选更多了）
DOWN_AMPLITUDE = 0.10      # 回调幅度 ≥ 10%（待测 8% vs 10%）
STOP_LOSS_PCT = 0.10       # 止损 10%
MAX_HOLD_DAYS = 30         # D: 持仓 30 天（给ATR目标足够时间到达）
REL_STRENGTH_TOP_PCT = 0.5 # C: 只取板块内前50%最具相对强度的股票
ATR_TARGET_MULTIPLIER = 3.0 # D: ATR倍数作为目标价
# 敞口熔断参数
MAX_CONCURRENT_POSITIONS = 8    # 1: 最多同时持仓 8 笔
MAX_POSITIONS_PER_SECTOR = 5    # 2: 单板块最多 5 笔
CIRCUIT_BREAKER_DRAWDOWN = 0.10 # 3: 回撤 > 10% 时停止所有新入场
CIRCUIT_BREAKER_RESUME = 0.03   #   回到新高前 3% 内恢复


def load_sector_pool() -> Dict[str, dict]:
    """加载板块股票池"""
    with open(SECTOR_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_daily_from_db_sector(
    sector_pool: Dict[str, dict],
    date_start: str,
    date_end: str,
) -> Dict[str, pd.DataFrame]:
    """从SQLite加载板块股票的日线数据

    Returns
    -------
    Dict[str, pd.DataFrame]
        {symbol: df}  df.columns=[open,high,low,close,volume], index=DatetimeIndex
    """
    t0 = time.time()
    print(f"  连接数据库: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)

    # 构造 symbol IN (...) 子句 - 数据库格式是 600xxx/000xxx（无前缀）
    symbols = list(sector_pool.keys())
    quoted = ', '.join(f"'{s}'" for s in symbols)

    # 尝试用 baostock 格式查，同时兼容有无点的格式
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
                AND symbol IN ({quoted})
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
    print(f"  执行聚合SQL（板块池 {len(symbols)} 只）...")
    df = pd.read_sql(query, conn)
    conn.close()
    print(f"  SQL完成: {len(df):,} 行, 耗时 {time.time()-t0:.1f}s")

    if df.empty:
        raise RuntimeError("板块池查询结果为空！请检查 symbol 格式")

    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 统一 symbol 格式（数据库存的是 600xxx/000xxx，sector_pool 键也是）
    df["symbol"] = df["symbol"].str.replace('.', '', regex=False)

    # 按股票拆分
    stock_data: Dict[str, pd.DataFrame] = {}
    for sym, grp in df.groupby("symbol"):
        s = grp.set_index("trade_date").sort_index()
        s = s[["open", "high", "low", "close", "volume"]]
        if len(s) >= 250:  # 需要250天以上才能计算250日均线
            stock_data[sym] = s

    # 交易日列表
    all_dates_set: set = set().union(*[set(d.index) for d in stock_data.values()])
    trade_dates: List[datetime] = sorted(all_dates_set)

    print(f"  有数据股票: {len(stock_data)} 只（排除数据不足250天）")
    print(f"  交易日: {trade_dates[0].date()} ~ {trade_dates[-1].date()}, 共 {len(trade_dates)} 天")
    print(f"  总耗时: {time.time()-t0:.1f}s")
    return stock_data


class SectorBacktest:
    """板块池回测引擎

    每日流程：
    1. 清理冷却期过期的记录
    2. 计算板块动量（当日涨幅前50%的板块才参与候选）
    3. 检查持仓退出（止损/止盈/超期）
    4. 扫描板块内候选股（最多8只/日）
    5. 记录净值
    """

    def __init__(
        self,
        stock_data: Dict[str, pd.DataFrame],
        sector_pool: Dict[str, dict],
        trade_dates: List[datetime],
        initial_capital: float = 1_000_000,
    ):
        self.stock_data = stock_data
        self.sector_pool = sector_pool
        self.trade_dates = trade_dates
        self.date_index = {d: i for i, d in enumerate(trade_dates)}

        # 每个股票独立的止跌形态识别器（四条件版）
        bottom_config = BottomPatternConfig(
            down_amplitude_min=DOWN_AMPLITUDE,
            price_spike_threshold=0.06,
            near_bottom_max_pct=0.02,    # 股价在10日最低价上方≤2%
            above_ma250=True,             # 站上250日均线
            cooldown_days=COOLDOWN_DAYS,
            cooldown_interrupt_threshold=0.05,
        )
        self.pattern_recognizers: Dict[str, BottomPatternRecognizer] = {
            sym: BottomPatternRecognizer(bottom_config)
            for sym in stock_data
        }

        # 策略配置（放松版）
        self.config = StrategyConfig(
            bottom_config=bottom_config,
            entry_config=EntryConfig(
                entry_time_start="14:40",
                entry_time_end="14:55",
                min_distance_to_stop=0.01,
                max_distance_to_stop=0.15,  # 15%（兼容10%止损的空间 ~11.1%）
                min_risk_reward_ratio=RR_RATIO,
                target_resistance_lookback=60,
            ),
            risk_config=RiskConfig(
                stop_loss_pct=STOP_LOSS_PCT,
                max_loss_per_trade=STOP_LOSS_PCT,
                trailing_stop_pct=0.99,  # 禁用移动止损（设为99%，永远不触发）
                atr_multiplier=2.0,
                max_consecutive_losses=3,
            ),
            max_hold_days=MAX_HOLD_DAYS,
            max_trades_per_day=MAX_TRADES_PER_DAY,
        )
        self.entry_checker = EntrySignalChecker(self.config.entry_config)
        self.risk_manager = RiskManager(self.config.risk_config)

        # 账户状态
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.positions: Dict[str, Dict] = {}
        self.holding_days: Dict[str, int] = {}
        self.trade_records: List[TradeRecord] = []
        self.equity_curve: List[Dict] = []

        # 敞口熔断状态
        self.equity_high_water_mark = initial_capital
        self.circuit_breaker = False  # 熔断激活时禁止新入场

        # 调试统计
        self._stat_candidates = 0
        self._stat_no_data = 0
        self._stat_bottom_fail = 0
        self._stat_bottom_cooldown = 0
        self._stat_entry_fail = 0
        self._stat_pos_zero = 0
        self._stat_not_top_sector = 0   # 板块不在动量前50%

        # 板块动量追踪（每个交易日的板块平均涨幅）
        self._sector_momentum: Dict[datetime, Dict[str, float]] = {}

    def _calculate_sector_momentum(self, date: datetime) -> Dict[str, float]:
        """计算当日各板块动量（平均涨幅）"""
        sector_returns: Dict[str, List[float]] = {}
        for sym, df in self.stock_data.items():
            if date not in df.index:
                continue
            prev_idx = df.index.get_loc(date) - 1
            if prev_idx < 0:
                continue
            prev_date = df.index[prev_idx]
            prev_close = float(df.loc[prev_date, "close"])
            curr_close = float(df.loc[date, "close"])
            if prev_close <= 0:
                continue
            ret = (curr_close - prev_close) / prev_close
            sector = self.sector_pool.get(sym, {}).get('industry', '其他')
            sector_returns.setdefault(sector, []).append(ret)

        return {
            sector: np.mean(rets)
            for sector, rets in sector_returns.items()
            if rets
        }

    def _is_top_sector(self, date: datetime, sector: str) -> bool:
        """判断板块是否在当日动量前50%"""
        momentum = self._sector_momentum.get(date, {})
        if not momentum:
            return True  # 无数据时默认放行
        sorted_sectors = sorted(momentum.items(), key=lambda x: x[1], reverse=True)
        top_half = [s for s, _ in sorted_sectors[:len(sorted_sectors)//2 + 1]]
        return sector in top_half

    def run(self, date_start: str, date_end: str) -> pd.DataFrame:
        """运行回测"""
        test_dates = [
            d for d in self.trade_dates
            if date_start <= str(d.date()) <= date_end
        ]
        print(f"\n  回测区间: {date_start} ~ {date_end}, 共 {len(test_dates)} 个交易日")
        print(f"  初始资金: {self.initial_capital:,.0f}")
        print(f"  形态识别: 回调≥{DOWN_AMPLITUDE*100:.0f}%, |日涨跌|≤6%, 窄幅筑底≤2%, 年线上方, 冷却{COOLDOWN_DAYS}天")
        print(f"  D目标: ATR*{ATR_TARGET_MULTIPLIER:.0f} | C选股: 板块前{REL_STRENGTH_TOP_PCT*100:.0f}%")
        print(f"  止损{STOP_LOSS_PCT*100:.0f}% | 持仓≤{MAX_HOLD_DAYS}天 | 每日上限{MAX_TRADES_PER_DAY}笔")
        print(f"  敞口上限: 总{MAX_CONCURRENT_POSITIONS}笔 | 单板块{MAX_POSITIONS_PER_SECTOR}笔")
        print(f"  熔断: 回撤>{CIRCUIT_BREAKER_DRAWDOWN*100:.0f}%停入场, 回到{CIRCUIT_BREAKER_RESUME*100:.0f}%内恢复")
        print("-" * 60)

        t0 = time.time()
        for t_idx, date in enumerate(test_dates):
            # 预计算当日板块动量
            self._sector_momentum[date] = self._calculate_sector_momentum(date)

            self._check_exits(date)
            if not self.risk_manager.is_paused and not self.circuit_breaker:
                self._check_entries(date)
            self._record_equity(date)

            if (t_idx + 1) % 100 == 0:
                elapsed = time.time() - t0
                rate = (t_idx + 1) / elapsed
                eta = (len(test_dates) - t_idx - 1) / max(rate, 0.001)
                print(f"    [{t_idx+1}/{len(test_dates)}] "
                      f"耗时{elapsed:.0f}s | {rate:.0f}天/秒 | 剩余~{eta:.0f}s "
                      f"| 持仓{len(self.positions)} | 交易{len(self.trade_records)}")

        print("-" * 60)
        elapsed = time.time() - t0
        print(f"  回测完成: {len(self.trade_records)} 笔交易, 耗时 {elapsed:.1f}s")

        # 调试统计
        total = max(self._stat_candidates, 1)
        print(f"\n  === 调试统计 ===")
        print(f"  候选扫描:        {self._stat_candidates:>6,}")
        print(f"  数据不足:        {self._stat_no_data:>6,} ({self._stat_no_data/total:.1%})")
        print(f"  板块动量靠后:    {self._stat_not_top_sector:>6,} ({self._stat_not_top_sector/total:.1%})")
        print(f"  止跌形态未通过:  {self._stat_bottom_fail:>6,} ({self._stat_bottom_fail/total:.1%})")
        print(f"  止跌形态冷却中:  {self._stat_bottom_cooldown:>6,} ({self._stat_bottom_cooldown/total:.1%})")
        print(f"  入场信号未通过: {self._stat_entry_fail:>6,} ({self._stat_entry_fail/total:.1%})")
        print(f"  仓位为0:        {self._stat_pos_zero:>6,} ({self._stat_pos_zero/total:.1%})")
        actual_enter = self._stat_candidates - self._stat_no_data - self._stat_not_top_sector - self._stat_bottom_fail - self._stat_bottom_cooldown - self._stat_entry_fail - self._stat_pos_zero
        print(f"  实际入场:       {actual_enter:>6,}")

        return pd.DataFrame(self.equity_curve)

    def _check_exits(self, date) -> None:
        """检查持仓退出"""
        for code, pos in list(self.positions.items()):
            df = self.stock_data.get(code)
            if df is None or date not in df.index:
                continue

            current_price = float(df.loc[date, "close"])
            hold_days = self.holding_days.get(code, 0) + 1

            if current_price > pos.get("high_since", pos["entry_price"]):
                pos["high_since"] = current_price

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
                # D：峰值回撤退出——持仓30天后，从最高点回撤≥5%且跌破入场价才退出
                peak = pos.get("high_since", pos["entry_price"])
                peak_drawdown = (peak - current_price) / peak if peak > 0 else 0
                below_entry = current_price <= pos["entry_price"]
                if peak_drawdown >= 0.05 and below_entry:
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

    def _calc_relative_strength(
        self,
        hist: pd.DataFrame,
        date: datetime,
        sector: str,
    ) -> float:
        """计算个股在板块内的相对强度（5日超额收益）

        Returns
        -------
        float
            个股5日涨幅 - 板块平均5日涨幅（正值 = 相对强势）
        """
        if len(hist) < 6:
            return 0.0

        # 个股5日涨幅
        prev_close = float(hist["close"].iloc[-6])
        if prev_close <= 0:
            return 0.0
        stock_5d_ret = (float(hist["close"].iloc[-1]) - prev_close) / prev_close

        # 板块内所有5日涨幅的平均值
        sector_5d_rets = []
        for sym, df in self.stock_data.items():
            sec = self.sector_pool.get(sym, {}).get('industry', '其他')
            if sec != sector:
                continue
            if date not in df.index or len(df.loc[:date]) < 6:
                continue
            sub_hist = df.loc[:date]
            prev_close = float(sub_hist["close"].iloc[-6])
            if prev_close <= 0:
                continue
            ret = (float(sub_hist["close"].iloc[-1]) - prev_close) / prev_close
            sector_5d_rets.append(ret)

        sector_avg = mean(sector_5d_rets) if sector_5d_rets else 0
        return stock_5d_ret - sector_avg

    def _check_entries(self, date) -> None:
        """扫描板块内候选股（两阶段：筛选→排序→入场）"""
        candidates: List[Dict] = []  # 所有通过各层筛选的候选

        for code in self.stock_data:
            if code in self.positions:
                continue

            df = self.stock_data[code]
            self._stat_candidates += 1

            if date not in df.index or len(df.loc[:date]) < 250:
                self._stat_no_data += 1
                continue

            # 板块动量过滤
            sector = self.sector_pool.get(code, {}).get('industry', '其他')
            if not self._is_top_sector(date, sector):
                self._stat_not_top_sector += 1
                continue

            hist = df.loc[:date]

            # 止跌形态
            recognizer = self.pattern_recognizers[code]
            is_bottom, bottom_info = recognizer.is_bottom_pattern(hist)
            if not is_bottom:
                if bottom_info.get("cooldown"):
                    self._stat_bottom_cooldown += 1
                else:
                    self._stat_bottom_fail += 1
                continue

            # 均线支撑确认：收盘价站上 5 日均线
            if len(hist) >= 5:
                ma5 = float(hist["close"].iloc[-5:].mean())
                curr_close = float(hist["close"].iloc[-1])
                if curr_close < ma5:
                    self._stat_entry_fail += 1
                    continue

            # 固定止损
            entry_price = float(hist["close"].iloc[-1])
            stop_price = entry_price * (1 - self.config.risk_config.stop_loss_pct)

            # 入场信号
            can_enter, entry_info = self.entry_checker.can_enter(
                hist, stop_price, "14:50"
            )
            if not can_enter:
                self._stat_entry_fail += 1
                continue

            # D：ATR自适应目标价
            atr = RiskManager.calculate_atr(hist, period=14)
            target_price = entry_price + atr * ATR_TARGET_MULTIPLIER
            # 保底：目标价至少比入场价高5%
            target_price = max(target_price, entry_price * 1.05)

            # C：计算相对强度
            rel_strength = self._calc_relative_strength(hist, date, sector)

            candidates.append({
                "code": code,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "sector": sector,
                "rel_strength": rel_strength,
                "bottom_info": bottom_info,
            })

        # C：按板块分组，按相对强度排序，取前50%
        if not candidates:
            return

        sector_groups: Dict[str, List[Dict]] = defaultdict(list)
        for c in candidates:
            sector_groups[c["sector"]].append(c)

        selected: List[Dict] = []
        for sec, cands in sector_groups.items():
            # 按相对强度降序排序
            cands.sort(key=lambda x: x["rel_strength"], reverse=True)
            top_n = max(1, int(len(cands) * REL_STRENGTH_TOP_PCT))
            selected.extend(cands[:top_n])
            filtered_out = len(cands) - top_n
            # 调试：记录被过滤的数量
            for _ in range(filtered_out):
                self._stat_entry_fail += 1

        # 按相对强度排序（全局），再按每日上限入场
        selected.sort(key=lambda x: x["rel_strength"], reverse=True)
        trades_today = 0

        for c in selected:
            if trades_today >= self.config.max_trades_per_day:
                break

            # 仓位计算
            shares, pos_info = self.risk_manager.calculate_position_size(
                self.capital, c["entry_price"], c["stop_price"]
            )
            if shares <= 0:
                self._stat_pos_zero += 1
                continue

            # 1: 总持仓上限
            if len(self.positions) >= MAX_CONCURRENT_POSITIONS:
                continue

            # 2: 单板块上限
            sector_positions = sum(
                1 for p in self.positions
                if self.sector_pool.get(p, {}).get('industry', '其他') == c["sector"]
            )
            if sector_positions >= MAX_POSITIONS_PER_SECTOR:
                continue

            interrupted = c["bottom_info"].get("cooldown_interrupted", False)
            cost = c["entry_price"] * shares
            if cost > self.capital:
                shares = int(self.capital / c["entry_price"])
                if shares <= 0:
                    self._stat_pos_zero += 1
                    continue
                cost = c["entry_price"] * shares

            self.capital -= cost
            self.positions[c["code"]] = {
                "entry_price": c["entry_price"],
                "shares": shares,
                "stop_price": c["stop_price"],
                "target_price": c["target_price"],
                "entry_date": str(date.date()),
                "high_since": c["entry_price"],
                "cooldown_interrupted": interrupted,
            }
            self.holding_days[c["code"]] = 0
            trades_today += 1

    def _record_equity(self, date) -> None:
        """记录每日净值，并检查利润回撤保护"""
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

        # 敞口熔断：回撤>10%停入场，回到新高前3%内恢复
        if total > self.equity_high_water_mark:
            self.equity_high_water_mark = total
            if self.circuit_breaker:
                self.circuit_breaker = False
                self.risk_manager.reset_pause()
        elif total < self.equity_high_water_mark * (1 - CIRCUIT_BREAKER_DRAWDOWN):
            if not self.circuit_breaker:
                self.circuit_breaker = True
        elif total >= self.equity_high_water_mark * (1 - CIRCUIT_BREAKER_RESUME):
            if self.circuit_breaker:
                self.circuit_breaker = False
                self.risk_manager.reset_pause()


def main():
    print("=" * 60)
    print("  援军战法 板块池回测 v1（券商+半导体）")
    print("  数据源: SQLite 5分钟K线 → 日线聚合")
    print(f"  回测区间: {DATE_START} ~ {DATE_END}")
    print("  参数: 冷却0天 | 止损10% | ATR*3目标 | 持仓30天 | 板块内相对强势选股")
    print("=" * 60)

    # 1. 加载板块池
    sector_pool = load_sector_pool()
    sec_count = sum(1 for v in sector_pool.values() if v['industry'] == '券商')
    semi_count = sum(1 for v in sector_pool.values() if v['industry'] == '半导体')
    print(f"\n  板块池: 券商{sec_count}只 + 半导体{semi_count}只 = {len(sector_pool)}只")

    # 2. 加载数据
    stock_data = load_daily_from_db_sector(sector_pool, DATE_START, DATE_END)

    # 3. 交易日
    all_dates_set: set = set().union(*[set(d.index) for d in stock_data.values()])
    trade_dates = sorted(all_dates_set)

    # 4. 构建回测
    bt = SectorBacktest(
        stock_data=stock_data,
        sector_pool=sector_pool,
        trade_dates=trade_dates,
        initial_capital=INITIAL_CAPITAL,
    )

    # 5. 运行
    eq = bt.run(DATE_START, DATE_END)

    # 6. 绩效分析
    print(f"\n{'=' * 60}")
    print(f"  绩效分析")
    print(f"{'=' * 60}")
    evaluator = BacktestEvaluator(initial_capital=INITIAL_CAPITAL)
    result = evaluator.evaluate(eq, bt.trade_records)
    evaluator.print_report(result)

    # 7. 冷却打断统计
    interrupted_trades = [r for r in bt.trade_records if r.cooldown_interrupted]
    normal_trades = [r for r in bt.trade_records if not r.cooldown_interrupted]
    print(f"\n  冷却打断统计:")
    print(f"    普通触底入场: {len(normal_trades)} 笔")
    print(f"    打断冷却入场: {len(interrupted_trades)} 笔")
    if interrupted_trades:
        int_win = sum(1 for r in interrupted_trades if r.is_win)
        print(f"    打断入场胜率: {int_win}/{len(interrupted_trades)} = {int_win/len(interrupted_trades):.1%}")

    # 8. 交易列表
    if bt.trade_records:
        print(f"\n  === 交易记录 ===")
        for r in bt.trade_records:
            sector = sector_pool.get(r.vt_symbol, {}).get('industry', '?')
            name = sector_pool.get(r.vt_symbol, {}).get('name', '?')
            print(f"  {r.entry_date} → {r.exit_date} | {r.vt_symbol}({name}) | "
                  f"{sector} | {'赢' if r.is_win else '亏'} | {r.exit_reason} | "
                  f"{r.pnl_pct*100:.1f}% | {r.hold_days}天 | 打断{int(r.cooldown_interrupted)}")

    # 9. 保存净值曲线
    out_path = f"{PROJECT_ROOT}/.cache/sector_backtest_2024_2026.pkl"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pd.to_pickle(eq, out_path)
    print(f"\n  净值曲线已保存: {out_path}")


if __name__ == "__main__":
    main()
