<!-- SUMMARY: VeighNa 领域术语表，覆盖交易核心概念、框架组件名称和 Alpha 模块术语 -->
# 术语表

## 框架核心

- MainEngine：交易平台核心引擎（vnpy/trader/engine.py），统一管理 EventEngine、Gateway 注册表和 App/Engine 注册表
- EventEngine：事件驱动引擎（vnpy/event/），维护事件队列和处理线程，支持事件 register / unregister / put
- BaseGateway：交易接口抽象基类（vnpy/trader/gateway.py），封装连接/订阅/下单/撤单/查询等操作
- BaseApp：应用模块基类（vnpy/trader/app.py），声明 app_name / engine_class / widget_class 等元信息
- BaseEngine：功能引擎基类（vnpy/trader/engine.py），与 MainEngine 协作实现具体业务逻辑
- SETTINGS：全局运行时配置对象（vnpy/trader/setting.py），从 vt_setting.json 加载

## 数据对象

- TickData：实时行情数据，含最新价、盘口五档、日内统计
- BarData：K线数据（OHLCV），含 Interval 枚举标记时间粒度
- OrderData：委托记录，含委托状态 Status
- TradeData：成交记录，每笔成交对应一条
- PositionData：持仓，按 symbol + exchange + direction 唯一标识
- AccountData：资金账户，含 balance / frozen / available
- ContractData：合约基础信息，含 product / size / pricetick / min_volume
- LogData：日志事件数据

## 请求对象

- SubscribeRequest：行情订阅请求
- OrderRequest：下单请求，含 symbol / exchange / direction / type / volume / price / offset
- CancelRequest：撤单请求，含 orderid / symbol / exchange
- HistoryRequest：历史数据请求，含时间范围和 Interval

## 枚举常量

- Direction：LONG（买/多）/ SHORT（卖/空）/ NET（净持仓）
- Exchange：交易所代码（SHFE/DCE/CZCE/CFFEX/INE/GFEX/SSE/SZSE 等国内，NYSE/NASDAQ 等海外）
- Interval：MINUTE / HOUR / DAILY / WEEKLY / TICK
- Offset：OPEN（开仓）/ CLOSE（平仓）/ CLOSETODAY（平今）/ CLOSEYESTERDAY（平昨）
- Status：SUBMITTING / NOTTRADED / PARTTRADED / ALLTRADED / CANCELLED / REJECTED
- Product：EQUITY（股票）/ FUTURES（期货）/ OPTION（期权）/ ETF / BOND（债券）/ SPOT（现货）等
- OrderType：LIMIT（限价）/ MARKET（市价）/ STOP（停损）/ FAK / FOK

## Alpha 模块

- Dataset：因子特征工程组件（vnpy/alpha/dataset/），从 BarData 计算多维因子特征
- Model：预测模型训练组件（vnpy/alpha/model/），封装 ML 算法标准接口
- Strategy：Alpha 策略组件（vnpy/alpha/strategy/），基于模型信号生成交易决策
- Lab：投研流程管理器（vnpy/alpha/lab.py），串联 Dataset -> Model -> Strategy 工作流
- Alpha 158：微软 Qlib 项目衍生的 158 维股票因子集（vnpy/alpha/dataset/datasets/alpha_158.py）

## 其他组件

- DataFeed：数据服务适配器接口，提供历史行情和基础数据
- RPC：跨进程通讯组件（vnpy/rpc/），基于 ZeroMQ，支持分布式部署
- OffsetConverter：平仓方向转换器（vnpy/trader/converter.py），处理上期所平今/平昨拆分逻辑
- ACTIVE_STATUSES：活跃委托状态集合 {SUBMITTING, NOTTRADED, PARTTRADED}
