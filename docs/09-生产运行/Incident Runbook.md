# Incident Runbook：Agent Incident 生产处置手册

## 1. 目标

本手册描述一次真实 Incident 从发现到关闭的标准处置过程，并明确 Agent 可以做什么、不能做什么。

## 2. 标准生命周期

```text
DETECTED
 ↓
TRIAGED
 ↓
INVESTIGATING
 ↓
DIAGNOSED
 ↓
ACTION_PENDING
 ↓
APPROVED
 ↓
EXECUTING
 ↓
VERIFYING
 ↓
RESOLVED
 ↓
CLOSED
```

异常路径：

```text
FAILED
BLOCKED
ESCALATED
UNKNOWN
CANCELLED
```

## 3. Incident 创建

Incident 必须具有：

```text
incident_id
source
severity
start_time
affected_scope
trigger
initial_evidence
owner
```

## 4. Triage

先判断：

```text
影响范围
影响程度
是否持续扩大
是否存在安全风险
是否存在近期变更
```

Agent 不应在证据不足时直接执行修复。

## 5. Investigation

调查遵循：

```text
当前状态
 ↓
时间线
 ↓
关键 Evidence
 ↓
Hypothesis
 ↓
Evidence Gap
 ↓
下一步查询
```

禁止：

```text
Evidence 不足
→ 猜测原因
→ 直接 Action
```

## 6. Diagnosis

Diagnosis 应至少包含：

```text
Hypothesis
Supporting Evidence
Contradicting Evidence
Confidence
Unknowns
```

## 7. Action Planning

每个 Action 必须明确：

```text
action_id
target
operation
parameters
expected_effect
risk
blast_radius
rollback
verification
```

## 8. Approval

高风险 Action 必须：

```text
Action
 ↓
Policy
 ↓
Approval
```

Approval 必须绑定 Action Fingerprint。

如果参数、目标、范围发生变化：

```text
旧 Approval INVALID
 ↓
重新 Policy
 ↓
重新 Approval
```

## 9. Execution

Executor 执行时记录：

```text
action_id
incident_id
principal
target
start_time
end_time
result
provider_request_id
```

## 10. 执行超时

执行请求超时不等于执行失败。

必须先查询实际状态：

```text
Timeout
 ↓
Query State
 ├── Applied → Verify
 ├── Not Applied → Retry if safe
 └── Unknown → Human / Provider reconciliation
```

## 11. Verification

验证必须观察真实系统状态。

例如：

```text
Error Rate ↓
Restart Rate ↓
Latency recovered
Ready replicas restored
Business health passed
```

## 12. Verification 失败

```text
Action Success
 ↓
Verification Failed
```

不能自动宣称恢复成功。

应进入：

```text
INVESTIGATING
```

重新收集 Evidence。

## 13. Incident Escalation

以下情况必须升级人工：

```text
Evidence 冲突
关键 Tool 不可用
Policy 不确定
Action 非幂等且状态未知
Verification 不可用
影响范围扩大
安全风险
```

## 14. Major Incident

重大事故优先保证：

```text
稳定
 ↓
止损
 ↓
恢复
 ↓
完整 RCA
```

不要为了“自动化率”阻碍人工快速止血。

## 15. 关闭条件

Incident 只有在以下条件满足后才能 RESOLVED：

```text
影响恢复
Verification 成功
没有持续异常
关键指标稳定
Action 状态明确
```

## 16. CLOSED 与 RESOLVED

```text
RESOLVED
= 技术问题已恢复

CLOSED
= 证据、审计、Postmortem、后续治理已完成
```

## 17. Postmortem

事故结束后记录：

```text
Timeline
Root Cause
Contributing Factors
Evidence
Actions
Verification
Detection Gap
Automation Gap
```

## 18. Badcase 沉淀

如果 Agent 在事故中出现错误：

```text
Incident
 ↓
Trace
 ↓
Badcase
 ↓
Root Cause
 ↓
Regression Scenario
```

最终进入 Evaluation。
