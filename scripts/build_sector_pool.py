"""
构建板块池：券商(J67) + 半导体（科创板688+主板知名龙头）
用于援军战法板块池回测
"""
import baostock as bs
import pandas as pd
import json
import os

OUT_PATH = "/Users/zyb/go/src/github/vnpy/.cache/sector_stocks.json"

# 已知半导体/集成电路/芯片龙头（主板，60xxxx/00xxxx）
SEMI_KNOWN = {
    # 集成电路设计
    "sh.600171": "上海贝岭",
    "sh.600460": "士兰微",
    "sh.600770": "综艺股份",
    "sh.603160": "汇顶科技",
    "sh.603290": "斯达半导",
    "sh.603501": "韦尔股份",
    "sh.603986": "兆易创新",
    "sh.605358": "立昂微",
    # 封测
    "sh.600584": "长电科技",
    "sz.002156": "通富微电",
    "sz.002185": "华天科技",
    # 半导体设备
    "sz.002008": "大族激光",
    "sz.002371": "北方华创",
    # 半导体材料
    "sh.600183": "生益科技",
    "sz.002409": "雅克科技",
    "sz.002129": "中环股份",
    # 芯片/通信相关
    "sz.000063": "中兴通讯",
    "sz.000938": "紫光股份",
    "sz.002049": "紫光国微",
    # LED/光电/第三代半导体
    "sh.600703": "三安光电",
    "sh.600345": "长江通信",
    # 封装载板/PCB
    "sz.002916": "深南电路",
    "sz.002938": "鹏鼎控股",
    # 其他电子
    "sh.600667": "太极实业",
    "sz.000021": "深科技",
    "sz.000100": "TCL科技",
    "sz.000725": "京东方A",
}

def main():
    bs.login()

    # 获取行业分类
    rs = bs.query_stock_industry()
    records = []
    while rs.error_code == '0' and rs.next():
        records.append(rs.get_row_data())
    df = pd.DataFrame(records, columns=rs.fields)

    # 主板
    main_board = df[df['code'].str.match(r'^(sh\.60|sz\.00)')].copy()

    # J67 资本市场服务 = 券商
    securities = main_board[main_board['industry'] == 'J67资本市场服务'].copy()

    bs.logout()

    # 合并
    stocks = {}

    # 券商 - baostock: sh.600xxx → 数据库: 600xxx; sz.000xxx → 000xxx
    for _, row in securities.iterrows():
        code_raw = row['code']  # sh.600xxx 或 sz.000xxx
        if code_raw.startswith('sh.'):
            code_db = code_raw[3:]   # sh.600xxx → 600xxx
        elif code_raw.startswith('sz.'):
            code_db = code_raw[3:]   # sz.000xxx → 000xxx
        else:
            code_db = code_raw
        stocks[code_db] = {
            'name': row['code_name'],
            'industry': '券商',
            'baostock_code': code_raw,
        }

    # 半导体 - 转为数据库格式: sh.600xxx → 600xxx; sz.000xxx → 000xxx
    for code, name in SEMI_KNOWN.items():
        baostock_code = code  # 已是 sh.600xxx 或 sz.000xxx 格式
        if baostock_code.startswith('sh.'):
            code_db = baostock_code[3:]   # sh.600xxx → 600xxx
        elif baostock_code.startswith('sz.'):
            code_db = baostock_code[3:]   # sz.000xxx → 000xxx
        else:
            code_db = baostock_code
        stocks[code_db] = {
            'name': name,
            'industry': '半导体',
            'baostock_code': baostock_code,
        }

    # 保存
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

    sec_count = sum(1 for v in stocks.values() if v['industry'] == '券商')
    semi_count = sum(1 for v in stocks.values() if v['industry'] == '半导体')
    print(f"券商: {sec_count} 只")
    print(f"半导体: {semi_count} 只")
    print(f"合计: {len(stocks)} 只")
    print(f"已保存: {OUT_PATH}")
    return stocks

if __name__ == "__main__":
    main()
