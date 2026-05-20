<!-- SUMMARY: VeighNa 项目概览，包含技术栈、入口文件、核心流程和模块分工 -->
# 项目概览

## 一句话

VeighNa（vnpy）是面向专业量化交易员的开源 Python 量化交易系统开发框架，通过事件驱动引擎统一接入多市场交易接口，并提供策略引擎、数据管理和 AI 因子研究等一体化能力。

## 技术栈

- 语言：Python 3.10+（推荐 3.13），全量类型注解，mypy strict
- 构建：hatchling + babel（国际化）
- UI：PySide6 6.8.x + pyqtgraph（K线图表）+ qdarkstyle（暗色主题）
- 数据处理：numpy / pandas / ta-lib / polars（alpha 模块）
- AI/ML：scikit-learn / LightGBM / PyTorch（alpha 模块可选依赖）
- 事件通讯：自研 EventEngine（vnpy/event/）
- 跨进程通讯：ZeroMQ（pyzmq）
- 日志：loguru
- 代码质量：ruff（lint）+ mypy（类型检查）
- 运行时配置：`~/.vnpy/vt_setting.json`（SETTINGS，vnpy/trader/setting.py 读取）

## 入口与根状态

- 入口：用户自建 `run.py`，调用 `create_qapp()` 初始化 Qt 应用
- 核心引擎：`MainEngine`（vnpy/trader/engine.py），持有 EventEngine 实例，统一管理 Gateway / App / Engine
- 启动流程：`EventEngine.start()` -> `MainEngine.__init__()` -> `add_gateway() / add_app()` -> `MainWindow.showMaximized()` -> `qapp.exec()`
- 全局配置：`SETTINGS`（vnpy/trader/setting.py）从 `vt_setting.json` 加载，支持运行时覆写

## 核心流程

1. 事件分发：EventEngine 维护事件队列，处理线程不断消费事件，调用已注册的 handler
2. 行情订阅：App/策略通过 MainEngine 向 Gateway 发送 SubscribeRequest，Gateway 推送 TickData 事件
3. 委托下单：策略调用 MainEngine.send_order()，路由到对应 Gateway，返回 OrderData 事件
4. 成交推送：Gateway 收到成交回报后推送 TradeData 事件，策略 on_trade() 回调处理
5. 数据持久化：数据库适配层（vnpy/trader/database.py BaseDatabase 接口）统一读写 BarData / TickData
6. AI 因子研究：vnpy.alpha.Lab 串联 Dataset -> Model -> Strategy 工作流，与实盘交易模块解耦

## 文档与规则

操作约束见 `.harness/framework/FRAMEWORK.md`，知识库加载策略见 `.harness/PROJECT.md`。
