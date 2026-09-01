# Kubernetes Evidence Adapter：Kubernetes 真实证据接入规范

## 1. 定位

Kubernetes Evidence Adapter 不是简单封装 `get_pods()`。它负责把 Kubernetes API、事件、Pod 状态、OwnerReference、Deployment、ReplicaSet、日志和资源指标转换成 Agent 可以可靠使用的结构化 Evidence。

核心链路：

```text
Kubernetes API
 ↓
Kubernetes Adapter
 ↓
Authentication
 ↓
Authorization / RBAC
 ↓
Query
 ↓
Pagination / Timeout
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

## 2. 为什么需要 Adapter

如果 Agent 直接操作 Kubernetes SDK，会产生几个问题：

- 每个 Agent 自己处理认证
- 每个 Agent 自己处理权限
- API 返回结构直接进入 Context
- 不同 Agent 对同一资源产生不同解释
- Tool Error 无统一分类
- 无法统一记录 Evidence 来源
- 很难做 Replay

因此 Agent 应看到稳定的平台能力，而不是 Kubernetes SDK 细节。

## 3. 资源模型

建议标准化：

```text
Cluster
Namespace
Workload
Pod
Container
Node
Service
Event
Deployment
ReplicaSet
```

Pod 的 OwnerReference 应用于建立：

```text
Deployment
 ↓
ReplicaSet
 ↓
Pod
 ↓
Container
```

这样 Agent 才能从单个 Pod 异常追溯到上层 Workload。

## 4. Evidence 类型

例如：

```text
K8S_POD_STATE
K8S_CONTAINER_STATE
K8S_EVENT
K8S_DEPLOYMENT_STATE
K8S_REPLICASET_STATE
K8S_RESOURCE_USAGE
K8S_LOG
K8S_OWNER_RELATION
```

## 5. Pod Evidence

至少包含：

```text
metadata.name
metadata.namespace
labels
annotations
ownerReferences
creationTimestamp
status.phase
conditions
containerStatuses
restartCount
reason
message
nodeName
podIP
```

不能只返回：

```text
Pod = Running
```

因为 Running 并不代表业务健康。

## 6. Container Evidence

重点保留：

```text
state
lastState
reason
message
restartCount
ready
started
image
imageID
```

例如：

```text
lastState.terminated.reason = OOMKilled
restartCount = 7
```

这比单纯“Pod 异常”具有更高诊断价值。

## 7. Event Evidence

Kubernetes Event 必须保留：

```text
type
reason
message
involvedObject
firstTimestamp
lastTimestamp
count
source
```

尤其需要保留时间信息，因为 Incident Timeline 依赖它判断事件先后。

## 8. Deployment Evidence

建议获取：

```text
spec.replicas
status.replicas
status.updatedReplicas
status.readyReplicas
status.availableReplicas
observedGeneration
conditions
strategy
revision
```

用于判断：

```text
发布是否完成
滚动更新是否卡住
Ready Pod 是否不足
```

## 9. 查询边界

Connector 不应该提供一个无限制的：

```text
list_everything()
```

应该要求：

```text
cluster
namespace
resource
label_selector
field_selector
limit
continue_token
```

并且设置服务端允许的最大范围。

## 10. 分页

Kubernetes List API 可能返回 Continue Token。

Adapter 必须支持：

```text
request(limit)
 ↓
items + continue
 ↓
next request
```

但 Agent 不应该无限分页。需要：

```text
max_pages
max_items
max_duration
```

## 11. Timeout

每一次 Kubernetes API 请求必须有超时。

建议区分：

```text
connect timeout
request timeout
overall investigation deadline
```

Tool 超时后应该返回结构化错误，而不是让 Agent 等待无限时间。

## 12. Retry

只对适合重试的错误执行 Retry：

```text
network transient error
429
部分 5xx
```

不要对：

```text
403
404
invalid parameter
permission denied
```

盲目重试。

## 13. 写操作

Kubernetes Evidence Adapter 默认应该偏向只读。

写操作应该进入独立 Action Tool：

```text
Evidence Adapter
→ read

Action Executor
→ mutation
```

例如：

```text
get_pod
get_deployment
get_events
```

与：

```text
restart_deployment
scale_deployment
patch_deployment
```

严格分离。

## 14. Authentication

Adapter 不应该让 Agent 提供 Kubernetes Token。

推荐：

```text
Agent
 ↓
Runtime
 ↓
Credential Provider
 ↓
Kubernetes Client
```

凭据由运行环境或受控 Secret 管理。

## 15. Authorization

必须同时考虑：

```text
Kubernetes RBAC
+
Agent Runtime Permission
+
Incident Scope
```

Kubernetes 允许访问，不代表当前 Agent 就应该访问。

## 16. Evidence Normalization

Kubernetes 原始 API 对象不应该直接进入模型 Context。

建议转换为：

```text
Evidence {
  id
  type
  source
  resource
  timestamp
  observed_at
  payload
  confidence
  provenance
}
```

原始对象可以存储，但 Context 使用压缩后的 Projection。

## 17. Provenance

每条 Evidence 至少记录：

```text
source = kubernetes
api_group
api_version
resource_kind
namespace
resource_name
collector
observed_at
retrieved_at
content_hash
```

这样可以回答：

> 这个判断到底来自哪个 Kubernetes 对象？

## 18. 数据新鲜度

Kubernetes 状态变化很快。

必须区分：

```text
observed_at
retrieved_at
```

例如：

```text
Pod 状态在 12:00:00 被观察到
12:00:10 才进入 Agent Context
```

不能让 Agent 误以为数据是 12:00:10 的实时状态。

## 19. Cache

可以缓存：

```text
静态 Metadata
Owner Graph
历史 Event
```

谨慎缓存：

```text
Pod 状态
Ready 状态
资源使用率
```

写操作前必须重新读取目标状态。

## 20. 日志

日志获取需要限制：

```text
namespace
pod
container
since_seconds
tail_lines
limit_bytes
```

不能默认把完整历史日志塞给 Agent。

推荐：

```text
tail / window
 ↓
error pattern extraction
 ↓
relevant Evidence
```

原始日志保留引用。

## 21. Error Model

统一错误分类：

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
```

错误类型直接影响 Agent 后续策略。

例如：

```text
TIMEOUT
→ limited retry

PERMISSION_DENIED
→ no retry
→ escalate

STALE_DATA
→ refresh evidence
```

## 22. Partial Result

分页、超时或 API 异常可能导致只拿到部分数据。

Adapter 必须明确：

```text
complete = false
```

不能把部分 Evidence 包装成完整事实。

## 23. Agent Context Projection

Agent 不需要完整 Kubernetes 对象。

例如将：

```text
50KB Pod JSON
```

投影为：

```text
payment-api-7d8f
namespace=payment
phase=Running
ready=1/2
restartCount=7
lastTermination=OOMKilled
owner=deployment/payment-api
observed_at=...
```

必要时再通过 Evidence ID 获取原始内容。

## 24. 生产安全原则

```text
Agent 不持有 Kubernetes Token
Agent 不直接操作 Kubernetes SDK
Read 与 Mutation 分离
所有 Mutation 进入 Policy Gate
所有 Evidence 带 Provenance
所有查询有限制
所有数据有时间戳
所有错误结构化
```
