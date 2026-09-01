# Azure Connector：Azure 真实资源与证据接入规范

## 1. 定位

Azure Connector 负责把 Azure Resource Manager、监控、日志和身份授权能力封装成 Agent 可安全消费的结构化数据。它不是让 Agent 直接获得 Azure SDK 或订阅级管理员权限。

```text
Agent
 ↓
Tool / Connector Interface
 ↓
Azure Connector
 ↓
Credential Provider
 ↓
Azure Authorization
 ↓
Azure API / Monitor / Resource Graph
 ↓
Normalize
 ↓
Evidence + Provenance
 ↓
Context Projection
 ↓
Agent
```

## 2. 为什么 Azure 需要独立 Connector

Azure 的资源层级与 Kubernetes 不同：

```text
Tenant
 ↓
Subscription
 ↓
Resource Group
 ↓
Resource
```

此外还可能存在：

```text
Managed Identity
RBAC
Azure Monitor
Log Analytics
Resource Graph
```

Connector 必须隐藏这些平台细节，同时保留足够的来源信息。

## 3. 认证

推荐使用工作负载身份 / Managed Identity 等受控方式获取凭据，而不是把长期 Client Secret 放入 Agent Context。

```text
Agent
 ↓
Runtime Identity
 ↓
Credential Provider
 ↓
Azure SDK
```

Secret 不得进入 Prompt、Evidence 或普通日志。

## 4. 授权

至少需要同时判断：

```text
Azure RBAC
+
Runtime Permission
+
Incident Scope
+
Policy
```

例如 Agent 可以读取某 Subscription，并不意味着它可以修改该 Subscription 下所有资源。

## 5. Resource Scope

建议限制：

```text
subscription
resource_group
resource_id
resource_type
```

生产 Action 尽可能绑定具体 Resource ID。

## 6. Read 与 Mutation

读取与修改必须分开：

```text
Azure Evidence Connector
→ resource.read
→ metric.read
→ log.read
```

```text
Azure Action Executor
→ restart
→ scale
→ update
```

Mutation 必须进入统一 Policy Gate。

## 7. Azure Resource Graph

Resource Graph 适合查询跨资源的资源元数据，例如：

```text
资源类型
资源位置
资源组
资源标签
资源状态
```

Connector 应限制查询范围和结果数量，避免 Agent 发起无限制全租户扫描。

## 8. Azure Monitor

Monitor 数据需要记录：

```text
metric_name
resource_id
time_range
aggregation
interval
observed_at
```

Agent 必须知道查询的是哪个时间窗口。

## 9. Log Analytics

日志查询需要限制：

```text
workspace
query
start_time
end_time
max_rows
max_bytes
```

不能默认允许 Agent 执行无限范围的 KQL 查询。

## 10. 分页与结果限制

Connector 应统一提供：

```text
page_size
continuation_token
max_pages
max_items
max_duration
```

超过限制应返回：

```text
PARTIAL_RESULT
```

不能伪装成完整查询结果。

## 11. Timeout

必须区分：

```text
HTTP timeout
SDK timeout
query timeout
investigation deadline
```

例如 Log Analytics 查询超时不能阻塞整个 Incident。

## 12. Retry

适合 Retry：

```text
429
transient network failure
部分 5xx
```

不应重试：

```text
403
invalid query
invalid resource
authentication failure
```

## 13. Rate Limit

Azure API 和监控查询都有服务端限制。

Connector 应：

```text
识别 429
 ↓
读取 Retry-After
 ↓
Backoff
 ↓
限制 Agent 后续调用
```

不能让 Agent 自己疯狂 Retry。

## 14. Normalize

Azure API 的资源模型很多，最终统一转换成：

```text
Evidence {
  id
  type
  source
  resource
  observed_at
  retrieved_at
  payload
  provenance
}
```

例如：

```text
AZURE_RESOURCE_STATE
AZURE_METRIC
AZURE_LOG
AZURE_DEPLOYMENT
AZURE_NETWORK_STATE
```

## 15. Provenance

至少保存：

```text
provider = azure
subscription_id
resource_group
resource_id
api_version
query
collector
observed_at
retrieved_at
content_hash
```

敏感凭据绝不能进入 Provenance。

## 16. 数据新鲜度

Azure Monitor 与 Resource Graph 数据的时间语义不同。

必须保留：

```text
metric timestamp
query time
retrieval time
```

Agent 不应把“查询时间”误认为“指标发生时间”。

## 17. Cache

适合缓存：

```text
Resource metadata
Resource type
Tags
```

谨慎缓存：

```text
metrics
health state
deployment state
```

执行 Mutation 前必须重新确认目标状态。

## 18. Error Model

统一错误：

```text
AUTHENTICATION_FAILED
PERMISSION_DENIED
NOT_FOUND
INVALID_ARGUMENT
QUERY_INVALID
RATE_LIMITED
TIMEOUT
NETWORK_ERROR
SERVER_ERROR
STALE_DATA
PARTIAL_RESULT
```

错误分类应直接映射 Agent 行为。

## 19. Azure 与 Kubernetes 的差异

Kubernetes 重点是：

```text
Cluster / Namespace / Workload / Pod
```

Azure 重点是：

```text
Tenant / Subscription / Resource Group / Resource
```

因此两者可以共享 Evidence 模型，但不能强行共享底层资源模型。

## 20. Agent Context Projection

例如 Azure Resource 原始 JSON 很大，Context 可以只提供：

```text
resource_id
resource_type
resource_group
location
state
relevant_metrics
recent_changes
observed_at
```

原始结果保留在 Evidence Store，通过 Evidence ID 按需获取。

## 21. 安全边界

```text
Agent 不持有 Azure 长期 Secret
Agent 不直接调用 Azure SDK
Connector 限制查询范围
Mutation 与 Evidence Read 分离
Mutation 必须经过 Policy
所有 Evidence 有 Provenance
```
