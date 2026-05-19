"""
获取 baostock 行业分类，输出券商+半导体板块股票代码
"""
import baostock as bs
import pandas as pd
import json
import os

OUT_PATH = "/Users/zyb/go/src/github/vnpy/.cache/sector_stocks.json"

def main():
    login = bs.login()
    print(f"登录: {login.error_msg}")

    # 获取所有股票的行业分类
    rs = bs.query_stock_industry()
    print(f"行业查询: {rs.error_msg}")

    records = []
    while rs.error_code == '0' and rs.next():
        records.append(rs.get_row_data())

    cols = rs.fields
    df = pd.DataFrame(records, columns=cols)
    print(f"共 {len(df)} 只股票有行业分类")
    print(f"行业列: {list(df.columns)}")
    print(f"\n所有行业:")
    print(df['industry'].value_counts().to_string())

    bs.logout()

    # 过滤：沪市主板(60xxxx) + 深市主板(00xxxx)
    main_board = df[
        df['code'].str.match(r'^(sh\.60|sz\.00)')
    ].copy()
    print(f"\n主板股票: {len(main_board)} 只")

    # 目标板块
    target_industries = [
        # 半导体/芯片/电子
        '半导体', '电子元件', '电子信息', '芯片', '集成电路',
        '光刻机', '第三代半导体', '半导体材料', '半导体设备',
        # 券商/多元金融
        '证券', '券商', '多元金融', '保险',
    ]

    # 模糊匹配
    mask = main_board['industry'].str.contains('|'.join(target_industries), na=False)
    selected = main_board[mask]
    print(f"\n匹配板块股票: {len(selected)} 只")
    print(selected[['code', 'code_name', 'industry']].to_string())

    # 转为 vnpy symbol 格式
    stocks = {}
    for _, row in selected.iterrows():
        code = row['code'].replace('sh.', 'sh').replace('sz.', 'sz')
        stocks[code] = {
            'name': row['code_name'],
            'industry': row['industry'],
            'code': row['code'],
        }

    # 保存
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

    print(f"\n已保存: {OUT_PATH}")
    print(f"共 {len(stocks)} 只板块股票")
    return stocks

if __name__ == "__main__":
    main()
