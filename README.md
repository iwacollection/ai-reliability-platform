# AI 可靠性平台（AI Reliability Platform）

一个面向企业 SRE / DevOps 场景的 **Agent 优先、证据驱动、人工在环的 AI 可靠性运行时平台**。

本项目不是简单地把“大模型接到告警上”，而是围绕一次完整故障事件建立可执行、可控制、可恢复、可审计、可评估的闭环：

```text
告警 / 对话
    ↓
事件上下文
    ↓
问题调查
    ↓
证据收集
    ↓
Agent 决策
    ↓
风险判断 / 人工审批
    ↓
执行动作
    ↓
恢复验证
    ↓
审计 / 评估
    ↓
记忆 / 能力演进
```

## 1. 项目解决什么问题

传统运维自动化通常是固定流程：

```text
告警 A → 脚本 B → 执行动作 C
```

这种方式对于明确、稳定的场景很好，但当告警数量、基础设施类型、工具数量和故障组合不断增加时，会出现明显问题：

- 每增加一种故障都需要新增流程；
- 不同基础设施需要维护大量分支；
- 一个流程很难覆盖未知故障；
- 工具越来越多后，流程编排越来越复杂；
- 自动执行容易把错误判断直接传递到生产环境；
- 传统脚本通常缺少完整的证据、审批、验证和审计闭环。

本项目采用 **Agent 优先架构**，让 Agent 在运行时根据当前目标、已有证据和可用能力决定下一步调查方向，同时由 Runtime 对 Agent 进行严格约束。

核心原则是：

```text
Agent 决定“下一步应该调查什么”
Runtime 决定“这个决定是否允许执行”
Tool / Connector 决定“具体怎么执行”
Evidence 记录“系统实际上观察到了什么”
Verification 判断“系统是否真的恢复”
```

## 2. 核心能力

- 告警降噪与聚合
- Incident 生命周期管理
- AI 辅助故障调查
- 基于证据的根因分析
- AI 自动治愈
- Agent Loop / Agent 编排
- 多 Agent 协作
- MCP 工具接入
- 工具注册与权限控制
- Agent 记忆与上下文管理
- 人工审批
- 安全动作执行
- Sandbox 隔离执行
- 恢复验证
- 完整审计
- Scenario Replay 场景重放
- Agent Evaluation 行为评估
- Badcase 回归验证

## 3. 总体架构

```text
外部系统
   │
   │ AlertManager / ChatOps / Kubernetes / Cloud / 其他监控系统
   ▼
┌───────────────────────┐
│       Gateway         │
│ 接入 / 鉴权 / 解析 / 标准化 │
└───────────┬───────────┘
            │ StandardEvent
            ▼
┌──────────────────────────────────────────────────────────┐
│                    AI Reliability Runtime                 │
│                                                          │
│  事件上下文 → Agent Loop → Evidence / Investigation      │
│      ↑             │                 │                   │
│      │             ▼                 ▼                   │
│    Memory       Tool / MCP       Connectors              │
│      │             │                 │                   │
│      └─────────────┴──────────┬──────┘                   │
│                               ▼                          │
│                         Action Plan                      │
│                               │                          │
│                       Policy / Approval                  │
│                               │                          │
│                               ▼                          │
│                         Action Runtime                   │
│                               │                          │
│                               ▼                          │
│                          Verification                    │
│                               │                          │
│                               ▼                          │
│                         Audit / Evaluation               │
└──────────────────────────────────────────────────────────┘
            │                 │                 │
            ▼                 ▼                 ▼
        Sandbox          Simulator          Cloud
       安全执行环境        场景模拟重放        云基础设施能力
```

## 4. 一次故障事件是怎么运行的

以 Kubernetes Pod CPU 持续过高为例：

```text
AlertManager
    ↓
Gateway 接收告警
    ↓
Parser 解析
    ↓
StandardEvent 标准化
    ↓
创建 Incident
    ↓
建立当前上下文
    ↓
Agent 发现当前证据不足
    ↓
查询 CPU / Throttling / Request / Limit
    ↓
查询流量 / 错误率 / 日志
    ↓
查询最近发布与配置变更
    ↓
形成并验证多个故障假设
    ↓
生成结构化动作计划
    ↓
风险策略判断
    ↓
低风险自动执行 / 高风险进入人工审批
    ↓
Action Runtime 执行
    ↓
检查 Pod / 错误率 / 延迟 / 告警状态
    ↓
确认恢复
    ↓
记录审计与评估结果
```

平台不会简单执行：

```text
CPU 高 → 直接扩容
```

因为 CPU 高可能来自：

- 流量增长
- CPU limit 导致 throttling
- GC 压力
- 重试风暴
- 应用程序 Bug
- 下游依赖变慢
- 节点资源竞争
- 最近版本发布

因此 Agent 必须先调查证据，再决定动作。

## 5. “主动发现问题”是怎么实现的

本项目中的主动性不是让大模型凭空“猜一个问题”，而是把主动调查定义成一个可以被验证的运行时过程：

```text
Incident Objective
       ↓
当前已有 Evidence
       ↓
比较“目标需要什么证据”和“当前有什么证据”
       ↓
发现 Evidence Gap（证据缺口）
       ↓
选择能够缩小证据缺口的工具
       ↓
获得新 Evidence
       ↓
重新评估假设
       ↓
继续调查 / 生成动作 / 请求人工介入 / 结束
```

例如：

```text
目标：解释 payment-api 为什么 CPU 持续升高

已有证据：
- CPU = 92%
- CPU 告警持续 10 分钟

缺少证据：
- CPU throttling
- CPU request / limit
- 请求量变化
- 错误率
- 最近发布

因此 Agent 不应该直接说“扩容”。

它应该主动发现证据缺口：
    ↓
查询 CPU throttling
    ↓
查询 request / limit
    ↓
查询流量
    ↓
查询最近变更
```

这也是平台降低大模型幻觉的核心方法之一：**让模型基于证据缺口选择下一步，而不是直接从告警文本猜结论。**

## 6. Agent Runtime 的可靠性控制

Agent 并不是无限循环运行的。

Runtime 同时控制多种预算：

```text
最大轮数
最大工具调用次数
最大重试次数
最大运行时间
最大 Token
最大动作次数
最大成本
```

同时具备多种停止条件：

```text
RESOLVED
INSUFFICIENT_EVIDENCE
WAITING_APPROVAL
BLOCKED_BY_POLICY
MAX_TURNS
MAX_TOOL_CALLS
TIMEOUT
BUDGET_EXCEEDED
UNRECOVERABLE_ERROR
HUMAN_CANCELLED
```

还需要防止 Agent 陷入循环：

```text
重复调用检测
        ↓
进展检测
        ↓
矛盾结论检测
        ↓
停止 / 改变调查方向 / 请求人工介入
```

因此真正的可靠性不是依靠 Prompt 告诉 Agent“不要无限循环”，而是由 Runtime 从机制上限制它。

## 7. Context 与 Memory 怎么解决

上下文不是简单的聊天记录数据库，而应该是当前决策需要的“工作集”。

```text
系统策略
  ↓
当前 Incident 目标
  ↓
当前状态
  ↓
最近关键 Evidence
  ↓
相关历史 Memory
  ↓
Context Builder
  ↓
LLM Context
```

历史数据不会全部塞进模型，而是按照：

```text
相关性
×
时间新鲜度
×
可信度
×
证据质量
```

进行检索，并设置上下文 Token、Evidence 数量和 Memory 检索预算。

当上下文过大时，不是简单删除最早消息，而是进行语义压缩：

```text
原始 Tool Result
      ↓
提取事实
      ↓
删除重复数据
      ↓
形成调查摘要
      ↓
保留 Evidence 引用
      ↓
丢弃大体积原始数据
```

这样解决了“记忆过头”和“记忆不足”两个相反问题。

## 8. Tool / MCP 怎么设计

工具不是散落在 Agent 代码里的函数，而应该通过统一的工具契约注册：

```text
工具名称
工具描述
输入参数 Schema
输出 Schema
风险等级
权限
超时时间
重试策略
幂等性
是否产生副作用
执行器
```

例如只读工具：

```text
get_pod_metrics
副作用：无
风险：低
权限：k8s.metrics.read
```

生产写操作：

```text
restart_deployment
副作用：有
风险：中
权限：k8s.deployment.restart
是否需要审批：根据环境与策略决定
```

MCP 的作用是标准化 Agent 与外部工具之间的能力边界，而不是把所有工具说明和所有返回结果一次性塞进上下文。

```text
Agent
  ↓
工具注册表
  ↓
MCP / Connector
  ↓
外部系统
  ↓
结构化结果
  ↓
Evidence
```

## 9. 为什么参数校验不能交给大模型

模型生成的参数必须经过 Runtime 再验证：

```text
LLM 输出
   ↓
JSON Schema 校验
   ↓
类型校验
   ↓
语义校验
   ↓
目标资源校验
   ↓
权限校验
   ↓
风险策略
   ↓
人工审批
   ↓
执行
```

例如模型给出：

```text
namespace = production
```

即使参数格式完全正确，如果当前 Agent 只被授权访问 staging，也必须拒绝。

## 10. Action、Approval、Sandbox、Verification 为什么必须拆开

这几个模块分别解决不同风险：

```text
Action
  ↓
描述“要改变什么”

Approval
  ↓
判断“是否允许改变”

Sandbox
  ↓
限制“在哪里改变”

Executor
  ↓
真正执行

Verification
  ↓
证明“系统是否真的恢复”
```

因此：

> 模型说“执行成功”不等于生产系统已经恢复。

例如重启 Pod 的 API 返回成功，只说明重启请求被接受；真正恢复还必须验证 Pod Ready、错误率、延迟、依赖健康度以及告警是否清除。

## 11. 审计与评估

每次 Incident 应形成完整轨迹：

```text
Event
 → Context
 → Agent Decision
 → Evidence
 → Tool Call
 → Action Plan
 → Approval
 → Action
 → Verification
 → Final Outcome
```

这条链路同时服务于：

- 生产审计
- 故障复盘
- Agent 调试
- Badcase 分析
- Scenario Replay
- 回归测试
- Agent 能力评估

Agent 的评价也不能只看“最终答案对不对”，还应该评价：

```text
决策质量
工具选择
参数正确性
证据完整性
安全行为
动作正确性
验证质量
最终恢复结果
```

## 12. 仓库目录

```text
services/
├── gateway/          外部事件接入、解析与标准化
├── agent_runtime/    Agent 核心运行时与可靠性控制
├── evidence/         证据采集与标准化
├── connectors/       外部系统适配器
├── mcp/              MCP 工具协议与能力暴露
├── sandbox/          隔离执行环境
├── harness/          Agent 执行控制与安全护栏
├── simulator/        故障场景模拟与重放
└── cloud/            云与基础设施能力

packages/
├── common/           公共领域模型与通用组件
├── models/           跨服务数据契约
└── llm_sdk/          与具体模型供应商解耦的 LLM 接入层

architecture_v2_archive/
└── 历史架构与评估实现

docs/
├── ARCHITECTURE.md       总体架构设计
├── MODULES.md            模块职责与工程取舍
├── AGENT_RUNTIME.md      Agent Runtime 深度设计
└── PRODUCTION_DESIGN.md  生产可靠性、安全与治理设计
```

## 13. 文档阅读顺序

如果第一次阅读这个项目，建议按照下面顺序：

```text
README
  ↓
总体架构
  ↓
模块设计
  ↓
Agent Runtime 深度设计
  ↓
生产可靠性设计
  ↓
源码
  ↓
Tests
  ↓
Scenario Replay
```

对应文档：

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：总体架构、Incident 生命周期、模块边界和演进方向。
- [`docs/MODULES.md`](docs/MODULES.md)：逐模块解释为什么存在、解决什么问题以及模块之间如何解耦。
- [`docs/AGENT_RUNTIME.md`](docs/AGENT_RUNTIME.md)：深入解释 Agent Loop、主动发现问题、Evidence Gap、Harness、Context、Memory、Tool Registry、MCP、Retry、Fallback、Checkpoint、幂等、审批、多 Agent 和 Verification。
- [`docs/PRODUCTION_DESIGN.md`](docs/PRODUCTION_DESIGN.md)：生产环境中的可靠性、安全、权限、Prompt Injection、防重复、并发、限流、降级、可观测性、灾备、SLO 和发布策略。

## 14. 核心设计原则总结

### Agent 优先

平台不把所有故障流程写死，而是让 Agent 根据当前目标和证据决定调查路径。

### 证据驱动

Agent 必须区分事实和假设。关键结论尽量绑定可追溯 Evidence。

### 调查与执行分离

只读调查和生产写操作使用不同的权限、风险、审批和执行路径。

### Runtime 控制模型

模型负责决策，Runtime 负责预算、权限、Schema、策略、停止条件和恢复。

### 执行必须可验证

任何动作都不能仅凭“API 调用成功”判断事故已经恢复。

### 人工审批是安全控制面

高风险操作必须可以暂停、审批、拒绝和审计，而不是通过 Prompt 请求模型“自觉谨慎”。

### 所有行为都应该可回放

通过 Simulator、Scenario Replay 和 Evaluation，把 Agent 行为转化为可以重复验证的工程对象。

## 15. 当前项目状态

🚧 **持续开发中**

仓库已经包含多个可运行的 Runtime 组件、测试和场景验证能力，并持续向生产级 AI Reliability Runtime 演进。

文档会与代码同步维护：新增重要能力时，应同时补充架构说明、模块设计、测试和生产风险分析。
