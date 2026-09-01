# Permission Model：Agent 身份、权限与资源范围设计

## 1. 核心问题

Agent 能不能执行一个动作，不应该由 Prompt 决定，而应该由确定性的身份与授权系统决定。

```text
Agent Identity
 ↓
Role
 ↓
Permission
 ↓
Resource Scope
 ↓
Policy
 ↓
Action
```

## 2. Principal

Principal 是发起操作的身份，可以是：

- investigation-agent
- remediation-agent
- human operator
- scheduled automation

每次 Action 都必须能够追溯到 Principal。

## 3. 最小权限

调查 Agent 通常只需要：

```text
metrics.read
logs.read
k8s.pod.read
k8s.deployment.read
```

修复 Agent 才可能拥有：

```text
k8s.deployment.restart
k8s.deployment.scale
```

不要让所有 Agent 共享一个全能凭据。

## 4. 权限三元组

建议把授权理解成：

```text
Who
What
Where
```

例如：

```text
Who   = remediation-agent
What  = deployment.restart
Where = namespace/payment
```

缺任何一个维度都不能认为授权完整。

## 5. 资源 Scope

Scope 可以按层级限制：

```text
环境
 ↓
集群
 ↓
Namespace
 ↓
Workload
 ↓
具体资源
```

越靠近具体资源，爆炸半径越小。

## 6. RBAC 与 Agent 权限的关系

Agent Runtime 的权限模型与 Kubernetes / 云平台 RBAC 不应混为一谈。

推荐：

```text
Agent Permission
     ↓
Runtime Authorization
     ↓
External Credential
     ↓
Kubernetes / Cloud RBAC
```

上层限制可以比底层 RBAC 更严格。

## 7. 为什么不能只依赖底层 RBAC

底层 RBAC 可能允许：

```text
restart deployment
```

但当前 Incident 可能只授权：

```text
payment-api
```

如果 Agent 使用同一个身份去操作：

```text
order-api
inventory-api
```

就可能越过 Incident 边界。

因此必须同时检查：

```text
平台权限
+
Incident Scope
```

## 8. 临时授权

高风险动作可以使用短生命周期授权：

```text
Request
 ↓
Approval
 ↓
Issue temporary credential
 ↓
Execute
 ↓
Revoke / Expire
```

不要让 Agent 长期持有生产高权限 Token。

## 9. 权限拒绝

Permission Denied 不应该自动 Retry。

因为 Retry 不会改变授权事实。

```text
PERMISSION_DENIED
 ↓
BLOCKED
 ↓
解释缺失权限
 ↓
人工处理 / 重新授权
```

## 10. 审计

每次授权判断至少记录：

```text
principal
permission
resource
incident_id
action_id
decision
policy_version
time
```

## 11. 生产原则

```text
默认拒绝
最小权限
最小 Scope
短生命周期凭据
权限与 Incident 绑定
高风险操作二次授权
所有决策可审计
```
