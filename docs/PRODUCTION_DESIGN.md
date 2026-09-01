# Production Engineering Design

> 本文从生产系统角度说明 AI Reliability Platform 如何从“能运行的 Agent”走向“可以被 SRE 信任的 Agent Runtime”。重点不是模型效果，而是可靠性、安全性、容量、故障恢复、可观测性和治理。

## 1. 为什么 Agent 系统比普通微服务更难做可靠

普通服务通常是：

```text
Input
 ↓
Deterministic Code
 ↓
Output
```

Agent 系统是：

```text
Input
 ↓
Probabilistic Decision
 ↓
Tool
 ↓
External State Change
 ↓
New Observation
 ↓
Probabilistic Decision
```

因此它同时拥有：

- 分布式系统的不确定性
- LLM 的概率性
- 外部 API 的失败
- 生产操作的风险
- 长任务的状态恢复问题
- 上下文和成本问题

所以生产化重点必须从“回答准确”扩展到“行为可控”。

## 2. Reliability Boundary

整个系统建议划分为四个信任域：

```text
                 Untrusted
┌───────────────────────────────────────┐
│ User Input / Alert / Tool Result      │
└──────────────────┬────────────────────┘
                   ↓
             AI Reasoning
┌───────────────────────────────────────┐
│ LLM / Agent / Memory Retrieval        │
└──────────────────┬────────────────────┘
                   ↓
             Runtime Control
┌───────────────────────────────────────┐
│ Schema / Policy / Auth / Budget       │
└──────────────────┬────────────────────┘
                   ↓
               Trusted
┌───────────────────────────────────────┐
│ Executor / Infrastructure             │
└───────────────────────────────────────┘
```

关键思想：越接近生产资源，越不能依赖模型的自觉。

## 3. 高风险操作的防线

生产写操作至少经过：

```text
LLM Proposal
 ↓
Schema Validation
 ↓
Semantic Validation
 ↓
Target Validation
 ↓
Authorization
 ↓
Risk Policy
 ↓
Approval
 ↓
Execution Isolation
 ↓
Verification
```

任何一个环节失败，都应该阻止继续执行。

## 4. 权限模型

建议使用最小权限原则。

### 4.1 Tool Permission

```text
k8s.pods.read
k8s.metrics.read
k8s.deployments.read
k8s.deployments.write
k8s.secrets.read
```

### 4.2 Resource Scope

权限不能只有“能不能调用 restart”，还必须限制：

```text
Environment
Cluster
Namespace
Resource
Operation
```

例如：

```text
Agent A
允许：staging/payment/*
禁止：production/*
```

### 4.3 Environment Policy

建议明确：

```text
Development → broad automation
Staging     → controlled automation
Production  → least privilege + approval
Critical    → multi-party approval / blocked
```

## 5. 凭据管理

模型不应该直接获得长期生产凭据。

错误：

```text
Prompt
 ↓
AWS_ACCESS_KEY
 ↓
Shell
```

正确：

```text
Agent
 ↓
Authorized Tool
 ↓
Short-lived credential
 ↓
External API
```

凭据应该尽可能：

- 短时有效
- 最小权限
- 与 Incident / Action 绑定
- 不进入模型 Context
- 不进入普通日志

## 6. Prompt Injection 的生产防护

需要假设所有外部数据都是潜在不可信输入：

```text
Alert text
Log line
Ticket description
Git commit message
Kubernetes annotation
Tool response
```

它们可以包含类似指令的文本，但这些文本只是数据，不是系统指令。

因此必须保持：

```text
Instruction hierarchy
    ≠
External data
```

并在最终执行前再次通过 Policy 防线。

## 7. Observability

Agent 系统不能只监控 CPU、Memory、QPS。

需要同时观察：

### Runtime Metrics

```text
incident_started_total
incident_resolved_total
incident_failed_total
agent_turns
agent_tool_calls
agent_duration
```

### Tool Metrics

```text
tool_call_total
tool_call_failure_total
tool_timeout_total
tool_latency
```

### LLM Metrics

```text
llm_request_total
llm_latency
input_tokens
output_tokens
estimated_cost
model_error_total
```

### Safety Metrics

```text
policy_denied_total
approval_requested_total
approval_rejected_total
sandbox_blocked_total
```

### Quality Metrics

```text
evidence_coverage
wrong_action_rate
verification_failure_rate
escalation_rate
```

## 8. Trace 一定要贯穿整个 Incident

建议所有操作共享：

```text
incident_id
trace_id
correlation_id
action_id
tool_call_id
```

例如：

```text
Incident INC-123
  │
  ├── Agent Turn 7
  │      └── Tool Call TC-99
  │             └── Evidence E-42
  │
  ├── Action A-18
  │      └── Approval AP-3
  │
  └── Verification V-9
```

这样可以从一次用户看到的 Incident 直接追踪到具体 API 调用。

## 9. 长任务架构

Agent 不应该依赖一个长期 HTTP request。

推荐：

```text
API Request
 ↓
Create Incident
 ↓
Persist State
 ↓
Queue / Scheduler
 ↓
Worker
 ↓
Agent Loop
 ↓
Checkpoint
```

用户侧通过：

```text
Incident ID
Conversation
Event stream
```

查看进度，而不是一直等待 HTTP 请求。

## 10. Worker 崩溃后的恢复

Worker 可能：

```text
OOM
Node failure
Network partition
Process restart
Deployment
```

恢复流程：

```text
Worker dies
 ↓
Lease expires
 ↓
Another Worker acquires Incident
 ↓
Load checkpoint
 ↓
Validate pending operation
 ↓
Resume safely
```

必须避免两个 Worker 同时处理同一个 Incident。

因此需要：

```text
Lease / Lock
+ fencing token
+ checkpoint version
```

## 11. Exactly-once 不应该成为执行假设

在分布式系统中，真正的外部 Exactly-once 很难保证。

更现实的设计是：

```text
At-least-once delivery
+
Idempotent operation
+
Deduplication
+
External status reconciliation
```

例如：

```text
restart action
```

必须能够判断：

```text
already executed
still running
never started
failed
```

而不是根据本地进程是否收到响应来猜测。

## 12. Incident 幂等

同一个告警可能重复发送：

```text
Alert 1
Alert 2
Alert 3
```

需要通过 fingerprint / dedup key 关联：

```text
source
alertname
resource
labels
window
```

从而避免：

```text
同一个故障
→ 创建 3 个 Incident
→ 启动 3 个 Agent
→ 执行 3 次修复
```

## 13. 并发控制

Agent 系统很容易出现“同一资源多个 Incident 同时修复”。

例如：

```text
Incident A → restart payment-api
Incident B → rollback payment-api
Incident C → scale payment-api
```

需要 Resource-level coordination：

```text
Resource Lock
    ↓
One active mutating action
    ↓
Other incidents observe / wait / escalate
```

锁的粒度应该足够细，避免整个集群被一个 Incident 锁死。

## 14. Rate Limit 与 Backpressure

Agent 自动化可能产生突发流量：

```text
1000 alerts
 ↓
1000 Agents
 ↓
10000 tool calls
```

因此入口和 Runtime 都需要限流：

```text
Event rate limit
Incident concurrency limit
Agent concurrency limit
Tool QPS limit
Provider rate limit
```

当达到上限时，不应该无限排队。

应该根据优先级：

```text
Critical
 ↓
High
 ↓
Normal
 ↓
Low
```

进行调度和丢弃/合并策略。

## 15. 告警风暴处理

Alert storm 是可靠性平台本身最容易被压垮的情况之一。

需要：

```text
Deduplication
 ↓
Grouping
 ↓
Correlation
 ↓
Incident aggregation
 ↓
Single investigation
```

例如 500 个 Pod 同时出现同类错误，不应该创建 500 个独立 Agent。

应该尽量形成：

```text
Cluster Incident
   ├── Deployment A
   ├── Deployment B
   └── Pods...
```

## 16. 降级策略

依赖失败时 Agent Runtime 也应该能继续工作在较低能力模式。

例如：

```text
LLM unavailable
→ use deterministic runbook / escalate

Metrics unavailable
→ logs + recent changes

MCP server unavailable
→ alternate connector

Memory unavailable
→ current Incident context only

Approval service unavailable
→ block write action
```

尤其注意：

> 安全依赖不可用时，默认应该 Fail Closed，而不是为了“自动化可用”而绕过安全控制。

## 17. 数据一致性

需要区分三类状态：

```text
Runtime State
External System State
Observation State
```

例如：

```text
Runtime：Action EXECUTED
External：Deployment still progressing
Observation：Metrics not recovered
```

因此不能只看 Runtime State 判断事故是否恢复。

## 18. Event Sourcing 思路

不一定要求完整 Event Sourcing，但 Incident 的关键变化建议保留事件：

```text
IncidentCreated
EvidenceCollected
HypothesisChanged
ToolCalled
PlanCreated
ApprovalRequested
ActionStarted
ActionCompleted
VerificationCompleted
```

好处：

- 可审计
- 可重放
- 可调试
- 可评估
- 可恢复

## 19. Agent 质量不能只看最终答案

需要至少四层评价：

```text
Decision Quality
Tool Selection
Safety Behavior
Outcome
```

例如最终回答“应该回滚”可能是正确的，但如果 Agent：

```text
没有查最近变更
没有验证 Evidence
没有审批
直接执行生产回滚
```

仍然应该判定为失败。

## 20. Badcase 分类

建议持续积累 Badcase：

```text
1. Hallucination
2. Wrong Tool
3. Wrong Parameters
4. Missing Evidence
5. Infinite Loop
6. Duplicate Action
7. Permission Bypass
8. Wrong Target
9. Failed Verification
10. Context Loss
11. Memory Retrieval Error
12. Prompt Injection
13. Tool Failure Handling Error
14. Incorrect Escalation
```

每个 Badcase 最终都应该进入：

```text
Scenario
 → Replay
 → Assertion
 → Evaluation
 → Regression Test
```

## 21. 生产发布策略

Agent Runtime 本身的变更可能改变生产行为，因此不应只做普通单元测试。

建议：

```text
Unit Test
 ↓
Integration Test
 ↓
Scenario Replay
 ↓
Regression Benchmark
 ↓
Staging
 ↓
Canary
 ↓
Production
```

对于模型 / Prompt / Tool Policy 变化尤其如此。

## 22. Model 与 Runtime 解耦

模型应该是可替换依赖：

```text
Agent Runtime
      ↓
LLM Gateway
      ↓
Provider A / Provider B / Local Model
```

Runtime 不应该依赖某一个模型的特殊输出格式。

所有模型输出最终都应该经过统一解析和验证。

## 23. 成本控制

Agent 成本不仅是 LLM token。

总成本可以近似理解为：

```text
LLM Cost
+
Tool Cost
+
Infrastructure Cost
+
Investigation Duration
```

因此 Harness 的 Budget 不只是防死循环，也承担成本控制职责。

可以按 Incident 设置：

```text
max_tokens
max_turns
max_tool_calls
max_wall_time
max_estimated_cost
```

## 24. 数据保留与隐私

不同数据应该有不同生命周期：

```text
Raw Alert
Short retention

Raw Tool Result
Short retention

Normalized Evidence
Longer retention

Incident Summary
Long retention

Audit Record
Policy-driven retention
```

尤其避免把：

```text
password
access token
secret
PII
```

直接写进 Prompt、普通日志或长期 Memory。

## 25. Disaster Recovery

关键数据至少需要考虑：

```text
Incident State
Checkpoint
Approval State
Audit Trail
Evidence Metadata
Memory
```

恢复优先级建议：

```text
1. Safety / Policy state
2. Incident state
3. Action state
4. Evidence
5. Conversation
6. Long-term memory
```

因为恢复过程中最危险的事情是：不知道之前已经执行过什么，却再次执行写操作。

## 26. SLO 建议

平台自身也应该有 SLO。

例如：

```text
Event ingestion availability
Incident creation latency
Investigation start latency
Tool success rate
Agent completion rate
Verification completion rate
Approval latency
```

但不能只追求 Agent 自动解决率。

更重要的质量指标包括：

```text
Unsafe action rate
False remediation rate
Wrong-target rate
Verification failure rate
Human escalation quality
```

## 27. 生产可信度模型

一个可以用于项目演进的简单模型：

```text
Trustworthiness
=
Evidence Quality
×
Decision Quality
×
Safety Enforcement
×
Verification Quality
×
Operational Reliability
```

任何一项接近 0，最终可信度都会显著下降。

这也是为什么平台不能只优化 Prompt。

## 28. 最终生产架构

长期目标可以形成：

```text
                         Users / ChatOps
                               │
                               ▼
                         ┌───────────┐
                         │  Gateway  │
                         └─────┬─────┘
                               │
                     ┌─────────▼─────────┐
                     │ Incident Control  │
                     │ State / Queue     │
                     └─────────┬─────────┘
                               │
                     ┌─────────▼─────────┐
                     │   Agent Runtime   │
                     │ Loop + Context    │
                     │ Harness + Policy  │
                     └───┬─────┬─────┬───┘
                         │     │     │
                       MCP   Memory Evidence
                         │     │     │
                         └─────┼─────┘
                               │
                         Action Layer
                               │
                     Approval / Authorization
                               │
                           Executor
                               │
                        Infrastructure
                               │
                         Verification
                               │
                         Audit / Eval
                               │
                         Memory / Learning
```

## 29. 最重要的工程结论

### 结论 1
Agent 的智能来自模型，但 Agent 的可靠性来自 Runtime。

### 结论 2
Evidence 是降低幻觉的核心机制，但不能替代权限和 Policy。

### 结论 3
Approval 是执行控制面，不应该依赖 Prompt。

### 结论 4
Verification 是“自动修复”真正成立的条件。

### 结论 5
Checkpoint + Idempotency 是长任务 Agent 能进入生产的基础。

### 结论 6
MCP 解决工具标准化，不应该承担 Incident 编排。

### 结论 7
Memory 解决跨 Incident 的知识复用，Context 解决当前决策，不应该混为一谈。

### 结论 8
Multi-Agent 应该最后引入；否则会把单 Agent 的可靠性问题放大。

### 结论 9
真正的 Agent 评估应该评估“轨迹和行为”，而不只是最终文本。

### 结论 10
生产级 Agent 的核心不是“更聪明”，而是“在不确定的情况下仍然可控、可恢复、可解释”。
