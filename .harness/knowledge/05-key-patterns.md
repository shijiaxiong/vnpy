<!-- SUMMARY: VeighNa 跨模块代码模式汇总，包含事件驱动、Gateway 接入、策略引擎集成等常见实现范式 -->
# 关键代码模式

项目中反复出现但不易从单个文件推断的模式，供新功能实现时参照。

## 模式一：事件驱动数据流

EventEngine 是框架数据流的唯一通路。

触发方（Gateway 收到行情）：
```python
tick = TickData(gateway_name=self.gateway_name, ...)
self.on_tick(tick)  # BaseGateway.on_tick -> event_engine.put(Event(EVENT_TICK + symbol, tick))
```

消费方（App/策略注册回调）：
```python
self.main_engine.event_engine.register(EVENT_TICK + symbol, self.process_tick_event)
```

陷阱：handler 在事件线程执行，若需更新 Qt UI，须通过 Qt signal 切换到主线程，不得直接操作 Widget。

## 模式二：Gateway 接入

新 Gateway 必须继承 `BaseGateway`（vnpy/trader/gateway.py），实现以下抽象方法：
- `connect(setting: dict) -> None`：连接交易所，不阻塞主线程（启动后台线程）
- `close() -> None`：断开连接
- `subscribe(req: SubscribeRequest) -> None`：订阅行情
- `send_order(req: OrderRequest) -> str`：下单，返回本地 orderid
- `cancel_order(req: CancelRequest) -> None`：撤单
- `query_account() -> None`：查询资金
- `query_position() -> None`：查询持仓
- `query_history(req: HistoryRequest) -> list[BarData]`：查询历史数据

回调数据推送（继承自 BaseGateway）：
```python
self.on_tick(tick)       # 推送行情
self.on_order(order)     # 推送委托
self.on_trade(trade)     # 推送成交
self.on_position(pos)    # 推送持仓
self.on_account(acct)    # 推送资金
self.on_contract(contract) # 推送合约
self.write_log(msg)      # 写日志
```

## 模式三：App/Engine 注册

App 需继承 `BaseApp`（vnpy/trader/app.py）并注册到 MainEngine：

```python
class CtaStrategyApp(BaseApp):
    app_name = "CtaStrategy"
    app_module = __module__
    app_path = Path(__file__).parent
    display_name = "CTA策略"
    engine_class = CtaEngine      # BaseEngine 子类
    widget_class = CtaManager     # QWidget 子类（可选）
```

Engine 通过 `main_engine.get_engine(engine_name)` 获取，不持有 App 实例。

## 模式四：策略基类模式

CTA/Portfolio 等策略引擎提供策略基类（各 App 包内定义），策略开发者继承后实现：
- `on_init() -> None`：策略初始化（加载历史数据、计算初始指标）
- `on_start() -> None`：启动
- `on_stop() -> None`：停止
- `on_tick(tick: TickData) -> None`：实时 Tick 回调
- `on_bar(bar: BarData) -> None`：K线回调
- `on_order(order: OrderData) -> None`：委托回调
- `on_trade(trade: TradeData) -> None`：成交回调

陷阱：策略回调均在事件线程；策略下单通过引擎提供的 buy/sell/short/cover 方法，不直接调用 MainEngine。

## 模式五：Alpha 研究工作流

Lab 串联数据获取、因子计算、模型训练和策略回测：

```python
lab = Lab()
lab.load_data(symbols, start_date, end_date)   # 加载原始行情
lab.run_dataset(dataset)                        # 因子特征工程，产出 features DataFrame
lab.train_model(model)                          # 模型训练，产出预测信号
lab.run_strategy(strategy)                      # 回测，产出绩效报告
```

与实盘解耦：Lab 不依赖 EventEngine 和 MainEngine，数据来自 database 适配层或外部 datafeed。

## 模式六：数据库适配器

新增数据库支持通过实现 `BaseDatabase`（vnpy/trader/database.py）接口，在 `vt_setting.json` 中配置 `database.name` 激活。

不同适配器的 `gateway_name` 约定：从数据库加载时 gateway_name 填 `"DB"`，不影响数据源路由逻辑。

## 模式七：RPC 分布式部署

服务端（RpcService App）：将本机 MainEngine 作为事件/行情/交易路由服务暴露
客户端：RpcGateway 连接服务端，对策略透明，与普通 Gateway 用法一致

ZeroMQ 通讯端口配置在 vt_setting.json，默认 REP=2014 / PUB=4102。
