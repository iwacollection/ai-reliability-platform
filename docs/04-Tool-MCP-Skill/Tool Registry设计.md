# Tool Registry 设计

## 1. 目标

Tool Registry 是 Agent Runtime 管理工具能力的统一入口。它解决的不是“保存几个 Python 函数”，而是让 Agent 知道：有什么工具、工具能做什么、需要什么参数、允许谁调用、调用风险是什么、结果如何审计。

## 2. Tool 的完整模型

一个生产 Tool 至少包含：

```text
name
version
description
input_schema
output_schema
authentication
permissions
risk_level
target_scope
timeout
retry_policy
idempotency
source
executor
audit_policy
```

## 3. Registry 与 Agent 的关系

```text
Agent
 ↓ capability discovery
Tool Registry
 ↓ policy filtered catalog
Agent
 ↓ structured Tool Call
Tool Gateway
 ↓ validation / authorization
Executor
 ↓
External System
```

Agent 不应该直接 import 外部系统 SDK 并自行调用。

## 4. Schema

参数必须通过结构化 Schema 校验：

```text
Tool: kubernetes.get_pod
Input:
  namespace: string
  pod_name: string
```

Runtime 必须拒绝缺失字段、未知字段、类型错误和非法范围。

Schema 是第一道防线，但不是权限边界。参数合法不代表用户有权操作该资源。

## 5. 参数安全

不能只依赖 Prompt 告诉模型“不要访问生产 namespace”。Runtime 应执行：

```text
Schema Validation
    ↓
Target Validation
    ↓
Permission Check
    ↓
Policy Check
    ↓
Execute
```

例如 `namespace=prod` 即使符合 Schema，也可能被当前 Agent 身份拒绝。

## 6. 权限模型

权限至少由以下维度组成：

```text
principal
agent_role
tool
operation
target
environment
risk_level
```

推荐最小权限：读工具与写工具分离；调查 Agent 与执行 Agent 分离；生产写操作默认需要 Approval。

## 7. 工具发现

Agent 不需要一次获得全部工具。Registry 应支持按能力、资源类型、权限和任务上下文过滤。

例如调查 Kubernetes CPU：只提供指标查询、Pod 查询、事件查询和日志查询，而不是把重启、删除、修改 Deployment 全部放进 Context。

## 8. Tool Call 生命周期

```text
DISCOVERED
 ↓
SELECTED
 ↓
INPUT_VALIDATED
 ↓
AUTHORIZED
 ↓
POLICY_CHECKED
 ↓
EXECUTING
 ↓
RESULT_VALIDATED
 ↓
RECORDED
```

任何一步失败都必须产生明确错误类别。

## 9. Tool Error

建议统一：

```text
INVALID_INPUT
UNAUTHORIZED
FORBIDDEN
NOT_FOUND
TIMEOUT
RATE_LIMITED
DEPENDENCY_UNAVAILABLE
EXECUTION_FAILED
RESULT_INVALID
UNKNOWN
```

错误分类决定 Retry、Fallback 还是 Stop，而不是让模型自己猜。

## 10. Tool Result

Tool 返回应尽量结构化：

```text
status
summary
data
evidence_refs
source
observed_at
warnings
error
```

原始结果与摘要分开。摘要可以进入 Context，原始结果保留在 Evidence Store。

## 11. Audit

每次调用至少记录：

- tool_call_id
- run_id
- incident_id
- actor
- tool
- 输入摘要
- 目标资源
- authorization result
- policy result
- start/end time
- result status
- evidence refs

敏感参数必须脱敏。

## 12. 幂等性

Tool Registry 必须声明 Tool 是否幂等。非幂等 Tool 默认禁止自动 Retry。

## 13. Tool 与 Skill 的边界

Tool 是“能力原语”；Skill 是“如何使用能力解决一类任务”。例如：

```text
Tool: get_pod
Tool: get_logs
Tool: get_metrics
        ↓
Skill: Kubernetes CPU Investigation
```

## 14. 验收标准

- [ ] 所有 Tool 有 Schema。
- [ ] Tool 调用经过 Runtime 校验。
- [ ] Tool 权限不依赖 Prompt。
- [ ] Tool 风险等级可声明。
- [ ] Tool Error 有统一分类。
- [ ] Tool Result 可转换为 Evidence。
- [ ] 调用有 Audit。
- [ ] 非幂等 Tool 不允许无条件 Retry。
