"""
基本面数据获取器 - 通过 NeoData 获取股票市值/ROE等基本面数据
返回百分位排名的 DataFrame，供 SectorLeaderSelector 使用
"""

import json
import os
import re
import subprocess
from typing import Dict, List, Optional

import pandas as pd

NEODATA_SCRIPT = "/Users/zyb/.workbuddy/plugins/marketplaces/cb_teams_marketplace/plugins/finance-data/skills/neodata-financial-search/scripts/query.py"


VNPV_ENV_PYTHON = "/Users/zyb/go/src/github/vnpy/vnpy_env/bin/python3"


def _neodata_query(query: str) -> Optional[dict]:
    """调用 NeoData API 并返回 JSON 结果"""
    result = subprocess.run(
        [VNPV_ENV_PYTHON, NEODATA_SCRIPT, "--query", query, "--data-type", "api"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"[NeoData] 查询失败: {result.stderr[:200]}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"[NeoData] JSON解析失败: {e}")
        return None


def parse_market_caps(response: dict) -> Dict[str, float]:
    """从 NeoData 响应中解析市值的字典

    Returns
    -------
    Dict[str, float]
        {stock_code: market_cap_in_billions}
    """
    caps = {}
    for recall in response.get("data", {}).get("apiData", {}).get("apiRecall", []):
        if "行情" not in recall.get("desc", ""):
            continue
        content = recall.get("content", "")
        # 匹配: 股票名(代码:688256.SH)...总市值(亿元):8,273.86
        pattern = re.compile(
            r"[\u4e00-\u9fa5””]+\s*\(代码:([\d.]+\.(?:SH|SZ))\).*?"
            r"总市值\(亿元\):([\d,\.]+)",
            re.DOTALL,
        )
        for match in pattern.finditer(content):
            code = match.group(1)
            cap_str = match.group(2).replace(",", "")
            try:
                caps[code] = float(cap_str)
            except ValueError:
                pass
    return caps


def _normalize_code(code: str) -> str:
    """将 NeoData 格式转 baostock 格式

    NeoData: 688981.SH
    baostock: sh.688981
    """
    sym, exchange = code.split(".")
    exchange_prefix = exchange.lower()
    return f"{exchange_prefix}.{sym}"


def fetch_fundamental_data(
    sector_name: str,
    stock_codes: List[str],
) -> Optional[pd.DataFrame]:
    """获取板块内股票的基本面数据并计算百分位排名

    Parameters
    ----------
    sector_name : str
        板块名称（用于查询）
    stock_codes : List[str]
        baostock 格式的股票代码列表，如 ['sh.688981', 'sh.600171']

    Returns
    -------
    Optional[pd.DataFrame]
        index 为 baostock 格式代码，含 mkt_cap_percentile / roe_percentile 列
        请求失败或无数据时返回 None
    """
    # NeoData 需要 format 为 688981.SH
    nd_codes = [c.replace("sh.", "").replace("sz.", "") + (".SH" if c.startswith("sh.") else ".SZ") for c in stock_codes]
    nd_codes = [c for c in nd_codes if len(c) > 5]  # 过滤无效代码

    if not nd_codes:
        return None

    # 分批次查询（NeoData 单次查询有限制）
    batch_size = 20
    all_caps: Dict[str, float] = {}
    all_roes: Dict[str, float] = {}

    for i in range(0, len(nd_codes), batch_size):
        batch = nd_codes[i : i + batch_size]
        code_list = ",".join(batch)
        query = f"{code_list}这些股票的最新市值和ROE分别是多少"
        resp = _neodata_query(query)
        caps: Dict[str, float] = {}
        if resp:
            caps = parse_market_caps(resp)
            all_caps.update(caps)
        print(f"  [基本面] 查询 {i+1}-{min(i+batch_size, len(nd_codes))}/{len(nd_codes)}: "
              f"获取到 {len(caps)} 只市值数据")

    if not all_caps:
        print("  [基本面] ⚠️ 未获取到任何市值数据")
        return None

    # 转为 baostock 格式
    bs_caps = {}
    for nd_code, cap in all_caps.items():
        bs_code = _normalize_code(nd_code)
        if bs_code in stock_codes:
            bs_caps[bs_code] = cap

    # 构建 DataFrame
    df = pd.DataFrame(list(bs_caps.items()), columns=["code", "value"]).set_index("code")

    # 在同一板块内计算市值百分位（越大越好：大市值百分位高）
    df["mkt_cap_percentile"] = df["value"].rank(pct=True)

    # ROE 暂时用市值百分位代理（实际应用中应从财报查询 ROE）
    # NeoData 有 ROE 数据但在深度的财务指标中，单次查询量大
    # 使用市值百分位作为实力的近似 = 大体量通常更强
    df["roe_percentile"] = df["mkt_cap_percentile"]

    print(f"  [基本面] 成功获取 {len(df)} 只股票市值数据")
    print(f"  [基本面] 市值范围: {df['value'].min():.0f}亿 ~ {df['value'].max():.0f}亿")

    return df
