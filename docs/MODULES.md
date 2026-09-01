# 模块设计与工程问题说明

本文从“模块为什么存在”而不是“目录里有什么文件”的角度说明平台设计。阅读顺序建议：先看 ARCHITECTURE.md，再看本文，最后结合源码。

## 1. 目录地图

```text
ai-reliability-platform/
├── services/
│   ├── gateway/          北向事件接入与标准化
│   ├── agent_runtime/    Agent 核心运行时
│   ├── evidence/         证据采集/规范化
│   ├── connectors/       外部系统适配
│   ├── mcp/              工具协议与能力暴露
│   ├── sandbox/          隔离执行
│   ├── harness/          Agent 运行控制
│   ├── simulator/        故障场景与重放
│   └── cloud/            云基础设施能力
│
├── packages/
│   ├── common/           共享领域模型/通用能力
│   ├── models/           跨服务模型
│   └── llm_sdk/          LLM 访问抽象
│
├── .github/workflows/    CI 验证
└── architecture_v2_archive/
    └── 历史架构归档，不作为当前主线依赖
```

## 2. Gateway：把外部世界转换成内部语言

### 问题

监控系统、ChatOps、云平台和 Kubernetes 的事件格式不同。如果 Agent 直接处理这些格式，Agent 会被大量解析逻辑污染。

### 设计

采用：

```text
Raw Event → Parser Registry → Source Parser → StandardEvent
```

Registry 负责“找到解析器”，Parser 负责“理解来源格式”，StandardEvent 负责“提供平台统一语义”。

### 为什么不能直接写 if/else

错误做法：

```python
if source == "alertmanager":
    ...
elif source == "pagerduty":
    ...
elif source == "cloudwatch":
    ...
```

随着来源增长，Gateway 会成为巨型条件分支。

Registry 的价值是把“新增来源”变成新增一个实现，而不是修改中心逻辑。

### 失败模式

- payload 格式错误 → 400 / rejected
- 未知 source → no parser
- parser 解析失败 → ingestion failure + 原始事件保留
- 重复事件 → 根据 event fingerprint 做幂等处理

## 3. Agent Runtime：平台的大脑，但不是执行器

Agent Runtime 负责：

- 创建/恢复 Incident
- 管理 Agent 生命周期
- 调度工具
- 管理 Context
- 管理状态
- 生成计划
- 与 Approval 协作
- 调用 Action
- 发起 Verification

它不应该直接实现 Kubernetes、GitHub、数据库等业务 API。

原因是：Runtime 的稳定性应该独立于工具数量增长。

## 4. Core：把 Agent Loop 与控制面分开

当前 `services/agent_runtime/app/core` 下包含：

```text
context/
execution/
orchestration/
state/
```

### context

负责 Agent 每一轮需要看到什么。

### execution

负责一次实际执行过程的控制，例如工具调用、动作执行、超时与错误传播。

### orchestration

负责决定“下一步应该进入哪个能力”。

### state

负责描述 Incident / Agent 当前处于什么阶段。

### 为什么拆开

如果把 Context、State、Execution、Decision 全写在一个 Agent 类里，会出现一个典型问题：Prompt 一改，状态机也跟着改；工具一多，执行代码也不断膨胀。

拆分以后：

```text
Decision changes → Agent implementation
Execution changes → Executor
State changes → State model
Context changes → Context manager
```

## 5. Harness：限制 Agent，而不是限制 Agent 的智能

Harness 是整个系统最容易被忽略、却最关键的生产安全边界之一。

### 它解决什么

LLM 天生不是可靠的状态机。可能发生：

- 一直重复调用同一个工具
- 工具失败后不断 Retry
- 得到错误结果后继续推理
- 在没有证据时继续猜
- Token 无限增长
- 单次 Incident 执行时间失控

### Harness 应控制

| 控制项 | 作用 |
|---|---|
| max_turns | 限制思考/行动轮数 |
| max_tool_calls | 限制工具调用次数 |
| max_tokens | 控制成本和上下文 |
| wall_time | 防止任务长期占用 Runtime |
| retry_budget | 限制失败重试 |
| repetition guard | 防止重复调用 |
| progress guard | 检测没有新信息的循环 |
| checkpoint | 支持恢复 |
| stop condition | 明确结束 |

### “主动发现问题”是怎么实现的

不是让模型凭空猜一个问题，而是让模型持续比较：

```text
当前目标
  vs
当前证据
```

如果目标要求回答“为什么 CPU 持续升高”，而现有证据只有 AlertManager 的一条告警，Agent 应识别：

```text
证据不足
→ 需要 Pod 指标
→ 调用 metrics tool
→ 获得新证据
→ 再判断是否需要 logs / changes
```

因此“主动发现”本质上是 **evidence gap detection**：发现当前结论所需证据还不存在。

## 6. Memory：解决“记不住”和“记太多”两个问题

Memory 不等于把所有聊天记录塞给模型。

推荐四层：

```text
L0 Current State
  当前 Incident 的必要状态

L1 Working Memory
  当前调查过程中的事实、假设、证据

L2 Incident Memory
  已结束 Incident 的摘要、结果、动作和验证

L3 Long-term Knowledge
  Runbook、故障模式、组织知识
```

### 写入策略

不是每条消息都进入长期记忆。只有稳定、可复用、经过验证的信息才应该升级。

例如：

```text
“我猜 Redis 可能有问题”
→ 不应该进入长期记忆

“2026-08-01 的 payment-api incident 中，Redis latency
在该版本发布后持续上升，回滚后恢复”
→ 可以作为历史 Incident Memory
```

### 解决记忆过头

通过：

- relevance retrieval
- recency
- confidence
- summarization
- deduplication
- evidence references

让模型只获得当前任务需要的历史信息。

## 7. Conversation：为什么对话不能直接等于 Context

Conversation 是用户与 Agent 的交互记录；Context 是 Agent 当前决策所需的工作状态。

两者必须分离。

```text
Conversation
  └── “刚才为什么判断是内存问题？”

Context
  ├── memory pressure evidence
  ├── pod resource limit
  ├── recent deployment
  └── current diagnosis
```

否则一个用户闲聊就可能挤掉 Agent 当前真正需要的诊断证据。

## 8. Evidence：把“模型说的”与“系统事实”分开

Evidence 是可信边界。

### 一条 Evidence 应尽量包含

```text
id
source
timestamp
resource
query
raw_reference
normalized_fact
quality
correlation_id
```

### Evidence 的生命周期

```text
Tool Call
  ↓
Raw Result
  ↓
Evidence Adapter
  ↓
Normalized Evidence
  ↓
Context Reference
  ↓
Agent Reasoning
```

### 为什么不能只把 tool result 给模型

因为 tool result 通常：

- 太大
- 格式不稳定
- 可能包含无关字段
- 无明确时间
- 无 provenance
- 不方便复用

Evidence 层的作用就是把“外部响应”变成“可引用事实”。

## 9. MCP：统一工具能力，但不承担业务编排

### MCP 解决的问题

如果每个 Agent 都自己集成：

```text
Kubernetes SDK
GitHub SDK
Prometheus API
CMDB API
Jenkins API
Cloud API
```

最终每个 Agent 都会有一套重复的工具适配代码。

MCP 可以把能力标准化为：

```text
list_tools
call_tool
structured_input
structured_output
```

### MCP 为什么容易污染 Context

最常见错误是：

```text
把所有 MCP Server 的全部工具定义
+ 所有工具历史结果
+ 所有 schema
全部放进 Prompt
```

解决方案：

```text
Capability Registry
      ↓
Task-based filtering
      ↓
Only expose relevant tools
      ↓
Call tool
      ↓
Normalize result
      ↓
Evidence reference
```

工具 schema 是运行时元数据；Evidence 是任务状态。两者不要混成无限聊天历史。

## 10. Connectors：隔离厂商差异

Connector 负责把：

```text
Platform Tool Contract
        ↓
Vendor Adapter
        ↓
External API
```

例如不同 Kubernetes / 云厂商的接口差异，都应该被 Connector 吸收。

这样 Agent 只需要知道：

```text
get_pod_status(target)
```

而不需要知道底层到底调用什么 SDK。

## 11. Action：为什么不能让 Agent 直接执行 Shell

如果 Agent 可以直接生成：

```bash
kubectl delete pod ...
```

那么模型错误、Prompt Injection、参数错误都会直接变成生产事故。

因此应该是：

```text
LLM
 ↓ structured action
Action Validator
 ↓
Policy
 ↓
Approval
 ↓
Executor
```

Action 是“意图的结构化表示”，不是“命令字符串”。

## 12. Approval：人不是异常处理，而是控制面

Approval 的意义不是“出问题了让人看看”，而是把高风险动作设计成人机协作状态。

例如：

```text
Read-only investigation
→ automatic

Test environment restart
→ policy-based auto approval

Production restart
→ human approval

Production database schema change
→ multi-party approval / blocked
```

审批记录必须绑定 action 的不可变版本。

## 13. Sandbox：解决工具/代码执行的爆炸半径

Agent 有时需要执行脚本、诊断程序或临时代码。Sandbox 用来降低执行风险。

隔离维度：

```text
CPU
Memory
Time
Filesystem
Network
Process
Credentials
```

尤其要避免把生产环境长期凭据直接注入模型可控的 shell 环境。

## 14. Audit：可解释性不是 Prompt，而是事件记录

Audit 应形成不可依赖模型记忆的执行链：

```text
incident.created
agent.started
evidence.collected
hypothesis.updated
tool.called
plan.created
approval.requested
approval.approved
action.started
action.completed
verification.started
incident.resolved
```

任何失败也要记录：

```text
action.failed
verification.failed
agent.timeout
agent.blocked
```

这样才能真正回答“系统为什么做了这个动作”。

## 15. Change：把“最近发生过什么变化”纳入 RCA

很多生产故障并不是系统自然退化，而是变更导致：

```text
deployment
config change
feature flag
infra change
dependency upgrade
```

Change 模块的价值是给 Agent 提供时间窗口内的变化证据。

典型 RCA：

```text
Alert at 10:05
        ↓
Find changes in 09:30~10:05
        ↓
Deployment at 09:58
        ↓
Error rate starts 09:59
        ↓
Compare before/after
        ↓
Change becomes a supported hypothesis
```

注意：时间相关性不是因果证明，仍需要其他 Evidence 验证。

## 16. Evaluation：从“测答案”升级到“测行为”

传统 LLM 测试只比较最终文本。可靠性 Agent 必须测轨迹。

### 示例

错误 Agent：

```text
收到 CPU 告警
→ 直接建议扩容
```

更好的 Agent：

```text
收到 CPU 告警
→ 查询 Pod
→ 查询 CPU trend
→ 查询 limits/requests
→ 查询最近变更
→ 发现 deployment change
→ 判断证据是否充分
→ 生成动作
→ 审批
→ 执行
→ 验证
```

因此 Evaluation 至少需要评价：

- Evidence coverage
- Tool selection
- Diagnosis
- Safety
- Action
- Verification
- Final outcome

## 17. Simulator：没有生产环境时如何证明 Agent

Simulator 通过构造故障场景验证 Agent。

每个 Scenario 应包含：

```text
Initial State
Evidence Set
Expected Investigation Opportunities
Expected Safe Boundaries
Expected Action
Expected Verification
```

然后运行 Replay：

```text
Scenario
 ↓
Agent
 ↓
Tool calls
 ↓
Evidence
 ↓
Plan
 ↓
Action simulation
 ↓
Verification
 ↓
Evaluation Report
```

这样可以在不触碰真实生产环境的情况下测试 Agent。

## 18. 为什么需要 packages

跨服务共享的稳定模型不应该复制到每个 service。

当前 packages 包含：

- `common`
- `models`
- `llm_sdk`

建议边界：

```text
packages/models
  → domain contracts

packages/common
  → generic shared utilities

packages/llm_sdk
  → provider-neutral LLM access
```

不要把某个具体 Service 的业务逻辑放进 common，否则 common 会逐渐变成“垃圾桶”。

## 19. LLM SDK：模型供应商应该是可替换的

Agent 不应直接依赖某一家模型厂商。

```text
Agent
 ↓
LLM Gateway / SDK
 ↓
Provider Adapter
 ├── OpenAI-compatible
 ├── Anthropic-compatible
 ├── Local model
 └── Other provider
```

这样可以实现：

- 模型降级
- 成本控制
- 能力路由
- 多模型 A/B
- provider 故障切换

## 20. Cloud：基础设施能力的统一边界

Cloud 层应该负责云资源查询和操作适配，而不是让 Agent 直接写云厂商 SDK。

未来可以形成：

```text
Cloud Capability
├── compute
├── network
├── storage
├── Kubernetes
├── IAM
└── observability
```

## 21. CI：为什么文档也要与工程验证绑定

仓库当前已有 Python validation、Enterprise validation、Terraform validation workflow。

文档描述的架构必须最终能在代码、测试和 CI 中找到对应物。

建议后续 CI 增加：

1. import boundary check
2. architecture dependency check
3. documentation link check
4. scenario replay smoke test
5. critical action safety test

## 22. 模块之间最重要的依赖规则

推荐保持单向依赖：

```text
Gateway
   ↓
Runtime
   ↓
Orchestration
   ↓
Agent / Evidence / Memory / Tools
   ↓
Action
   ↓
Approval / Policy
   ↓
Executor
   ↓
Verification
```

而不是：

```text
Agent ↔ Gateway ↔ Action ↔ MCP ↔ Agent
```

后者会形成循环依赖，最终任何模块修改都会影响整个系统。

## 23. 生产故障场景：CPU 告警

完整示例：

```text
1. AlertManager 发送 CPU high
2. Gateway 解析为 StandardEvent
3. Runtime 创建 Incident
4. Agent 检查当前 Evidence
5. 发现只有告警，没有趋势数据
6. 调用 metrics tool
7. 发现 CPU 在持续增长
8. 查询 Pod resource limits
9. 查询最近 deployment
10. 查询应用日志
11. 形成假设：新版本导致计算量上升
12. 查询变更 diff / 历史 incident
13. Evidence 支持回滚
14. 生成 rollback action
15. Policy 判断需要人工审批
16. Approval approved
17. ActionRuntime 执行 rollback
18. Verification 查询 Pod、error rate、latency、alert
19. 所有指标恢复
20. Incident resolved
21. Audit 保存完整轨迹
22. Evaluation 记录本次 Agent 行为
```

### 这个流程解决了什么问题

它避免：

```text
CPU high
→ 直接扩容
```

因为 CPU 高可能是：

- 流量上涨
- 内存压力导致 GC
- 死循环
- 新版本 bug
- 下游异常导致重试
- CPU limit 太低
- 节点争抢

Agent 必须通过 Evidence 缩小假设空间。

## 24. 最终设计目标

平台最终不是：

> “一个可以调用很多工具的 ChatGPT。”

而是：

> “一个拥有受控决策、证据、状态、权限、执行、验证和审计能力的可靠性运行时。”

核心闭环：

```text
Conversation
   ↓
Incident
   ↓
Investigation
   ↓
Evidence
   ↓
Decision
   ↓
Approval
   ↓
Action
   ↓
Verification
   ↓
Audit
   ↓
Evaluation
   ↓
Memory / Evolution
   └───────────────→ 下一次 Incident
```
