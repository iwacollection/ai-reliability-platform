# Connector 设计规范：外部系统接入 Agent 的统一工程标准

## 1. Connector 的真正职责

Connector 不是 SDK 包装器，也不是给 Agent 暴露一个万能 API。

它负责完成：

```text
External System
 ↓
Authentication
 ↓
Authorization
 ↓
Query / Mutation
 ↓
Timeout / Retry / Rate Limit
 ↓
Normalize
 ↓
Evidence
 ↓
Provenance
 ↓
Context Projection
 ↓
Agent
```

## 2. 为什么必须统一 Connector

如果每个 Agent 自己接外部系统，会出现：

```text
Agent A → Kubernetes SDK
Agent B → Kubernetes SDK
Agent C → kubectl shell
```

最终导致：

- 权限不一致
- 错误处理不一致
- Evidence 格式不一致
- 审计缺失
- 无法 Replay
- 无法统一限流

Connector 应成为平台能力，而不是 Agent 私有代码。

## 3. 四层模型

推荐拆成：

```text
Connector Interface
        ↓
Provider Adapter
        ↓
External Client
        ↓
External System
```

例如：

```text
get_workload()
 ↓
Kubernetes Adapter
 ↓
Kubernetes Client
 ↓
Kubernetes API
```

Agent 不需要知道底层 SDK。

## 4. Tool 与 Connector 的区别

```text
Tool
= Agent 可以调用的能力

Connector
= 与外部系统通信的实现边界
```

一个 Connector 可以提供多个 Tool。

```text
Kubernetes Connector
├── get_pod
├── get_deployment
├── get_events
└── get_logs
```

## 5. Read / Mutation 分离

强制区分：

```text
Evidence Connector
→ Read

Action Connector / Executor
→ Mutation
```

Mutation 不得因为“使用了同一个 SDK”就绕过 Policy。

## 6. 标准 Tool Contract

每个 Tool 至少应该定义：

```text
name
description
input_schema
output_schema
permission
resource_scope
risk_level
idempotency
timeout
retry_policy
rate_limit
```

## 7. 参数校验

所有参数在进入外部系统前校验：

```text
Type
Required
Enum
Range
Length
Resource format
Scope
```

例如 namespace 不允许：

```text
../../other
```

资源 ID 必须符合对应 Provider 的格式。

## 8. Scope 校验

参数合法不等于允许访问。

必须继续检查：

```text
requested resource
vs
allowed scope
```

例如：

```text
allowed = payment namespace
requested = kube-system
→ DENY
```

## 9. Authentication

Agent 不负责保存外部系统凭据。

```text
Agent
 ↓
Runtime Identity
 ↓
Credential Provider
 ↓
Connector
```

Secret 不得进入：

```text
Prompt
Context
Evidence
普通日志
```

## 10. Authorization

统一授权层：

```text
Principal
+
Permission
+
Resource Scope
+
Incident Scope
```

最终还要受到 Provider 自身权限系统约束。

## 11. Timeout

Connector 必须设置：

```text
connect timeout
request timeout
overall operation timeout
```

同时受 Incident / Agent 总体 Deadline 限制。

## 12. Retry

Retry 必须由 Connector 根据错误类型决定。

```text
Transient
→ Retry

Permanent
→ Fail
```

不能把所有异常统一 Retry。

## 13. Backoff

推荐：

```text
exponential backoff
+
jitter
+
maximum retry count
```

避免多个 Agent 同时失败后形成请求风暴。

## 14. Rate Limit

Connector 负责保护外部系统：

```text
Agent
 ↓
Rate Limiter
 ↓
External API
```

可以按：

```text
principal
provider
resource
incident
```

进行限制。

## 15. Pagination

分页必须统一处理：

```text
page_size
continuation
max_pages
max_items
```

Agent 不应该通过重复调用自行实现无限分页。

## 16. Cache

缓存策略必须声明：

```text
cacheable
TTL
stale policy
invalidation
```

动态状态不应该长时间缓存。

## 17. 数据新鲜度

所有 Evidence 至少区分：

```text
observed_at
retrieved_at
```

必要时记录：

```text
source_timestamp
```

Agent 判断时必须知道数据有多新。

## 18. Normalize

不同 Provider 最终统一成平台 Evidence：

```text
Evidence
├── id
├── type
├── source
├── resource
├── observed_at
├── retrieved_at
├── payload
├── provenance
└── confidence
```

Provider-specific 原始数据放在 payload 或外部存储中。

## 19. Provenance

Provenance 必须能够回答：

```text
数据来自哪里？
什么时候获取？
由哪个 Connector 获取？
对应哪个原始资源？
原始内容是否发生变化？
```

建议：

```text
provider
resource_id
api_version
endpoint
collector
observed_at
retrieved_at
content_hash
```

## 20. Context Projection

Connector 不应该把整个 API Response 直接塞进 LLM Context。

正确：

```text
Raw Response
 ↓
Normalize
 ↓
Relevant Fields
 ↓
Compact Projection
 ↓
Agent Context
```

Agent 需要详细数据时通过 Evidence ID 二次获取。

## 21. Error Contract

统一错误模型：

```text
AUTHENTICATION_FAILED
PERMISSION_DENIED
NOT_FOUND
INVALID_ARGUMENT
RATE_LIMITED
TIMEOUT
NETWORK_ERROR
SERVER_ERROR
STALE_DATA
PARTIAL_RESULT
UNAVAILABLE
```

每种错误应定义：

```text
retryable
user_action
agent_action
severity
```

## 22. Partial Result

任何非完整结果必须明确标记：

```text
complete=false
```

否则 Agent 可能根据不完整数据做错误 RCA。

## 23. Idempotency

Connector 必须声明 Mutation 是否幂等。

```text
idempotent=true
```

才允许在满足条件时自动 Retry。

对于非幂等操作，需要更严格的：

```text
checkpoint
approval
execution lock
verification
```

## 24. Concurrency

同一个 Incident 可能同时触发多个 Agent。

Mutation 前需要避免：

```text
Agent A → scale 5
Agent B → scale 2
```

互相覆盖。

可以通过：

```text
Action Lock
Resource Version
Optimistic Concurrency
```

降低竞态。

## 25. Audit

Connector 必须记录：

```text
request_id
incident_id
principal
tool
target
parameters_hash
provider
result
error
latency
```

敏感参数应脱敏。

## 26. Security Boundary

最重要的边界：

```text
External Data
    ↓ 不可信
Connector
    ↓
Normalized Evidence
    ↓
Agent

Agent Decision
    ↓
Policy
    ↓
Permission
    ↓
Executor
    ↓
External Mutation
```

外部数据不能反向修改 Agent 权限或 Policy。

## 27. Kubernetes / Azure / GitHub 的统一与差异

统一的是：

```text
认证
授权
超时
重试
限流
分页
Evidence
Provenance
Context Projection
Audit
```

不同的是资源模型：

```text
Kubernetes
Cluster → Namespace → Workload → Pod

Azure
Tenant → Subscription → Resource Group → Resource

GitHub
Organization → Repository → Branch → Commit / PR / Workflow
```

因此应该统一“接口和治理”，而不是强行统一 Provider 数据结构。

## 28. Connector 测试

每个 Connector 至少覆盖：

```text
正常读取
权限拒绝
资源不存在
参数错误
超时
429
5xx
分页
空结果
部分结果
过期数据
Prompt Injection
Credential failure
```

Mutation 还需要：

```text
Policy DENY
Approval Required
Approval Expired
Action Fingerprint Changed
Verification Failed
```

## 29. Replay 兼容

Connector 必须支持测试环境下的确定性 Stub / Replay。

```text
Scenario
 ↓
Recorded Connector Response
 ↓
Agent Replay
```

这样 Evaluation 不需要访问真实生产系统。

## 30. 生产验收标准

一个 Connector 达到生产 Ready 至少需要：

```text
□ 明确 Tool Contract
□ Schema 校验
□ Permission
□ Resource Scope
□ Timeout
□ Retry Policy
□ Rate Limit
□ Pagination
□ Cache Policy
□ Freshness
□ Error Contract
□ Evidence Normalization
□ Provenance
□ Context Projection
□ Audit
□ Prompt Injection 防护
□ Replay Stub
□ Read / Mutation 分离
```

## 31. 最终原则

```text
Connector 的目标不是让 Agent“能调用 API”。

而是让 Agent 能够：

安全地获取真实数据
可靠地理解数据来源
在受控权限下执行动作
并且让每一次调查和执行都可以审计、回放和验证。
```
