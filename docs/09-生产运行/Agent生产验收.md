# Agent 生产验收：从能运行到 Production Ready

## 1. 验收目标

Agent 生产验收不是检查：

```text
模型能回答问题
```

而是证明：

```text
Agent 能在受控权限下
稳定调查 Incident
正确使用 Evidence
正确调用 Tool
遵守 Policy
正确处理 Approval
安全执行 Action
可靠完成 Verification
完整留下 Audit
```

## 2. 验收分层

```text
代码验收
 ↓
单元测试
 ↓
集成测试
 ↓
Scenario Replay
 ↓
安全测试
 ↓
故障注入
 ↓
Canary
 ↓
生产验收
```

## 3. 功能验收

必须验证：

```text
Alert → Incident
Incident → Agent
Agent → Evidence
Evidence → Decision
Decision → Tool
Action → Policy
Policy → Approval
Approval → Executor
Executor → Verification
```

## 4. Evidence 验收

检查：

```text
Evidence 是否真实
来源是否明确
时间是否正确
是否可追溯
是否存在伪造
是否标记不完整数据
```

## 5. Tool 验收

每个 Tool 检查：

```text
Schema
参数校验
Permission
Scope
Timeout
Retry
Rate Limit
Error Contract
Audit
```

## 6. MCP 验收

至少测试：

```text
Tool Discovery
Schema
Connection Failure
Timeout
Server Error
Permission
Malformed Response
Context Isolation
```

## 7. Policy 验收

必须证明：

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

三条路径都正确。

尤其验证：

```text
Policy 服务异常
→ Mutation FAIL CLOSED
```

## 8. Approval 验收

测试：

```text
未审批 → 不执行
审批过期 → 不执行
审批被拒 → 不执行
Fingerprint 改变 → 不执行
正确审批 → 才能执行
```

## 9. Permission 验收

需要验证：

```text
合法 Tool
合法 Resource
合法 Environment
```

以及：

```text
越权 Resource
越权 Operation
越权 Environment
```

全部必须拒绝。

## 10. Action 验收

检查：

```text
Target
Parameters
Risk
Blast Radius
Idempotency
Rollback
Verification
```

## 11. Verification 验收

必须证明：

```text
Action executed
≠
Recovery confirmed
```

至少包含：

```text
Positive verification
Negative verification
Timeout
Conflicting evidence
```

## 12. Failure Injection

必须主动模拟：

```text
Model timeout
Model 429
Model 5xx
Tool timeout
Tool 403
Tool 404
MCP unavailable
Policy unavailable
Approval unavailable
Executor timeout
Verification unavailable
Evidence Store unavailable
```

## 13. Agent Loop 验收

测试：

```text
max iterations
max runtime
max tool calls
repeated tool calls
no progress
context overflow
```

预期：

```text
STOP
SAVE TRACE
ESCALATE
```

## 14. 安全验收

测试：

```text
Prompt Injection
Malicious Tool Output
Malicious PR
Malicious Issue
Credential Leakage
Privilege Escalation
Policy Bypass
Approval Bypass
```

## 15. 数据安全

确认 Secret 不进入：

```text
Prompt
Context
Evidence
普通日志
Trace
```

必要数据必须脱敏。

## 16. Evaluation 验收

使用固定 Scenario Dataset 运行：

```text
Baseline Agent
vs
Candidate Agent
```

比较：

```text
Recovery Rate
Wrong Diagnosis Rate
Wrong Action Rate
Policy Violation Rate
Verification Failure Rate
Tool Calls
Latency
Cost
```

## 17. Regression 验收

所有历史重大 Badcase 必须 Replay。

```text
Historical Incident
 ↓
Regression Scenario
 ↓
Candidate Agent
 ↓
Replay
 ↓
Pass / Fail
```

## 18. Canary 验收

上线初期限制 Incident 范围：

```text
低风险
低影响
明确可回滚
```

观察：

```text
错误率
安全违规
自动化成功率
人工升级率
成本
延迟
```

## 19. 生产准入 Gate

推荐：

```text
Unit Test       PASS
Integration     PASS
Replay          PASS
Security        PASS
Regression      PASS
Canary          PASS
Audit           PASS
Rollback        PASS
```

任意关键安全项失败：

```text
NO-GO
```

## 20. Go / No-Go

### GO

```text
□ 核心功能正常
□ Scenario Replay 达标
□ 历史 Badcase 无关键回归
□ 无 Policy Bypass
□ 无 Permission Bypass
□ Approval 正常
□ Verification 正常
□ 故障降级有效
□ Audit 完整
□ Rollback 验证通过
```

### NO-GO

出现以下任意情况：

```text
未授权 Mutation
Policy Bypass
Approval Bypass
无法验证 Action
Evidence 伪造
Credential 泄漏
严重 Regression
无法回滚
```

## 21. 上线后验收

部署完成后执行 Synthetic Incident：

```text
Trigger
 ↓
Incident
 ↓
Investigation
 ↓
Evidence
 ↓
Decision
 ↓
Policy
 ↓
Approval
 ↓
Action（安全测试目标）
 ↓
Verification
 ↓
Audit
```

只有完整闭环通过，才认为部署成功。

## 22. 最终生产标准

Agent Production Ready 的定义：

```text
不是“它很聪明”。

而是：

它知道什么时候调查；
它知道需要什么证据；
它知道什么时候不能判断；
它知道什么时候不能执行；
它知道什么时候必须请求人工；
它知道 Action 是否真的生效；
并且整个过程都可以审计、回放和验证。
```
