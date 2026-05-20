<!-- SUMMARY: VeighNa 编码约定、质量规范和文件管理规则，为 PROJECT.md 项目规范摘要的权威来源 -->
# 约定与约束（实现细节）

本文件是项目规范约定的权威来源，`.harness/PROJECT.md` "项目规范"各节为摘要引用，以本文件为准。

---

# 一、UI 交互约定

- 窗口：各 App 面板通过 `BaseMonitor`（vnpy/trader/ui/widget.py）实现表格类视图，继承 QTableWidget
- 事件刷新：UI 组件通过 `register_event()` 注册事件回调，收到事件后在 Qt 主线程刷新
- 弹窗：使用 `QDialog` 子类，不阻塞主窗口事件循环

---

# 二、编码约定

## 类型注解

- 所有函数参数和返回值必须有完整类型注解
- 使用 Python 3.10+ 内置 `X | Y` union 语法，不使用 `Optional[X]`
- mypy 配置：`disallow_untyped_defs = true`，`strict_optional = true`，`warn_return_any = true`

## 数据对象

- 所有数据对象使用 `@dataclass` 定义，继承 `BaseData`（vnpy/trader/object.py）
- `BaseData` 要求 `gateway_name: str` 字段作为数据来源标识
- 已推送的数据对象视为不可变，不得修改其字段

## 枚举与常量

- 交易所、方向、品种、状态等使用 `vnpy/trader/constant.py` 中的枚举类（Direction、Exchange、Interval 等）
- 运行时配置通过 `SETTINGS`（vnpy/trader/setting.py）管理，不硬编码
- 事件类型常量定义在 `vnpy/trader/event.py`，新增事件必须集中在此注册

## 日志

- 使用 `loguru` logger（vnpy/trader/logger.py 导入），禁止使用 `print` 或标准库 `logging`
- 日志级别：DEBUG / INFO / WARNING / ERROR / CRITICAL
- 不得在日志中输出账号密码、Token 等敏感信息

## 国际化

- 用户可见字符串通过 `vnpy/trader/locale/_()` 包装，支持中英文切换
- 构建时通过 babel 生成 `.mo` 文件

## 禁止 Mock 造假

- 生产代码禁止以硬编码假数据冒充真实实现
- 接口不可用时返回 None 或抛出明确异常，不得静默返回零值
- Stub 实现须显式注释标记，Code Review 必须检查采集/监控类函数

---

# 三、质量约定

## 代码检查

- ruff：`ruff check .`，零 warning/error，目标版本 `py310`
- mypy：`mypy vnpy`，零 error（polars/lightgbm/hatchling 允许 ignore_missing_imports）

## 错误处理

- 用户侧：通过 EventEngine 发布 LogData 事件，UI 层展示
- 开发侧：使用 loguru logger 记录，级别 ERROR 以上包含 traceback
- Gateway 连接超时/断线：通过 LogData 事件通知用户，不静默失败
- 异步错误：在回调线程捕获后通过事件切回主引擎处理

## 线程与并发

- EventEngine 运行独立处理线程；handler 回调在事件线程执行
- Qt UI 更新必须在主线程；需要 UI 刷新的事件 handler 通过 Qt signal/slot 切换线程
- Gateway 通常运行独立网络线程，收到数据后通过 EventEngine.put() 推送事件
- 并发资源保护使用 threading.Lock 或 queue.Queue，避免裸访问共享状态

## 内存与性能

- BarData / TickData 大批量历史数据通过 database 适配层懒加载，不全量持有在内存
- 策略 on_bar / on_tick 回调要求低延迟，禁止阻塞 IO

## 单元测试

- 测试目录：`tests/`
- 修改 vnpy/trader/ 核心模块或 vnpy/event/ 时必须执行相关测试
- 外部依赖（交易所 API、数据库）使用 Mock 隔离

## 代码扫描

- 本次变更新引入的问题：当次修复，不得合入带 lint/type error 的代码
- 既存问题（非本次引入）：记录到 `.harness/plans/debt-tracker.md`，不强制当次修复
- 判定方法：以 git diff 范围内的文件为准
- 例外：安全漏洞无论是否本次引入，一律立即修复

---

# 四、文件管理约定

- 禁止主动创建 README
- 禁止自主删除项目文件；治理/升级场景允许经用户确认后删除
- 文件命名：小写英文 kebab-case（Snake_case 用于 Python 模块）
- 知识库编号：01~05 认知约束类，21~22 工具索引类
- 执行计划：`.harness/specs/active/` 和 `.harness/plans/active/`，完成后移至 `completed/`
- 长命令（3+ 步或 10+ 行）先写入 `locals/harness_tmp/` 再执行

---

# 五、安全约定

- 密钥：API 密钥、账号密码通过 `SETTINGS`（vt_setting.json）配置，禁止硬编码
- 网络：Gateway 对外接口使用 HTTPS/WSS，响应数据校验签名（各 Gateway 自行实现）
- 隐私：vt_setting.json 包含敏感配置，不提交到 git；日志禁止输出账号密码 Token
