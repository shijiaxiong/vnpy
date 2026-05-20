<!-- SUMMARY: VeighNa 分层架构描述，包含各层职责、模块边界约束和关键依赖规则 -->
# 架构与模块边界

## 分层

- 表现层（vnpy/trader/ui/）：PySide6 图形界面，MainWindow 承载各 App 的面板，通过事件订阅刷新显示
- 引擎层（vnpy/trader/engine.py）：MainEngine 作为核心路由，持有 EventEngine、Gateway 注册表、App 注册表；BaseEngine 提供策略/功能引擎抽象
- 接口层（vnpy/trader/gateway.py）：BaseGateway 抽象交易接口（connect/subscribe/send_order/cancel_order），各 Gateway 实现独立发布为 vnpy_xxx 包
- App 层（各 vnpy_xxx App 包）：策略引擎（CTA、Portfolio 等）、工具类引擎（DataManager、RiskManager 等）继承 BaseApp，通过 MainEngine 注册
- 数据层（vnpy/trader/object.py + database.py）：统一数据对象（TickData/BarData/OrderData 等）+ BaseDatabase 适配器接口
- Alpha 模块（vnpy/alpha/）：独立的 AI 量化研究子系统，Dataset / Model / Strategy / Lab；仅依赖 vnpy/trader/object.py 数据结构，与实盘引擎解耦

## 模块边界

- Gateway 层只负责协议翻译，不含任何业务逻辑；不得直接调用 App 层接口
- App/Engine 层通过 MainEngine API 调用 Gateway（send_order / cancel_order / subscribe），不持有 Gateway 实例
- UI 层只通过 EventEngine.register() 订阅事件来刷新界面，不直接调用 Engine 写操作
- 策略代码不得直接操作数据库，须通过 MainEngine 获取 DatabaseEngine 进行读写
- Alpha 模块（vnpy/alpha/）不依赖 vnpy/trader/engine.py，保持独立可测试性
- RPC 模块（vnpy/rpc/）只作通讯层，不含交易逻辑

## 关键约束

- 事件类型常量定义在 vnpy/trader/event.py，新增事件类型必须在此集中注册
- 所有跨引擎数据传递只能通过 EventEngine 的事件机制，禁止直接函数调用
- UI 更新必须在 Qt 主线程执行；EventEngine handler 运行在事件线程，需通过 Qt signal 或 QMetaObject 切换到主线程
- BaseGateway 子类必须实现所有抽象方法；connect() 中不得阻塞主线程
- 数据对象（TickData/BarData 等）使用 @dataclass 定义，不得修改已推送对象的字段（不可变约定）
