# Policy Gate：Agent 生产动作策略门设计

> Policy Gate 是 Agent 从“提出动作”进入“允许执行”之间的确定性安全边界。它不负责判断根因，也不负责执行动作，而是根据 Incident、Action、身份、目标、环境、风险和当前系统状态，决定该动作是否可以继续。

## 1. 为什么需要 Policy Gate

LLM 可以提出一个合理的修复方案，但模型输出本身不能成为生产授权。

```text
Agent：建议重启 payment-api
        ↓
Policy Gate：当前是否允许？
        ├── deny
        ├── approval_required
        └── allow
```

核心原则：

```text
模型负责“建议什么”
Policy 负责“允许不允许”
Executor 负责“怎么执行”
```

## 2. Policy 输入

一次策略判断至少需要：

```text
principal
incident
action
resource
environment
risk
current_state
permissions
approval_context
policy_version
```

例如：

```text
principal = agent-remediator
environment = production
resource = payment/deployment/payment-api
action = restart
risk = high
incident_state = PLAN_READY
```

## 3. Policy 输出

不要只返回 true / false。建议结构化为：

```text
decision
reason_codes
required_approval
required_permissions
policy_version
constraints
expires_at
```

典型结果：

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

## 4. 决策顺序

推荐固定顺序：

```text
身份认证
 ↓
Action Schema 校验
 ↓
目标资源校验
 ↓
权限校验
 ↓
环境校验
 ↓
Incident 状态校验
 ↓
风险计算
 ↓
Policy Rule
 ↓
Approval
 ↓
执行约束
```

Policy 不应该因为 LLM 说“这是紧急情况”就跳过安全检查。

## 5. 典型策略

### 只读调查

```text
metrics.read
logs.read
k8s.pod.read
```

通常可以自动执行。

### 测试环境写操作

可以根据风险允许自动执行，但仍需要：

- 目标限制
- 参数限制
- 时间限制
- 审计

### 生产写操作

默认进入审批或更严格的策略。

### 高爆炸半径操作

例如：

```text
delete namespace
restart all workloads
change global routing
```

可以直接 DENY，或者要求多方审批。

## 6. Policy 不相信模型声明的 Risk

错误：

```text
LLM：risk = low
Policy：允许
```

正确：

```text
Action
 + target
 + environment
 + blast_radius
 + reversibility
 + permission
        ↓
Runtime 重新计算风险
        ↓
Policy
```

## 7. 资源范围

权限必须与资源范围同时判断。

例如：

```text
permission = k8s.deployment.restart
scope = namespace/payment
```

Agent 请求：

```text
namespace/order
```

即使拥有 restart 权限，也应该拒绝。

## 8. Policy 与 Approval 的边界

Policy 决定：

```text
是否允许自动执行？
是否必须审批？
是否明确禁止？
```

Approval 决定：

```text
人工是否批准这一个具体 Action？
```

所以：

```text
Policy DENY
→ 人工不能通过点击绕过

Policy REQUIRE_APPROVAL
→ 才进入审批
```

## 9. Action 版本绑定

审批和 Policy Decision 必须绑定 Action Fingerprint。

```text
action_v1
 ↓
policy decision
 ↓
approval
```

如果 target、parameters、权限或执行策略发生变化：

```text
action_v2
 ↓
重新 Policy
 ↓
重新 Approval
```

不能复用旧审批。

## 10. 时间有效性

Policy Decision 和 Approval 不应该永久有效。

例如：

```text
approval_expires_at
policy_expires_at
```

如果 Incident 已经发生变化，旧批准可能失效。

## 11. Policy 失败策略

Policy 服务不可用时，生产写操作推荐：

```text
fail closed
```

即：

```text
无法判断是否允许
→ 不执行生产写操作
→ BLOCKED / ESCALATED
```

只读调查可以根据风险设置有限降级策略。

## 12. Policy Decision Audit

每次策略判断必须记录：

```text
incident_id
action_id
principal
target
input_hash
decision
reason_codes
policy_version
created_at
```

这样事后可以回答：

> 当时为什么允许这个动作？

## 13. 与 Rego 等策略引擎的关系

Policy 规则可以使用确定性策略引擎实现，例如 Open Policy Agent / Rego。

推荐架构：

```text
Agent Runtime
     ↓
Policy Adapter
     ↓
Policy Engine
     ↓
Decision
```

Agent 不应该直接执行 Rego，也不应该决定 Policy 输入中的权限字段。

## 14. 生产验收

至少验证：

```text
无权限 → DENY

错误 namespace → DENY

生产高风险 → REQUIRE_APPROVAL

Policy 服务不可用 → 写操作 fail closed

Action 修改后 → 旧 approval 失效

Approval 过期 → 不允许执行

同一 Action 重试 → 不产生绕过 Policy 的路径
```

## 15. 最终原则

```text
Policy Gate 不是 Prompt。
Policy Gate 不是 LLM。
Policy Gate 不是 Approval UI。
Policy Gate 是确定性的生产安全边界。
```
