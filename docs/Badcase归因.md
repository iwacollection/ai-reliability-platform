# Agent Badcase 归因：从失败结果定位真正根因

## 1. 为什么需要 Badcase 归因

只记录“Agent 失败”没有工程价值。

必须回答：

```text
失败发生在哪里？
为什么发生？
是模型问题、Evidence 问题、Tool 问题还是 Runtime 问题？
应该修改什么？
如何证明修复没有引入回归？
```

## 2. 统一失败链

```text
Scenario
 ↓
Trajectory
 ↓
Failure
 ↓
Failure Localization
 ↓
Root Cause
 ↓
Fix
 ↓
Regression Case
 ↓
Replay
```

## 3. 第一层：结果分类

建议先区分：

```text
SUCCESS
SAFE_ABSTENTION
WRONG_DIAGNOSIS
WRONG_ACTION
VERIFICATION_FAILURE
SAFETY_VIOLATION
TOOL_FAILURE_UNHANDLED
LOOP / NO_PROGRESS
TIMEOUT
INFRASTRUCTURE_FAILURE
```

## 4. 第二层：故障域

### Evidence Failure

```text
没有获取关键证据
证据错误
证据过期
证据冲突未处理
```

### Reasoning Failure

```text
错误假设
忽略反证
因果关系判断错误
过早收敛
```

### Tool Failure

```text
错误 Tool
错误参数
重复调用
没有处理 Timeout
没有处理 PermissionDenied
```

### Runtime Failure

```text
Context 丢失
状态机错误
Checkpoint 恢复错误
预算控制失效
```

### Safety Failure

```text
绕过 Policy
绕过 Approval
越权
错误 Scope
危险 Action
```

### Verification Failure

```text
没有验证
验证指标错误
把 API success 当 Recovery
错误判断恢复
```

## 5. 第三层：根因定位

不要直接把“模型幻觉”当根因。

例如：

```text
Agent 错误重启 payment-api
```

需要继续向下追：

```text
为什么重启？
 ↓
错误 Diagnosis
 ↓
为什么 Diagnosis 错？
 ↓
缺少数据库连接指标
 ↓
为什么没拿到？
 ↓
Tool Registry 没暴露该指标
```

最终根因可能是 Tool Capability Gap，而不是 LLM 本身。

## 6. Badcase 标签

建议支持多标签：

```text
EVIDENCE_MISSING
EVIDENCE_MISREAD
HYPOTHESIS_PREMATURE
WRONG_TOOL
BAD_ARGUMENT
TOOL_RETRY_ERROR
CONTEXT_LOSS
MEMORY_ERROR
POLICY_ERROR
PERMISSION_ERROR
APPROVAL_ERROR
ACTION_SELECTION_ERROR
VERIFICATION_ERROR
```

## 7. Badcase 严重等级

```text
P0：安全违规 / 生产破坏
P1：错误 Action / 错误恢复
P2：错误 Diagnosis / 无效调查
P3：效率问题
P4：表达或可观测性问题
```

P0/P1 应优先进入 Regression Suite。

## 8. Trajectory 分析

不能只看最终消息。

需要分析：

```text
Agent Turn 1
Tool Call
Tool Result
Decision

Agent Turn 2
Tool Call
Tool Result
Decision

...
```

这样才能定位第一次偏离正确轨迹的位置。

## 9. 第一个错误原则

很多失败轨迹后面会出现大量连锁错误。

例如：

```text
错误 Evidence
 ↓
错误 Diagnosis
 ↓
错误 Action
 ↓
Verification Failure
```

真正根因可能只是第一步 Evidence Selection 错误。

因此分析时优先找：

> First Divergence：第一次偏离预期行为的节点。

## 10. Fix 分类

Badcase 修复应该明确属于：

```text
Prompt Fix
Model Fix
Tool Contract Fix
Evidence Adapter Fix
Runtime Fix
Policy Fix
Permission Fix
Evaluation Fix
Scenario Fix
```

避免所有问题最后都变成“改 Prompt”。

## 11. 修复验证

任何 P0/P1 Badcase 修复后必须：

```text
Replay 原 Scenario
 ↓
通过原 Assertion
 ↓
运行完整 Regression Suite
 ↓
检查 Safety
 ↓
比较其他指标
```

## 12. Badcase 生命周期

```text
Detected
 ↓
Triaged
 ↓
Root Cause Identified
 ↓
Fixed
 ↓
Regression Added
 ↓
Verified
 ↓
Closed
```

## 13. Badcase 数据结构

建议保存：

```text
badcase_id
scenario_id
run_id
agent_version
failure_type
severity
first_divergence
root_cause
fix_type
regression_case
status
```

## 14. 最终目标

Badcase 不应该成为失败日志仓库，而应该成为 Agent 能力成长的数据集：

```text
真实失败
 ↓
结构化归因
 ↓
可重复 Scenario
 ↓
Regression
 ↓
新版本
 ↓
验证修复
```
