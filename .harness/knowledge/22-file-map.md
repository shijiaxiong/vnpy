<!-- SUMMARY: VeighNa 功能与文件映射，按模块列出源文件路径，方便快速定位实现代码 -->
# 功能与文件映射

## 框架入口与全局

- 包入口：`vnpy/__init__.py`（版本号）
- 事件引擎：`vnpy/event/__init__.py`（EventEngine, Event）
- 核心引擎：`vnpy/trader/engine.py`（MainEngine, BaseEngine）
- 全局配置：`vnpy/trader/setting.py`（SETTINGS, vt_setting.json）
- 日志：`vnpy/trader/logger.py`（loguru logger 封装）

## 数据对象与常量

- 数据对象：`vnpy/trader/object.py`（TickData / BarData / OrderData / TradeData / PositionData / AccountData / ContractData / LogData 等）
- 枚举常量：`vnpy/trader/constant.py`（Direction / Exchange / Interval / Offset / Status / Product / OrderType 等）
- 事件类型：`vnpy/trader/event.py`（EVENT_TICK / EVENT_ORDER / EVENT_TRADE 等常量）

## 交易接口层

- 接口基类：`vnpy/trader/gateway.py`（BaseGateway）
- App 基类：`vnpy/trader/app.py`（BaseApp）
- 偏移转换：`vnpy/trader/converter.py`（OffsetConverter，处理上期所平今/平昨）

## 数据库与数据服务

- 数据库接口：`vnpy/trader/database.py`（BaseDatabase / BarOverview / TickOverview）
- 数据服务接口：`vnpy/trader/datafeed.py`（BaseDatafeed）
- 实用工具：`vnpy/trader/utility.py`（BarGenerator / ArrayManager / TRADER_DIR 等）

## UI 层

- 主窗口：`vnpy/trader/ui/mainwindow.py`（MainWindow）
- 通用控件：`vnpy/trader/ui/widget.py`（BaseMonitor / BaseCell 等表格组件）
- Qt 应用初始化：`vnpy/trader/ui/__init__.py`（create_qapp）

## 优化与回测支持

- 参数优化：`vnpy/trader/optimize.py`（遗传算法 deap 封装，用于策略参数优化）

## K线图表

- 图表引擎：`vnpy/chart/__init__.py`（ChartWidget）

## RPC 跨进程通讯

- RPC 基础：`vnpy/rpc/__init__.py`（RpcClient / RpcServer，基于 ZeroMQ）

## Alpha AI 量化研究

- 包入口：`vnpy/alpha/__init__.py`
- 投研管理器：`vnpy/alpha/lab.py`（Lab）
- 日志：`vnpy/alpha/logger.py`
- 因子特征工程：`vnpy/alpha/dataset/`
  - 基类：`vnpy/alpha/dataset/base.py`（BaseDataset）
  - Alpha 158 因子集：`vnpy/alpha/dataset/datasets/alpha_158.py`
- 预测模型：`vnpy/alpha/model/`
  - 基类：`vnpy/alpha/model/base.py`（BaseModel）
  - Lasso：`vnpy/alpha/model/models/lasso_model.py`
  - LightGBM：`vnpy/alpha/model/models/lgb_model.py`
  - MLP：`vnpy/alpha/model/models/mlp_model.py`
- Alpha 策略：`vnpy/alpha/strategy/`
  - 基类：`vnpy/alpha/strategy/base.py`（BaseAlphaStrategy）

## 示例与测试

- Jupyter 投研 Demo：`examples/alpha_research/`（download_data_rq/xt, research_workflow_lasso/lgb/mlp）
- 单元测试：`tests/`

## 构建配置

- pyproject.toml：项目依赖、构建工具、ruff 规则、mypy 配置
- 安装脚本：`install_osx.sh` / `install.sh` / `install.bat`
- 国际化构建 Hook：`vnpy/trader/locale/build_hook.py`
