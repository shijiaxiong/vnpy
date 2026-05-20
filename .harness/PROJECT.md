# PROJECT.md -- VeighNa (vnpy)

VeighNa 是面向专业量化交易员的开源 Python 交易系统开发框架，提供多市场交易接口接入、策略引擎、数据管理和 AI 因子研究一体化解决方案。

---

# Harness 框架适配

本节为 Harness 框架提供项目级配置，框架文件通过 `.harness/PROJECT.md` 直接引用。

## 知识库目录

首次加载时需建立 SUMMARY 索引的目录：
- `.harness/knowledge/`
- `.harness/prd/`（除 .harness/prd/03-prd-specs.md）
- `.harness/lessons/`

## 任务类型加载矩阵

首次加载时，根据任务类型选择性读取知识库文件（所有文件首行 SUMMARY 始终必读）：

| 任务类型 | 必读（完整读取） | 按需读取 |
|---------|----------------|---------|
| 功能需求 | .harness/knowledge/01-overview.md, .harness/knowledge/02-architecture.md, .harness/knowledge/22-file-map.md, .harness/prd/01-prd-sense.md, .harness/prd/02-prd-baseline.md | .harness/knowledge/03-conventions.md, .harness/knowledge/04-data-boundaries.md, .harness/knowledge/05-key-patterns.md, .harness/knowledge/21-glossary.md |
| Bug修复 | .harness/knowledge/01-overview.md, .harness/knowledge/03-conventions.md, .harness/knowledge/22-file-map.md | .harness/knowledge/02-architecture.md, .harness/knowledge/04-data-boundaries.md, .harness/knowledge/05-key-patterns.md, .harness/knowledge/21-glossary.md |
| 治理/扫描 | .harness/knowledge/01-overview.md, .harness/knowledge/03-conventions.md, .harness/knowledge/22-file-map.md | .harness/knowledge/02-architecture.md, .harness/knowledge/05-key-patterns.md |
| 文档维护 | .harness/knowledge/01-overview.md, .harness/knowledge/22-file-map.md | 读取目标文件引用链上的 knowledge/ 和 prd/ 文件 |

## 知识回填文件映射

知识回填的回填目标：
- 架构变化 -> .harness/knowledge/02-architecture.md
- 新术语 -> .harness/knowledge/21-glossary.md
- 数据结构/存储变化 -> .harness/knowledge/04-data-boundaries.md
- 新源文件 -> .harness/knowledge/22-file-map.md
- 新跨文件模式 -> .harness/knowledge/05-key-patterns.md
- 产品方向调整 -> 提示用户，人工更新 .harness/prd/01-prd-sense.md

## 教训库加载路径

本项目教训库分布在两个位置：
- `.harness/framework/lessons/general.md`（Harness 通用教训）
- `.harness/lessons/project.md`（项目教训）

## 构建与测试

### 构建
```bash
# macOS
bash install_osx.sh

# 或直接用 pip 安装
pip install -e ".[dev]"
```

### 单元测试
单元测试执行策略：
- 用户明确要求时：必须执行
- 修改 vnpy/trader/ 核心模块或 vnpy/event/ 时自动执行相关测试
- 其他场景：跳过

```bash
# 代码风格检查
ruff check .

# 静态类型检查
mypy vnpy
```

## 扫描维度

代码扫描使用的维度及规则来源。下表路径均相对于 `.harness/knowledge/` 目录：

| # | 维度 | 规则来源 |
|---|------|---------|
| 1 | 代码规范 | 03-conventions.md 二、编码约定 |
| 2 | 架构边界 | 02-architecture.md 模块边界 |
| 3 | 数据类型安全 | 04-data-boundaries.md 边界约定 |
| 4 | 错误处理 | 03-conventions.md 三、质量约定 / 错误处理 |
| 5 | 线程与并发 | 03-conventions.md 三、质量约定 / 线程与并发 |

## 项目知识索引

| 文件 | 何时查阅 |
|------|---------|
| .harness/prd/01-prd-sense.md | 功能迭代前，确认产品定位和判断准则 |
| .harness/knowledge/01-overview.md | 任务开始时，了解项目概览（技术栈/入口/核心流程） |
| .harness/knowledge/02-architecture.md | 涉及模块新增、依赖关系、分层结构调整时 |
| .harness/knowledge/03-conventions.md | 实现细节、编码规范、错误处理、并发模型有疑问时 |
| .harness/knowledge/04-data-boundaries.md | 修改数据模型、数据库读写或事件数据结构时 |
| .harness/knowledge/05-key-patterns.md | 实现新功能时需要了解已有模式时 |
| .harness/knowledge/21-glossary.md | 对术语不清楚时 |
| .harness/knowledge/22-file-map.md | 确定功能对应源文件时 |
| .harness/prd/02-prd-baseline.md | 确认功能需求与产品约束时 |
| .harness/lessons/project.md | 用户指令或当前根因与 SUMMARY 高度相关时按需读取 |

---

# 项目规范

## 代码生成

以下各节（代码生成、架构边界、质量守护、安全规范）为快速参考摘要，权威定义见 .harness/knowledge/03-conventions.md。

- 类型注解：所有函数参数和返回值必须有完整类型注解（mypy strict 模式）
- 日志：使用 loguru logger，禁止使用 print 或标准 logging
- 常量：交易所、方向、品种等枚举值使用 vnpy/trader/constant.py 中定义的枚举类
- 事件：通过 EventEngine 发布/订阅事件，禁止直接调用跨引擎方法
- 数据对象：使用 dataclass 定义，继承 BaseData，必须包含 gateway_name 字段
- 编码风格：ruff 零 warning，目标版本 Python 3.10

## 架构边界

- Gateway 层只负责对接交易所 API，不含业务逻辑
- App 层（策略引擎）通过 MainEngine 调用 Gateway，不直接操作底层接口
- UI 层通过事件订阅获取数据，不直接调用 Engine 方法写操作
- Alpha 模块（vnpy/alpha/）独立于交易核心，仅依赖 vnpy/trader/object.py 数据结构

## 质量守护

- mypy 静态检查零 error
- ruff lint 零 warning/error
- 新引入问题当次修复，既存问题记录到 debt-tracker.md

## 安全规范

- API 密钥、账号密码不得硬编码，通过 SETTINGS（vnpy/trader/setting.py）配置
- 生产代码禁止硬编码假数据
- 不得在日志中输出账号密码、Token 等敏感信息

---

# 项目附录

## 仓库结构

```
AGENTS.md              -- AI 入口（纯路由）
CLAUDE.md              -- Claude Code 入口
.harness/
  PROJECT.md           -- 项目规范入口（本文件）
  framework/           -- 通用能力（详见 FRAMEWORK.md "Framework 目录结构"）
  knowledge/           -- AI 知识库（01~05 认知约束类, 21~22 工具索引类）
  prd/                 -- 产品文档（AI只读：01-prd-sense、02-prd-baseline、03-prd-specs）
  lessons/
    project.md         -- 项目教训（AI自主维护）
  specs/               -- 设计文档
    active/
    completed/
  plans/               -- 实现计划
    active/
    completed/
    debt-tracker.md    -- 技术债追踪
vnpy/                  -- 核心框架源码
  event/               -- 事件驱动引擎
  trader/              -- 交易平台核心（engine/gateway/app/object/constant/setting）
    ui/                -- PySide6 图形界面
  rpc/                 -- 跨进程通讯
  chart/               -- K线图表组件
  alpha/               -- AI 量化因子研究模块
    dataset/           -- 因子特征工程
    model/             -- 预测模型训练
    strategy/          -- Alpha 策略开发
examples/              -- Jupyter Notebook 示例（量化投研 Demo）
tests/                 -- 单元测试
docs/                  -- 项目文档
```

## 知识层级关系

```
Layer 0   AGENTS.md -> FRAMEWORK.md（通用规范+注册表） + PROJECT.md（项目配置+规则摘要）
Layer 1   framework/agents/（5个角色: Orchestrator/Designer/Planner/Coder/Reviewer）
Layer 1.5 framework/workflows/（迭代功能/修复Bug/迭代文档 + harness-ops/治理类）
Layer 2   framework/skills/（harness/ 核心Skill + harness-ops/ 运维Skill + superpowers/ 方法论）
Layer 3   framework/skills/harness/subskills/（扫描模板）
数据层    knowledge/（权威知识） + prd/（产品文档，AI只读） + guides/（方法论） + lessons/（教训）
辅助层    specs/（设计文档） + plans/（执行计划+技术债）
```

引用方向：Layer 0 -> Layer 1/1.5 -> Layer 2 -> Layer 3 -> 数据层。PROJECT.md 摘要引用 knowledge/03-conventions.md（权威源）。
