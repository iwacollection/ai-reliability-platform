# Agent Loop 与 Harness 设计

> Agent Loop 是“观察 → 决策 → 工具 → 结果 → 再决策”的循环；Harness 是约束这个循环能够安全、可控、可恢复地运行的运行时控制层。

## 1. 为什么需要 Harness

直接把大模型放进一个 `while True` 循环并不能形成生产级 Agent。模型可能：

- 重复调用同一个工具；
- 在没有新证据时继续思考；
- 工具失败后不断重试；
- 生成不存在的工具参数；
- 在事故已经恢复后继续执行；
- 消耗过多 Token、时间或工具调用额度；
- 在高风险动作前绕过审批。

因此平台必须把“模型可以提出什么”与“运行时允许做什么”分开。

## 2. 标准 Agent Loop

```text
┌──────────┐
│ Observe  │ 当前事件、Context、Evidence
└────┬─────┘
     ▼
┌──────────┐
│ Assess   │ 判断当前证据是否足够
└────┬─────┘
     ▼
┌──────────────┐
│ Decide Next  │ 调查 / 诊断 / 动作 / 结束
└──────┬───────┘
       ▼
┌────────────────┐
│ Policy / Guard │ 运行时安全检查
└───────┬────────┘
        ▼
┌──────────────┐
│ Tool / Action│
└──────┬───────┘
       ▼
┌──────────────┐
│ Validate     │ 检查结果是否可信、是否成功
└──────┬───────┘
       ▼
   更新 Context
       │
       └──────────────→ 下一轮
```

## 3. Agent 的决策不能直接执行

模型输出应该首先进入 Runtime，而不是直接进入 Executor：

```text
LLM Output
   ↓
Structured Decision
   ↓
Schema Validation
   ↓
Tool Registry Check
   ↓
Permission Check
   ↓
Policy Check
   ↓
Budget Check
   ↓
Approval Check
   ↓
Executor
```

这条链是防止“模型一句话改变生产系统”的核心边界。

## 4. Harness 的职责

Harness 至少负责以下控制：

### 4.1 最大轮数

每个 Incident 设置最大 Agent Loop 次数。达到上限后不能继续无限调查，应进入：

```text
MAX_ITERATIONS
   ↓
生成当前证据摘要
   ↓
标记调查未完成
   ↓
人工介入 / 后续任务
```

最大轮数不是唯一预算，还需要结合时间、Token、工具调用次数和动作风险。

### 4.2 时间预算

整个 Incident 和单次 Tool Call 都应有独立超时：

```text
Incident Timeout
 ├── Investigation Timeout
 ├── Tool Timeout
 ├── Approval Timeout
 └── Verification Timeout
```

避免一个故障因为某个外部 API 卡死而永久占用 Agent。

### 4.3 重试预算

重试应该按错误类型决定：

- 网络瞬态错误：有限次数重试；
- 参数错误：不应该原样重试，应修正参数；
- 权限错误：停止并报告；
- 资源不存在：停止并重新判断目标；
- 服务端持续错误：进入降级；
- 高风险 Action 失败：不能自动无限重试。

## 5. 防止重复调用

Agent 很容易出现：

```text
query_pod
query_pod
query_pod
query_pod
...
```

Runtime 应记录工具调用签名：

```text
tool_name + normalized_arguments + relevant_time_window
```

如果连续重复且没有新状态变化，应触发重复调用保护，并要求 Agent 改变调查策略。

## 6. “没有进展”检测

比最大轮数更重要的是 Progress Detection。

如果连续多轮：

- 没有新增 Evidence；
- 没有改变假设；
- 没有改变调查方向；
- 只是重复工具调用；
- 置信度没有变化；

说明 Agent 已经陷入循环。

此时应：

```text
No Progress
   ↓
压缩当前状态
   ↓
要求重新选择调查方向
   ↓
仍无进展 → Stop / Escalate
```

## 7. 工具失败处理

工具失败不能简单交给 LLM 一句“工具调用失败”。Runtime 应先分类：

```text
Tool Error
 ├── Retryable
 ├── InvalidInput
 ├── Unauthorized
 ├── NotFound
 ├── Timeout
 ├── RateLimited
 └── InternalError
```

不同错误进入不同恢复策略。

## 8. Checkpoint

每一个有意义的状态转移都可以形成 Checkpoint：

```text
checkpoint = {
  incident_state,
  context_summary,
  evidence_ids,
  hypotheses,
  pending_action,
  approval_state,
  loop_count,
  budget,
  last_successful_step
}
```

这样即使 Runtime 崩溃，也可以从确定状态恢复。

## 9. Stop 条件

Agent 不能只知道“继续”，还必须明确知道什么时候停止：

### 成功停止

- 根因达到要求的置信度；
- 必要证据已经收集；
- 修复动作完成；
- Verification 通过。

### 安全停止

- 风险超过策略；
- 缺少权限；
- 需要人工审批；
- 目标不明确；
- 证据互相冲突。

### 资源停止

- 最大轮数；
- 时间预算；
- Token 预算；
- Tool Call 预算。

## 10. Agent 与 Harness 的职责边界

| 问题 | Agent | Harness |
|---|---|---|
| 下一步调查什么 | 负责 | 校验预算 |
| 哪个工具最有价值 | 决策 | 检查是否允许 |
| 根因假设 | 负责 | 要求证据 |
| 最大轮数 | 不决定 | 强制执行 |
| 权限 | 不决定 | 强制执行 |
| 审批 | 请求 | 强制状态机 |
| 是否允许高风险动作 | 不决定 | Policy Gate |
| 超时 | 不决定 | 强制停止 |
| 崩溃恢复 | 不决定 | Checkpoint |

核心原则：**LLM 可以提出决策，但不能定义自己的安全边界。**

## 11. 与主动调查的关系

Harness 并不让 Agent 失去自主性。它约束的是“行动空间”，而不是替 Agent 写死调查流程。

```text
Agent：下一步应该查 Deployment
Harness：检查该 Tool 是否注册、权限是否允许、预算是否足够
Agent：调用 Deployment Evidence Tool
Harness：执行并记录
Agent：根据结果决定下一步
```

## 12. 生产验收

至少需要验证：

- 无限循环能够被停止；
- 工具超时能够恢复；
- 工具重复调用能够被识别；
- 参数错误不会无限重试；
- 高风险 Action 无法绕过 Approval；
- Runtime 重启后能够从 Checkpoint 恢复；
- Verification 失败后不会错误标记 Incident 已解决；
- 所有 Loop、Tool、Action 和 Stop 原因都有审计记录。
