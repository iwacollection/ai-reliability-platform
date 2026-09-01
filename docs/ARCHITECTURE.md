# AI Reliability Platform 架构设计

> 本文档描述仓库当前代码结构、核心运行时模型、模块边界以及为什么这样设计。文档以代码中的实际目录为基础；对于尚在演进中的能力，会明确标注为“目标/演进方向”，避免把规划能力误写成已经完成的生产能力。

## 1. 项目定位

AI Reliability Platform 是面向 SRE / DevOps 场景的 Agent-first 可靠性平台。核心目标不是简单地把一个大模型接到告警上，而是把“发现问题 → 获取证据 → 推理 → 制定动作 → 人工审批 → 执行 → 验证 → 留痕 → 学习”做成一个可恢复、可审计、可演进的运行时。

当前 README 将能力概括为告警降噪、根因分析、AI 自动治愈、Workflow、Multi-Agent Runtime 与 MCP Integration；代码进一步拆分出了 Gateway、Agent Runtime、Harness、Evidence、Connectors、MCP、Sandbox、Simulator、Cloud 等边界。

## 2. 总体架构

```text
                           ┌──────────────────────────────┐
                           │         External Sources      │
                           │ AlertManager / ChatOps / ...  │
                           └──────────────┬───────────────┘
                                          │
                                          ▼
                           ┌──────────────────────────────┐
                           │            Gateway            │
                           │ 接入 / 鉴权 / 解析 / 标准化事件 │
                           └──────────────┬───────────────┘
                                          │ StandardEvent
                                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         AI Reliability Runtime                            │
│                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────────┐  │
│  │ Context       │──▶│ Agent / Loop │──▶│ Evidence / Investigation   │  │
│  │ 上下文管理     │   │ 主动决策       │   │ 证据获取与归因               │  │
│  └──────────────┘   └──────┬───────┘   └──────────────┬──────────────┘  │
│                            │                           │                 │
│                            ▼                           ▼                 │
│                    ┌──────────────┐           ┌──────────────┐          │
│                    │ Tool / MCP   │           │ Memory       │          │
│                    │ 工具能力层     │           │ 记忆层         │          │
│                    └──────┬───────┘           └──────────────┘          │
│                           │                                             │
│                           ▼                                             │
│                    ┌──────────────┐                                    │
│                    │ Action       │                                    │
│                    │ 动作规划/执行  │                                    │
│                    └──────┬───────┘                                    │
│                           │                                             │
│              ┌────────────┴────────────┐                                │
│              ▼                         ▼                                │
│      ┌──────────────┐          ┌──────────────┐                         │
│      │ Approval     │          │ Sandbox      │                         │
│      │ 风险审批       │          │ 安全执行环境  │                         │
│      └──────┬───────┘          └──────┬───────┘                         │
│             └──────────────┬──────────┘                                 │
│                            ▼                                            │
│                     ┌──────────────┐                                    │
│                     │ Verification │                                    │
│                     │ 恢复验证       │                                    │
│                     └──────┬───────┘                                    │
│                            ▼                                            │
│                     ┌──────────────┐                                    │
│                     │ Audit / Eval  │                                    │
│                     │ 审计与评估      │                                    │
│                     └──────────────┘                                    │
└──────────────────────────────────────────────────────────────────────────┘

         ▲                 ▲                    ▲
         │                 │                    │
   Connectors           Cloud                Simulator
  外部系统适配         云/基础设施能力         场景重放与验证
```

## 3. 核心设计原则

### 3.1 Agent-first，而不是 Workflow-first

传统自动化系统通常先写死：收到 A 告警 → 查 B → 执行 C。这样做在固定场景中稳定，但面对真实生产环境中的未知组合问题会迅速膨胀。

本项目将 Agent Runtime 作为决策中心：

1. 输入是结构化事件与当前上下文。
2. Agent 根据目标和已有证据决定下一步。
3. 工具只负责提供能力，不替 Agent 决定业务流程。
4. 每次工具调用都产生可记录的证据和执行结果。
5. Agent 可以根据结果继续调查、改变假设、请求审批或退出。

这样解决的问题是“工具越多，Workflow 越难维护”。代价是 Agent 运行时必须解决最大轮数、上下文膨胀、工具错误、重复调用、幻觉、权限和失败恢复等问题，因此 Harness、Context、Evidence、Approval、Audit 不是附属模块，而是运行时安全边界。

### 3.2 Evidence-driven，而不是 LLM 猜测

Agent 的结论必须尽量建立在可引用证据上。证据包括告警、指标、日志、Kubernetes 对象、变更记录、历史事件和工具执行结果。

推理链应形成：

```text
现象
  ↓
假设
  ↓
需要验证什么
  ↓
调用哪个工具
  ↓
得到什么证据
  ↓
假设是否成立
  ↓
下一步调查 / 动作
```

这可以降低“模型看起来很合理，但实际上没有证据”的幻觉风险。

### 3.3 Action 与 Investigation 分离

调查是读取型行为，动作是改变系统状态的行为。二者不能只靠 Prompt 区分。

平台应该在类型、权限、审批、执行环境和审计层面都建立边界：

```text
Read-only investigation
        │
        ├── query metrics
        ├── query logs
        ├── inspect Kubernetes
        └── inspect recent changes

State-changing action
        │
        ├── scale
        ├── restart
        ├── rollback
        ├── change config
        └── execute remediation
```

这样可以避免模型因为误判而直接改变生产系统。

### 3.4 可恢复优先

Agent 不是一次 HTTP 请求。一个真实 incident 可能持续数分钟甚至更久，并且可能在任意步骤失败。因此运行时需要把过程视为状态机：

```text
CREATED
  → INVESTIGATING
  → PLAN_READY
  → WAITING_APPROVAL
  → EXECUTING
  → VERIFYING
  → RESOLVED

任何状态均可能进入：
  FAILED / BLOCKED / CANCELLED / TIMED_OUT
```

关键状态必须可持久化，避免 Runtime 重启后只能重新猜测发生过什么。

## 4. 一次 Incident 的完整生命周期

### Step 1：事件进入

外部系统发送告警或事件到 Gateway。

Gateway 的职责不是分析问题，而是完成：

- 接收请求
- 基础校验
- 原始事件留存
- 选择对应 Parser
- 将不同来源转换为统一事件模型
- 将事件交给 Runtime

### Step 2：事件标准化

不同系统的字段命名完全不同。例如 AlertManager、云监控、Kubernetes、ChatOps 可能使用不同的 severity、resource、labels 和时间字段。

平台通过 StandardEvent 把来源差异隔离在接入层：

```text
Raw Event
   ↓
ParserRegistry
   ↓
Source Parser
   ↓
StandardEvent
   ↓
Agent Runtime
```

这样 Agent 不需要知道“这个事件到底来自哪个监控系统”。

### Step 3：建立 Incident Context

Runtime 建立当前 Incident 的上下文，包括：

- 原始事件
- 当前目标
- 已知事实
- 当前假设
- 已获取证据
- 已调用工具
- 工具结果
- 历史动作
- 审批状态
- 当前 Agent 状态
- 消息/对话上下文
- 预算与运行限制

### Step 4：Agent 主动调查

Agent 不应该仅仅“回答用户问题”，而应该判断当前证据是否足够。

典型循环：

```text
Observe
  ↓
Assess
  ↓
Hypothesize
  ↓
Select Tool
  ↓
Execute
  ↓
Validate Result
  ↓
Update Context
  ↓
Decide Next Step
```

如果证据不足，就继续调查；如果证据足够，就生成诊断结论和动作计划；如果风险不可接受，则停止并请求人工介入。

### Step 5：动作计划

动作计划应该是结构化对象，而不是自然语言句子。例如：

```json
{
  "action": "increase_memory_limit",
  "target": "payment-api",
  "risk": "medium",
  "reason": "evidence-backed diagnosis",
  "preconditions": [],
  "verification": ["pod restart success", "memory pressure recovered"],
  "rollback": "restore_previous_limit"
}
```

这样 Approval、Executor、Audit 和 Verification 可以消费同一份契约。

### Step 6：风险审批

风险低且满足策略的动作可以自动执行；中高风险动作进入人工审批。

审批不是 UI 功能，而是状态机的一部分：

```text
PLAN_READY
   ↓
Policy Check
   ├── Deny → BLOCKED
   ├── Auto Approve → EXECUTING
   └── Human Approval → WAITING_APPROVAL
                              ↓
                       APPROVED / REJECTED
```

### Step 7：安全执行

ActionRuntime 只执行结构化动作，不接受 Agent 任意拼接的 shell 或 API 请求。

对于需要执行代码、脚本或高风险操作的能力，应进入 Sandbox，并限制：

- 网络访问
- 文件系统
- CPU / Memory
- 执行时间
- 凭据
- 可调用命令
- 可操作资源范围

### Step 8：验证

执行成功不等于事故恢复。

例如重启 Pod 成功，只能证明 `restart` 请求成功；真正需要验证的是：

```text
Pod Ready
+ error rate recovered
+ latency recovered
+ alert cleared
+ dependency healthy
```

因此 Action 和 Verification 必须是两个独立阶段。

### Step 9：审计与评估

最终 Incident 应留下完整链路：

```text
Event
 → Context
 → Agent decisions
 → Evidence
 → Tool calls
 → Plan
 → Approval
 → Action
 → Verification
 → Final outcome
```

这既用于生产审计，也用于 Agent 评估和后续能力演进。

## 5. 为什么要拆这么多模块

模块多并不是为了“看起来像微服务”。每一个边界都对应一个实际工程问题：

| 模块 | 主要问题 | 隔离方式 |
|---|---|---|
| Gateway | 外部系统格式不一致 | Parser + StandardEvent |
| Agent Runtime | 模型需要持续决策 | Agent Loop + Context |
| Harness | Agent 可能无限循环/失控 | Budget、Stop、Retry、Checkpoint |
| Evidence | 模型可能无依据猜测 | Evidence object + provenance |
| Memory | 上下文越来越大 | 分层记忆 + 检索 + 摘要 |
| MCP | 工具来源复杂、协议不统一 | Tool contract / MCP adapter |
| Connectors | 外部系统 API 各不相同 | Adapter boundary |
| Action | 改变生产状态存在风险 | Structured action |
| Approval | 人工控制高风险动作 | Approval state machine |
| Sandbox | 任意代码执行风险 | 隔离运行环境 |
| Audit | 无法解释 Agent 做了什么 | Append-only execution trail |
| Evaluation | 无法知道 Agent 是否真的变好 | Replay / assertion / benchmark |
| Simulator | 没有真实生产环境也无法验证 | Scenario replay |
| Cloud | 基础设施操作与 Agent 解耦 | Infrastructure adapters |

## 6. 核心模块设计

### 6.1 Gateway

目录：`services/gateway`

Gateway 是平台北向入口。核心设计是 Registry + Parser：

```text
Webhook
  ↓
RawEvent
  ↓
ParserRegistry.get(source)
  ↓
Parser.parse()
  ↓
StandardEvent
```

优点：新增 Prometheus、PagerDuty、云监控等来源时，不需要修改 Runtime。

### 6.2 Agent Runtime

目录：`services/agent_runtime`

这是平台最核心的运行时。当前代码已经按职责进一步拆分为 `app/action`、`app/agent`、`app/agents`、`app/approval`、`app/audit`、`app/change`、`app/contexts`、`app/conversation`、`app/core`、`app/evaluation`、`app/events`、`app/evolution` 等边界，并配套 `memory`、`skills` 和 `tests`。

其中 `app/core` 再划分为：

```text
core/
├── context       上下文模型与生命周期
├── execution     执行控制
├── orchestration 编排与决策协调
└── state         运行状态
```

这四层解决的是“Agent 本身会变，但 Runtime 的控制面必须稳定”。

### 6.3 Agent 与 Agents

`agent` 更适合承载 Agent 抽象、注册或通用运行机制；`agents` 承载具体 Agent 实现。

设计目标是：

```text
Agent Contract
     ↓
Concrete Agent
     ├── Noise / Classification
     ├── Investigation
     ├── RCA
     └── Healing
```

新增 Agent 不应修改核心 Runtime。

### 6.4 Harness

目录：`services/harness` 与 `services/agent_runtime/harness`

Harness 是 Agent 的“安全驾驶员”，不是另一个 Agent。

它负责控制：

- 最大执行轮数
- 单轮/总 Token 预算
- 工具调用预算
- 时间预算
- 重试次数
- 重复工具调用检测
- 无进展检测
- checkpoint
- stop condition
- failure / timeout
- human escalation

核心思想：**让模型拥有决策自由，但不给模型无限执行权。**

### 6.5 Context

上下文不是简单的 message list。建议分成：

```text
Working Context
├── Incident facts
├── Current hypotheses
├── Evidence references
├── Recent tool results
├── Current plan
└── Pending approval

Historical Context
├── Previous incident
├── Previous remediation
└── Conversation history

Long-term Memory
├── Known failure patterns
├── Runbooks
├── Successful actions
└── Organizational knowledge
```

这样可以解决两个极端：

- 记忆不足：Agent 不知道以前发生过什么。
- 记忆过头：所有历史都塞进 Prompt，导致 Token、噪声和注意力下降。

正确做法不是无限扩大上下文，而是“按任务检索 + 分层 + 摘要 + 引用”。

### 6.6 Evidence

目录：`services/evidence`

Evidence 层把外部系统返回的数据包装成可追踪的证据。至少应包含：

- source
- timestamp
- resource
- query
- raw/reference
- normalized facts
- confidence / quality
- correlation id

Agent 应引用 Evidence，而不是直接把一次工具返回结果当成永恒事实。

### 6.7 MCP

目录：`services/mcp`

MCP 层用于统一外部工具能力。关键原则是：**MCP 是能力协议，不是业务 Workflow。**

例如 Kubernetes、GitHub、CMDB、Prometheus、日志系统可以分别暴露工具，但 Agent 决定调用哪个工具、什么时候调用。

为了避免 MCP 污染上下文，建议采用：

```text
Tool Registry
     ↓
Capability Discovery
     ↓
Select relevant tool
     ↓
Call tool
     ↓
Normalize result
     ↓
Store Evidence
     ↓
Only inject compact result into context
```

不能把所有 MCP server 的全部工具定义、全部历史结果永久塞入模型上下文。

### 6.8 Connectors

目录：`services/connectors`

Connector 负责外部系统适配，例如：

```text
Agent
 ↓ stable tool contract
Connector
 ↓ vendor-specific API
Kubernetes / GitHub / CMDB / Monitoring / ChatOps
```

这样可以防止业务 Agent 与第三方 SDK 强耦合。

### 6.9 Action

目录：`services/agent_runtime/app/action`

Action 是状态改变能力的统一入口。其核心不是“怎么执行命令”，而是保证动作具备：

- 明确目标
- 明确参数
- 风险等级
- 前置条件
- 权限
- 审批要求
- 幂等键
- 超时
- 回滚策略
- 验证条件

### 6.10 Approval

目录：`services/agent_runtime/app/approval`

当前设计包含 ApprovalStatus、ApprovalRequest、ApprovalManager、ApprovalStore、ApprovalService 等概念。

推荐状态：

```text
PENDING
 ├── APPROVED
 ├── REJECTED
 └── EXPIRED
```

Approval 必须绑定具体 action hash / incident / requester / approver / expiration，防止“审批了一个动作，执行时却偷偷换成另一个动作”。

### 6.11 Audit

目录：`services/agent_runtime/app/audit`

Audit 用于记录 Agent 的决策和执行链。审计记录应该回答：

1. 为什么触发？
2. Agent 看到了什么？
3. 使用了什么证据？
4. 调用了什么工具？
5. 得出了什么结论？
6. 计划执行什么？
7. 谁批准？
8. 实际执行什么？
9. 是否恢复？
10. 如果失败，失败在哪里？

### 6.12 Evaluation

目录：`services/agent_runtime/app/evaluation`

Evaluation 不应只测试“答案像不像正确答案”，而应测试整个行为轨迹：

```text
正确性
├── Diagnosis correctness
├── Evidence sufficiency
├── Tool selection
├── Action correctness
├── Safety policy
├── Verification correctness
└── Final outcome
```

### 6.13 Simulator / Replay

目录：`services/simulator`

没有真实生产环境时，最大的风险是“代码通过测试，但 Agent 行为从未在完整 Incident 上跑过”。

Simulator 通过固定场景构造故障证据，然后 Replay Agent 的行为。

典型场景：

```text
Pod CPU high
 → inspect pod
 → inspect metrics
 → inspect recent change
 → identify resource limit issue
 → propose remediation
 → approval
 → simulated action
 → verification
```

这让 Agent 可以像软件一样进行回归测试。

## 7. Agent 健壮性设计

### 7.1 最大轮数

不能让模型决定自己何时停止。Runtime 应设置硬上限：

```text
max_turns
max_tool_calls
max_wall_time
max_tokens
max_same_tool_repetition
```

达到任意上限即停止或升级人工。

### 7.2 Retry

Retry 只能用于可恢复错误，例如：

- 网络暂时失败
- 429
- 临时连接失败
- 外部服务短暂不可用

不能对“业务参数错误”无限重试。

### 7.3 Fallback

Fallback 应按能力降级，而不是让模型继续胡猜：

```text
LLM failed
  ↓
retry provider
  ↓
fallback model
  ↓
rule-based safety decision
  ↓
human escalation
```

### 7.4 Checkpoint

每完成一个重要阶段就持久化状态：

```text
event accepted
context initialized
investigation checkpoint
plan generated
approval received
action completed
verification completed
```

Runtime 重启后从最后一个 checkpoint 恢复，而不是重新执行已经完成的动作。

### 7.5 Idempotency

所有状态改变动作都需要 idempotency key：

```text
incident_id + action_id + plan_version
```

防止 Agent 重试导致“同一个扩容/重启/发布动作执行两次”。

## 8. 幻觉控制

模型不能直接拥有“事实权”。事实来自 Evidence，模型负责解释 Evidence。

推荐采用三层约束：

### 第一层：工具约束

不能通过工具获得的数据，模型不能声称已经验证。

### 第二层：结构化输出

诊断必须区分：

```text
FACT
HYPOTHESIS
INFERENCE
RECOMMENDATION
```

### 第三层：验证

动作执行后必须重新查询系统状态，而不是相信模型说“已经恢复”。

## 9. 权限模型

权限至少分成四层：

```text
Agent Identity
   ↓
Tool Permission
   ↓
Resource Scope
   ↓
Action Permission
```

例如 Agent 可以读取 Kubernetes Pod，但不代表它可以删除 Pod；可以操作测试 Namespace，也不代表可以操作生产 Namespace。

建议权限决策由 Runtime/Policy 层完成，而不是由 Prompt 决定。

## 10. 上下文长度治理

上下文控制采用“保留关键状态、压缩过程细节”的原则。

```text
永久保留
├── Incident summary
├── Current diagnosis
├── Key evidence references
├── Current plan
└── Pending action

可摘要
├── 老的 tool result
├── 重复日志
├── 中间推理
└── 已结束对话

可丢弃
├── 重复 metadata
└── 已被后续证据否定的临时信息
```

模型真正需要的是“当前决策所需信息”，不是“过去发生过的所有信息”。

## 11. Multi-Agent 演进

Multi-Agent 不应该为了多 Agent 而多 Agent。只有当职责、权限、上下文或模型能力确实不同才拆分。

一个合理的协作方式：

```text
Supervisor
 ├── Investigator
 ├── RCA Agent
 ├── Change Agent
 └── Verification Agent
```

Supervisor 管理任务边界；子 Agent 返回结构化结果和 Evidence 引用，而不是互相共享无限聊天记录。

这样解决 Multi-Agent 最常见的问题：上下文互相污染、职责重叠、循环调用和不可审计。

## 12. 生产化演进路线

### Phase 1：运行时正确性

- 标准事件模型
- Agent Loop
- Tool Contract
- Action Contract
- Approval
- Verification
- Audit

### Phase 2：可靠运行

- Durable State
- Checkpoint
- Retry / Timeout
- Idempotency
- Distributed lock
- Queue

### Phase 3：安全

- RBAC / ABAC
- Secret isolation
- Sandbox
- Policy engine
- Production approval
- Break-glass mechanism

### Phase 4：规模化

- 多 Agent
- Tool registry
- MCP federation
- Context service
- Memory service
- Evidence store

### Phase 5：持续进化

- Incident replay
- Offline evaluation
- Production feedback
- Badcase classification
- Prompt / skill / policy versioning
- Model routing

## 13. 当前代码与目标架构的关系

仓库中存在 `architecture_v2_archive`，其中保存了历史架构/评估实现。当前主线应以 `services/*` 与 `packages/*` 为准；Archive 用于追溯设计演进，不应被新代码直接依赖。

当前 README 仍保持简洁的 Under Development 定位，本文档则作为深入设计说明。后续新增模块时，应同步更新本文件对应章节，特别是“职责、边界、解决的问题、失败模式、权限和测试策略”。
