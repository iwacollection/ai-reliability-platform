# Incident 生命周期与状态机设计

> Incident 是 Agent Runtime 的长期工作单元。它不是一次 HTTP 请求，也不是一条聊天消息。本文定义 Incident 如何创建、运行、暂停、恢复、失败、验证和关闭。

## 1. 为什么必须有 Incident

如果只把 Agent 当作：

```text
HTTP Request → LLM → Response
```

那么 Runtime 无法可靠处理：

- 长时间调查
- 工具超时
- 人工审批等待
- Runtime 重启
- Action 执行中断
- Verification 失败
- 用户稍后继续询问

Incident 提供稳定的业务身份：

```text
incident_id
```

所有 Event、Evidence、Decision、Tool Call、Action、Approval、Verification、Audit 都应该围绕这个 ID 关联。

## 2. 推荐状态模型

```text
CREATED
   ↓
TRIAGING
   ↓
INVESTIGATING
   ↓
DIAGNOSED
   ↓
PLAN_READY
   ↓
┌───────────────────────────┐
│ Policy / Approval         │
└───────────────────────────┘
   ↓              ↓
EXECUTING      BLOCKED
   ↓
VERIFYING
   ↓
┌───────────────┐
│               │
RESOLVED     VERIFICATION_FAILED
                  ↓
              INVESTIGATING
```

任何阶段还可能因为外部条件进入：

```text
FAILED
TIMED_OUT
CANCELLED
ESCALATED
```

## 3. 状态和事实必须分离

状态回答：

```text
Incident 当前处于什么阶段？
```

Evidence 回答：

```text
系统实际发生了什么？
```

不能因为：

```text
Action API 返回 200
```

就直接把 Incident 状态改成 `RESOLVED`。

正确流程：

```text
Action completed
   ↓
Verification
   ↓
Service healthy
   ↓
RESOLVED
```

## 4. 状态转换必须有前置条件

### CREATED → TRIAGING

条件：

- Incident 已生成唯一 ID
- 原始事件已保存
- StandardEvent 已生成

### TRIAGING → INVESTIGATING

条件：

- Incident Objective 已确定
- 初始上下文已建立

### INVESTIGATING → DIAGNOSED

条件：

- 已获得足够 Evidence
- 当前 Diagnosis 有引用
- 关键 Hypothesis 已完成验证或明确标注不确定

### DIAGNOSED → PLAN_READY

条件：

- Action 已结构化
- Target 已确定
- 前置条件已定义
- Verification 已定义
- Rollback 已定义或明确无需回滚

### PLAN_READY → EXECUTING

必须经过：

```text
Action Validation
→ Permission Check
→ Policy Check
→ Approval Decision
```

### EXECUTING → VERIFYING

条件：

- Action 执行结果已持久化
- Execution ID 已记录

### VERIFYING → RESOLVED

必须存在独立 Verification Evidence。

## 5. 为什么状态机不能由 LLM 自己维护

错误：

```text
Prompt:
如果成功就认为 incident resolved。
```

模型无法可靠保证状态一致性。

正确方式：

```text
LLM 提议下一步
       ↓
Runtime State Machine
       ↓
检查当前状态是否允许该动作
       ↓
允许 / 拒绝
       ↓
持久化状态
```

模型是 Decision Maker，不是 State Authority。

## 6. 并发与重复执行

同一个 Incident 可能出现：

```text
Webhook 重试
用户重复点击
Runtime 重启恢复
Worker 重试
消息重复投递
```

因此必须设计幂等键：

```text
incident_id
execution_id
action_id
approval_id
verification_id
```

对于 Action 尤其要避免：

```text
第一次 restart 已经成功
Runtime 没收到响应
↓
Retry
↓
第二次 restart
```

生产动作应区分：

```text
not_started
started
completed
unknown
```

`unknown` 不能简单当成失败并再次执行。

## 7. Checkpoint

Checkpoint 用于保存 Agent 长任务的恢复点。

至少应该包含：

```text
incident_id
state
current_turn
budget
objective
hypotheses
selected_hypothesis
evidence_refs
recent_tool_calls
pending_action
approval_ref
execution_ref
verification_plan
updated_at
```

恢复流程：

```text
Runtime restart
   ↓
Load checkpoint
   ↓
Validate checkpoint version
   ↓
Recover pending state
   ↓
Check external execution status
   ↓
Continue / compensate / escalate
```

## 8. 工具失败怎么处理

工具失败不能统一成“Retry”。

应分类：

```text
Timeout
Transient error
Rate limit
Authentication error
Permission denied
Invalid argument
Target not found
External state unknown
```

例如：

```text
Timeout
→ 有限重试

Permission denied
→ 不重试，进入 BLOCKED / ESCALATED

Invalid argument
→ 修正参数或重新规划

External state unknown
→ 查询实际状态，不直接重复写操作
```

## 9. Agent 失败与 Action 失败必须分离

### Agent 失败

例如：

```text
LLM timeout
context overflow
invalid decision
max turns exceeded
```

系统状态可能仍然安全。

### Action 失败

例如：

```text
restart failed
scale failed
rollback failed
```

此时可能已经改变了一部分系统状态，必须进入补偿和验证流程。

## 10. Verification 失败后的闭环

```text
EXECUTING
   ↓
Action completed
   ↓
VERIFYING
   ↓
Health check failed
   ↓
VERIFICATION_FAILED
   ↓
分析：
- Action 没有生效？
- 诊断错误？
- 出现第二个问题？
- 需要 rollback？
   ↓
重新 INVESTIGATING
```

这就是为什么平台不是简单的“自动修复脚本集合”。

## 11. 人工介入

以下情况应该支持 Escalation：

```text
证据不足
权限不足
Policy 拒绝
关键工具不可用
风险超过自动化边界
持续 Verification 失败
预算耗尽
```

人工介入不是 Runtime 失败，而是一种合法状态。

## 12. 关闭 Incident 的条件

推荐同时满足：

```text
1. Incident Objective 已满足
2. Verification 通过
3. 关键 Action 已有结果
4. 没有 pending execution
5. 没有 pending approval
6. 审计事件完整
```

如果只是“Agent 不知道怎么办”，不能关闭为 RESOLVED。

## 13. 状态机的工程原则

```text
LLM：提出决策
Runtime：控制状态
Policy：控制允许范围
Executor：执行动作
Evidence：记录事实
Verification：判断恢复
Audit：记录全过程
```

这套分工让 Agent 可以保持灵活，而平台仍然保持确定性边界。