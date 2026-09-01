# Agent 故障排查：Runtime、模型、Tool、Policy 全链路诊断

## 1. 排障原则

Agent 故障不能只看 Agent 日志。

完整链路：

```text
Incident
 ↓
Gateway
 ↓
Runtime
 ↓
Model
 ↓
Context / Memory
 ↓
Tool Registry
 ↓
MCP / Connector
 ↓
Policy
 ↓
Approval
 ↓
Executor
 ↓
Verification
```

先定位“哪一层坏了”，再决定处理方式。

## 2. 第一阶段：判断范围

先回答：

```text
所有 Incident 都失败？
只有某个 Agent？
只有某个 Model？
只有某个 Tool？
只有某个 Provider？
只有写操作？
```

这是最快的故障分层方式。

## 3. Runtime 故障

典型现象：

```text
Run timeout
Worker crash
Queue backlog
Memory growth
Loop stuck
```

检查：

```text
worker health
queue depth
active runs
run duration
CPU
memory
GC
open connections
```

## 4. Agent 无限循环

重点观察 Trace：

```text
Tool A
 ↓
Tool B
 ↓
Tool A
 ↓
Tool B
```

如果重复调用没有新 Evidence：

```text
progress = false
```

应触发 Loop Guard。

## 5. Context 爆炸

现象：

```text
context overflow
latency increase
cost increase
model failure
```

检查：

```text
evidence count
tool output size
memory size
conversation size
compaction count
```

处理：

```text
Filter
 ↓
Summarize
 ↓
Compress
 ↓
Keep Evidence IDs
```

## 6. 模型故障

### Timeout

```text
Model timeout
 ↓
Retry
 ↓
Fallback
 ↓
Degrade
```

### 429

```text
检查 Provider quota
 ↓
Backoff
 ↓
降低并发
 ↓
Fallback
```

### 5xx

判断是否 Provider 故障，再决定 Retry。

### Context Overflow

不是简单 Retry。

应该：

```text
Compaction
 ↓
Evidence Projection
 ↓
Retry
```

## 7. Tool 故障

先分类：

```text
Timeout
Permission
Not Found
Rate Limit
Invalid Parameter
Server Error
```

不要把所有 Tool Error 都交给模型自由发挥。

## 8. Permission Denied

```text
403
 ↓
检查 Runtime Permission
 ↓
检查 Resource Scope
 ↓
检查 Provider RBAC
```

不要直接 Retry。

## 9. Tool 返回空数据

必须区分：

```text
真实无数据
vs
查询错误
vs
权限过滤
vs
数据源异常
```

否则 Agent 可能把“查不到”理解成“不存在”。

## 10. MCP 故障

检查：

```text
MCP server health
connection
protocol compatibility
tool discovery
schema
latency
error rate
```

如果 MCP Server 不可用：

```text
尝试备用 Evidence Source
否则标记 Evidence Gap
```

禁止编造结果。

## 11. Policy 故障

### Policy DENY

先确认：

```text
Action
Target
Principal
Environment
Risk
Policy Version
```

DENY 不是系统故障。

### Policy Timeout

生产 Mutation：

```text
FAIL CLOSED
```

进入 HOLD / ESCALATE。

## 12. Approval 故障

检查：

```text
approval_id
status
expiry
action_fingerprint
approver
```

Approval 存在但 Fingerprint 不一致：

```text
INVALID
```

## 13. Executor 故障

最危险情况：

```text
请求超时
 ↓
不知道是否已经执行
```

必须先查询实际状态，再决定 Retry。

## 14. Verification 故障

如果 Verification Tool 挂掉：

```text
不能标记 RESOLVED
```

应进入：

```text
VERIFYING / UNKNOWN
```

## 15. Gateway 故障

如果 Alert 无法进入系统：

检查：

```text
Ingress
Webhook
Authentication
Parser
Queue
```

重点防止：

```text
Alert 丢失
Alert 重复
```

## 16. Queue 积压

检查：

```text
queue depth
arrival rate
processing rate
worker count
model latency
Tool latency
```

如果：

```text
arrival rate > processing rate
```

需要扩容或 Admission Control。

## 17. 数据库 / Evidence Store

检查：

```text
connection pool
latency
IOPS
storage
lock
error rate
```

Evidence Store 不可用时应根据能力降级，而不是生成无来源的 RCA。

## 18. 安全异常

发现：

```text
绕过 Policy
越权 Tool Call
Prompt Injection
异常 Action
```

立即：

```text
停止 Mutation
保留 Audit
隔离相关 Agent / Tool
人工调查
```

## 19. 标准排障顺序

```text
1. Incident 是否进入
2. Runtime 是否运行
3. Queue 是否积压
4. Model 是否可用
5. Context 是否异常
6. Tool Registry 是否正常
7. MCP / Connector 是否正常
8. Policy 是否正常
9. Approval 是否正常
10. Executor 是否正常
11. Verification 是否正常
```

## 20. 排障证据

每次排障必须保存：

```text
run_id
incident_id
trace_id
agent_version
model_version
tool_registry_version
policy_version
error
latency
state
```

## 21. 恢复后验证

修复后不能只检查：

```text
HTTP 200
```

应该重新运行：

```text
Synthetic Incident
 ↓
Investigation
 ↓
Tool
 ↓
Policy
 ↓
Verification
```

确认完整链路恢复。
