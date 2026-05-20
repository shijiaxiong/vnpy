<!-- SUMMARY: VeighNa 核心数据对象定义、事件类型、数据库接口规范和数据边界约定 -->
# 数据与类型边界

## 核心数据对象

所有数据对象定义在 `vnpy/trader/object.py`，继承 `BaseData`（必须含 `gateway_name: str`）：

- `TickData`：实时行情（symbol/exchange/datetime/价格/盘口五档）
- `BarData`：K线数据（symbol/exchange/datetime/interval/open/high/low/close/volume）
- `OrderData`：委托（orderid/type/direction/offset/price/volume/status）
- `TradeData`：成交（orderid/tradeid/direction/offset/price/volume）
- `PositionData`：持仓（symbol/exchange/direction/volume/price/pnl）
- `AccountData`：资金（accountid/balance/frozen/available）
- `ContractData`：合约信息（symbol/exchange/product/size/pricetick 等）
- `LogData`：日志（msg/level）

枚举定义在 `vnpy/trader/constant.py`：
- `Direction`：LONG / SHORT / NET
- `Exchange`：CFFEX / SHFE / DCE / CZCE / INE / GFEX / SSE / SZSE / NYSE / ... （完整列表见文件）
- `Interval`：MINUTE / HOUR / DAILY / WEEKLY / TICK
- `Offset`：NONE / OPEN / CLOSE / CLOSETODAY / CLOSEYESTERDAY
- `Status`：SUBMITTING / NOTTRADED / PARTTRADED / ALLTRADED / CANCELLED / REJECTED
- `Product`：EQUITY / FUTURES / OPTION / INDEX / FOREX / SPOT / ETF / BOND / WARRANT / SPREAD / FUND / CFD / OTC / LOAN
- `OrderType`：LIMIT / MARKET / STOP / FAK / FOK / RFQ

活跃委托状态集合：`ACTIVE_STATUSES = {Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED}`

## 请求与指令对象

- `SubscribeRequest`：行情订阅请求（symbol/exchange）
- `OrderRequest`：下单请求（symbol/exchange/direction/type/volume/price/offset/reference）
- `CancelRequest`：撤单请求（orderid/symbol/exchange）
- `HistoryRequest`：历史数据请求（symbol/exchange/start/end/interval）
- `QuoteRequest` / `QuoteData`：询价相关

## 事件类型

定义在 `vnpy/trader/event.py`：
```
EVENT_TICK = "eTick."         # 行情 Tick
EVENT_BAR = "eBar."           # K线
EVENT_ORDER = "eOrder."       # 委托
EVENT_TRADE = "eTrade."       # 成交
EVENT_POSITION = "ePosition." # 持仓
EVENT_ACCOUNT = "eAccount."   # 资金
EVENT_CONTRACT = "eContract." # 合约
EVENT_LOG = "eLog"            # 日志
EVENT_QUOTE = "eQuote."       # 询价
```

## 数据库接口

`BaseDatabase`（vnpy/trader/database.py）抽象接口，各数据库适配器实现：
- `save_bar_data(bars: list[BarData]) -> bool`
- `save_tick_data(ticks: list[TickData]) -> bool`
- `load_bar_data(symbol, exchange, interval, start, end) -> list[BarData]`
- `load_tick_data(symbol, exchange, start, end) -> list[TickData]`
- `delete_bar_data(symbol, exchange, interval) -> int`
- `delete_tick_data(symbol, exchange) -> int`
- `get_bar_overview() -> list[BarOverview]`
- `get_tick_overview() -> list[TickOverview]`

当前默认适配器：SQLite（vnpy_sqlite）

## 运行时配置文件

`~/.vnpy/vt_setting.json` 结构（示例，各 Gateway 扩展字段）：
```json
{
  "font.family": "微软雅黑",
  "font.size": 12,
  "log.active": true,
  "log.level": 20,
  "log.console": true,
  "log.file": true,
  "email.server": "",
  "email.port": 0,
  "email.username": "",
  "email.password": "",
  "email.sender": "",
  "email.receiver": "",
  "datafeed.name": "",
  "datafeed.username": "",
  "datafeed.password": "",
  "database.name": "sqlite",
  "database.database": "database.db",
  "database.host": "localhost",
  "database.port": 3306,
  "database.user": "root",
  "database.password": ""
}
```

## 边界约定

- UI 层不直接构造 OrderRequest / SubscribeRequest，通过 Engine 提供的方法包装
- 历史数据加载按需分页，不全量加载到内存（HistoryRequest 指定时间范围）
- BarData 中 gateway_name 在从数据库加载时填 "DB"，从实盘推送时填 Gateway 名称
- Alpha 模块使用 polars DataFrame 内部处理，对外转换为 vnpy/trader/object.py 兼容格式
