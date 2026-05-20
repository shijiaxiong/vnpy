<!-- SUMMARY: VeighNa 稳定功能基线需求，含核心模块功能描述、非功能约束和版本管理规则 -->
# 产品需求 - 稳定固化(不频繁变更)

## 1. 极简摘要

- 产品：VeighNa -- 基于 Python 的量化交易系统开发框架，以事件驱动引擎为核心，接入多市场交易接口
- 用户：专业量化交易员、量化研究员、私募基金技术团队
- 结构：无固定 UI 导航结构；以 MainEngine 为中枢，各 App 作为独立功能模块嵌入
- 核心流程：Gateway 接收市场数据 -> EventEngine 分发事件 -> 策略 App 处理信号 -> 通过 MainEngine 下单 -> Gateway 执行

产品定位、体验原则与判断准则见 ./01-prd-sense.md。

---

## 2. 模块结构

```
核心框架（vnpy/）
  ├── event/          事件引擎
  ├── trader/         交易平台核心
  │   ├── engine.py   MainEngine（核心路由）
  │   ├── gateway.py  Gateway 接口抽象
  │   ├── app.py      App 模块抽象
  │   ├── object.py   数据对象
  │   ├── constant.py 枚举常量
  │   ├── database.py 数据库接口
  │   ├── datafeed.py 数据服务接口
  │   ├── setting.py  全局配置
  │   └── ui/         PySide6 GUI
  ├── rpc/            跨进程通讯
  ├── chart/          K线图表
  └── alpha/          AI 量化研究（独立子系统）

扩展包（独立发布，通过 pip 安装）
  ├── vnpy_ctp / vnpy_xtp / ...    Gateway 接口实现
  ├── vnpy_ctastrategy / ...       App 策略引擎
  ├── vnpy_sqlite / vnpy_mysql ... 数据库适配器
  └── vnpy_rqdata / vnpy_xt / ...  数据服务适配器
```

---

## 3. 功能需求

### 3.1 事件驱动引擎（vnpy/event）

- 支持事件 register / unregister / put 操作
- 独立事件处理线程，队列消费，handler 顺序执行
- 支持定时事件（Timer Event），驱动心跳和周期任务

### 3.2 交易平台核心（vnpy/trader）

- MainEngine 统一管理 Gateway、App/Engine 注册与生命周期
- 支持多 Gateway 同时接入，按 gateway_name 路由
- 委托生命周期管理：创建 -> 报送 -> 成交/撤单 -> 结束
- 持仓和资金状态维护（LogEngine 缓存最新状态）
- 邮件告警支持（EmailEngine）
- 多语言支持（中英文切换，i18n 通过 babel）

### 3.3 交易接口（Gateway）

- BaseGateway 抽象接口覆盖：connect / close / subscribe / send_order / cancel_order / query_account / query_position / query_history
- 支持国内主流期货、证券、期权接口（CTP/XTP/UFT 等）和海外接口（IB/TAP 等）
- 实盘行情和历史数据查询统一接口

### 3.4 策略应用（App）

- CTA 策略引擎：单合约趋势策略，支持回测和实盘，支持细粒度委托控制
- CTA 回测模块：图形化回测分析，参数优化（遗传算法）
- 价差交易：自定义价差，算法交易和自动策略
- 期权交易：定价模型、隐含波动率、希腊值风险
- 组合策略：多合约 Alpha 策略，历史回测和实盘
- 算法交易：TWAP / Sniper / Iceberg / BestLimit
- 数据管理：历史数据导入/导出/查看
- 行情记录：实时录制 Tick/K线
- 风控管理：流控、数量、活动委托限制

### 3.5 AI 量化研究（vnpy/alpha）

- Dataset：因子特征工程，内置 Alpha 158 因子集
- Model：统一 ML 模型接口，内置 Lasso / LightGBM / MLP
- Strategy：截面多标的和时序单标的两种策略类型
- Lab：端到端投研工作流管理，内置可视化分析
- 与实盘交易解耦，不自动触发委托

### 3.6 数据与基础设施

- 数据库：SQLite（默认）/ MySQL / PostgreSQL / MongoDB / TDengine 适配
- 数据服务：RQData / 迅投研 / TuShare / Wind / iFinD 等适配
- RPC：ZeroMQ 跨进程通讯，支持分布式部署
- K线图表：大数据量高性能显示，实时更新

---

## 4. 非功能与约束

- 平台：Windows 11+ / Ubuntu 22.04+ / macOS，Python 3.10+（推荐 3.13）
- 体验：实盘下单延迟不受 EventEngine 队列积压影响（合理的事件吞吐）
- 数据：BarData / TickData 支持 CSV 导入导出；数据库按 symbol + exchange + interval 索引
- 类型安全：全量 mypy strict 类型检查，零 error
- 代码质量：ruff lint 零 warning，目标版本 Python 3.10
- 适配：同一套代码跨 Windows / Linux / macOS 运行，不使用平台专有 API（UI 层除外）
- 版本：遵循语义化版本（SemVer），当前 4.x；版本号在 `vnpy/__init__.py` 中定义

---

## 5. 版本与需求池

- 版本历史见 `CHANGELOG.md`
- 待办需求池和技术债见 `.harness/plans/debt-tracker.md`
