# Incident Conversation：Incident 上下文与对话模型

## 1. 为什么需要独立的 Incident Conversation

普通聊天记录只保存“用户说了什么、Agent 回答了什么”。生产故障处理还需要保存：

```text
Incident
Objective
Evidence
Hypothesis
Decision
Tool Call
Action
Approval
Verification
Human Message
```

因此 Conversation 是 Incident 的协作视图，而不是唯一事实来源。

## 2. Conversation 与 Incident 的关系

```text
Incident
 ├── Conversation
 ├── Evidence
 ├── Investigation
 ├── Actions
 ├── Approvals
 └── Audit Events
```

Conversation 可以展示这些对象，但不能取代它们的结构化存储。

## 3. 消息类型

建议区分：

```text
USER_MESSAGE
AGENT_MESSAGE
SYSTEM_MESSAGE
TOOL_SUMMARY
EVIDENCE_SUMMARY
ACTION_PROPOSAL
APPROVAL_REQUEST
APPROVAL_RESULT
VERIFICATION_RESULT
ESCALATION
```

这样 UI 和 Runtime 不需要通过自然语言猜测事件类型。

## 4. Agent 消息不能成为事实来源

例如 Agent 说：

```text
数据库连接池耗尽导致故障。
```

这只是 Hypothesis / Diagnosis，必须引用 Evidence。

```text
Diagnosis
 ↓
Evidence refs
 ↓
Verification
```

## 5. Conversation Context

给 Agent 的上下文不应该是完整聊天记录无限增长。

建议构造：

```text
Incident Objective
Current State
Recent Human Messages
Relevant Evidence
Active Hypotheses
Recent Decisions
Pending Action
Approval State
Verification State
```

无关历史可以摘要或归档。

## 6. 人工输入

人工可以：

```text
提供新 Evidence
修正目标
拒绝 Action
批准 Action
取消 Action
要求重新调查
提升优先级
```

人工输入必须进入 Incident 的结构化事件流，而不是只作为一段文本存在。

## 7. 审批消息

审批消息应该明确：

```text
发生了什么
为什么认为这个 Action 有效
将操作什么资源
预计影响
风险
Rollback
Verification
有效期
```

批准后生成结构化 Approval，不应该通过“好的”这种自然语言触发执行。

## 8. 对话恢复

用户离开后再次进入：

```text
Load incident
 ↓
Load current state
 ↓
Load checkpoint
 ↓
Load relevant evidence
 ↓
Resume conversation
```

不需要重新把整个历史 Prompt 给模型。

## 9. 并发消息

同一个 Incident 可能同时收到：

```text
Alert update
Human message
Tool result
Worker result
```

因此需要事件顺序和版本控制，避免旧消息覆盖新状态。

## 10. 对话安全

Conversation 中的外部内容仍然属于不可信输入。

用户不能通过聊天消息直接绕过：

```text
Permission
Policy
Approval
```

例如：

```text
“我批准你删除数据库。”
```

并不能自动产生生产 Delete Approval，除非平台定义的审批流程确实允许这种身份和动作。

## 11. Conversation 与 Audit

Conversation 用于协作与理解。

Audit 用于回答：

```text
谁
什么时候
通过什么身份
提出什么 Action
Policy 为什么允许/拒绝
谁批准
实际执行了什么
最终验证结果是什么
```

两者必须分开。

## 12. 最终模型

```text
Conversation
    ↓
Human / Agent collaboration
    ↓
Structured Incident State
    ↓
Evidence / Action / Approval / Verification
    ↓
Audit
```

Conversation 是入口和协作层，不是生产安全边界。
