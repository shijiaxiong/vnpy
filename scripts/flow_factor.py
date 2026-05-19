"""
P2: 可选因子插件 - 资金流向因子

通过 NeoData 查询个股当日资金流向，对龙头评分进行调整。
实时环境下生效，回测/无数据时返回0调整，不影响原有逻辑。
"""

import json as _json
import re
import subprocess
from abc import ABCMeta, abstractmethod
from typing import Dict, List

import pandas as pd

NEODATA_SCRIPT = "/Users/zyb/.workbuddy/plugins/marketplaces/cb_teams_marketplace/plugins/finance-data/skills/neodata-financial-search/scripts/query.py"
VNPV_ENV_PYTHON = "/Users/zyb/go/src/github/vnpy/vnpy_env/bin/python3"


class ScoreAdjuster(metaclass=ABCMeta):
    """评分调整器基类

    可选注入到 SectorLeaderSelector，对龙头评分做调整。
    不依赖外部数据时返回空 dict，不影响原有评分逻辑。
    """

    @abstractmethod
    def get_adjustments(self, stock_codes: List[str]) -> Dict[str, float]:
        """返回 {stock_code: score_adjustment}

        Parameters
        ----------
        stock_codes : List[str]
            baostock 格式的股票代码列表

        Returns
        -------
        Dict[str, float]
            评分调整量（正数加分，负数减分），默认空 dict
        """
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class NoopAdjuster(ScoreAdjuster):
    """空调整器 - 始终返回空，供回测/无数据时使用"""

    def get_adjustments(self, stock_codes: List[str]) -> Dict[str, float]:
        return {}


class NeoDataFlowAdjuster(ScoreAdjuster):
    """资金流向调整器

    通过 NeoData 查询个股主力资金净流入，按板块内百分位映射为 [-10, +10] 的评分调整。

    Parameters
    ----------
    max_adjustment : float
        最大调整幅度，默认 10.0 分
    """

    def __init__(self, max_adjustment: float = 10.0):
        self.max_adjustment = max_adjustment
        self._last_flows: Dict[str, float] = {}
        """最近一次查询的资金流数据，用于调试"""

    def get_adjustments(self, stock_codes: List[str]) -> Dict[str, float]:
        if not stock_codes:
            return {}

        # 转为 NeoData 格式
        nd_codes = [
            c.replace("sh.", "").replace("sz.", "")
            + (".SH" if c.startswith("sh.") else ".SZ")
            for c in stock_codes
            if len(c) > 5
        ]
        if not nd_codes:
            return {}

        # 按名称查询
        code_str = "、".join(nd_codes[:15])  # 单次最多15只
        query = f"{code_str}今日资金流向"
        flow_data = self._query_flows(query)

        if not flow_data:
            return {}

        self._last_flows = flow_data

        # 转换回 baostock 格式
        bs_adjustments = {}
        bs_flows = {}
        for nd_code, flow in flow_data.items():
            sym, ex = nd_code.split(".")
            bs_code = f"{ex.lower()}.{sym}"
            if bs_code in stock_codes:
                bs_flows[bs_code] = flow

        if not bs_flows:
            return {}

        # 板块内百分位排名映射
        values = pd.Series(bs_flows)
        percentiles = values.rank(pct=True)

        # 百分位 [0,1] → 调整量 [-max, +max]
        for code, pct in percentiles.items():
            adjustment = (pct - 0.5) * 2 * self.max_adjustment
            bs_adjustments[code] = round(adjustment, 2)

        return bs_adjustments

    def _query_flows(self, query: str) -> Dict[str, float]:
        """查询 NeoData 资金流向

        Returns
        -------
        Dict[str, float]
            {neo_data_code: 主力净流入金额}
        """
        try:
            result = subprocess.run(
                [VNPV_ENV_PYTHON, NEODATA_SCRIPT, "--query", query, "--data-type", "api"],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {}

        if result.returncode != 0:
            return {}

        try:
            data = _json.loads(result.stdout)
        except Exception:
            return {}

        flows = {}
        for recall in data.get("data", {}).get("apiData", {}).get("apiRecall", []):
            content = recall.get("content", "")
            # 匹配: 股票名A股(代码：688256.SH)...主力净流入136778467元
            pattern = re.compile(
                r"[\u4e00-\u9fa5]+A股\(代码：([\d.]+\.(?:SH|SZ))\).*?"
                r"主力净流入(-?[\d,\.]+)元",
                re.DOTALL,
            )
            for match in pattern.finditer(content):
                code = match.group(1)
                flow_str = match.group(2).replace(",", "")
                try:
                    flows[code] = float(flow_str)
                except ValueError:
                    pass

        return flows
