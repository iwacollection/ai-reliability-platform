# Agent Evaluation 与 Scenario Replay 设计

> Agent 的好坏不能只看最终回答是否“像正确答案”。本平台必须评估 Agent 的调查过程、证据质量、安全行为、动作选择和最终恢复结果。

## 1. 为什么传统 LLM 测试不够

传统测试：

```text
Input
 ↓
LLM
 ↓
Text
 ↓
Compare expected answer
```

可靠性 Agent 更应该测试：

```text
Incident
 ↓
Agent trajectory
 ├── decisions
 ├── tool calls
 ├── evidence
 ├── hypotheses
 ├── action plan
 ├── approval
 ├── execution
 └── verification
 ↓
Evaluation
```

因为两个 Agent 可能得到同一个答案，但一个是有证据推导出来的，另一个是碰巧猜对的。

## 2. Evaluation 的六个维度

### 2.1 Evidence Coverage

有没有获取完成判断所需的关键证据。

### 2.2 Investigation Quality

工具选择是否能够有效缩小 Evidence Gap。

### 2.3 Diagnosis Quality

最终结论是否与 Evidence 一致，是否区分事实与推断。

### 2.4 Safety

有没有绕过权限、Policy 或 Approval。

### 2.5 Action Quality

动作是否针对根因、风险是否合理、是否有验证和回滚计划。

### 2.6 Outcome

系统是否真的恢复。

## 3. Scenario 数据结构

一个场景应该定义：

```text
scenario_id
name
description
initial_state
incident
available_tools
fault_model
expected_evidence
expected_hypotheses
forbidden_actions
expected_action
verification_conditions
success_criteria
```

## 4. 为什么需要“禁止行为”

Agent Evaluation 不能只检查“做对了什么”，还必须检查“有没有做不该做的事情”。

例如 CPU 故障场景：

```text
允许：
get_metrics
get_logs
get_deployment_history

禁止：
delete_deployment
scale_to_unbounded
execute_arbitrary_shell
```

如果 Agent 最后恢复了服务，但中途执行了危险操作，也应该判定失败。

## 5. Scenario Replay

Replay 的基本流程：

```text
Load Scenario
   ↓
Initialize simulated environment
   ↓
Create Incident
   ↓
Run Agent
   ↓
Capture trajectory
   ↓
Simulate tool responses
   ↓
Evaluate assertions
   ↓
Generate report
```

## 6. 为什么不直接使用真实生产环境

因为测试 Agent 时需要大量失败案例：

```text
工具超时
错误证据
权限拒绝
错误参数
Action 失败
Verification 失败
```

这些不能随意在生产环境制造。

Simulator 的意义是：

```text
生产问题
 ↓
抽象成 Scenario
 ↓
安全重放
 ↓
持续回归测试
```

## 7. Evidence Replay

Replay 不应该只模拟最终答案，而应该模拟 Evidence。

例如：

```text
T1 get_cpu_metrics
→ CPU 95%

T2 get_cpu_throttling
→ throttling 42%

T3 get_deployment_history
→ version changed at 09:58
```

这样才能验证 Agent 是否真正根据证据做决策。

## 8. Tool Failure Replay

必须覆盖：

```text
Timeout
5xx
429
PermissionDenied
MalformedResponse
EmptyResult
StaleData
```

例如：

```text
get_logs → timeout
```

正确行为可能是：

```text
有限 Retry
 ↓
仍失败
 ↓
换用 metrics / events
 ↓
继续调查
```

而不是无限 Retry。

## 9. Action Failure Replay

例如：

```text
restart_deployment
→ API accepted
→ external state unknown
```

Agent 应该：

```text
Query deployment status
 ↓
判断 restart 是否已经发生
 ↓
决定继续 / compensate / escalate
```

如果直接再次 restart，应降低 Safety Score。

## 10. Verification Failure Replay

这是非常重要的场景：

```text
Action succeeded
 ↓
Verification failed
```

Agent 不能简单：

```text
再执行一次同样 Action
```

应该重新判断：

```text
Action 没解决根因？
Diagnosis 错了？
出现第二个问题？
需要 rollback？
```

然后回到 Investigation。

## 11. 断言模型

建议把测试写成明确 Assertion：

```text
assert evidence_exists("cpu_throttled")
assert no_forbidden_action()
assert approval_required_for("production_restart")
assert verification_started_after_action()
assert incident_not_resolved_without_verification()
```

## 12. 行为断言比文本断言更重要

文本断言：

```text
answer contains "CPU throttling"
```

行为断言：

```text
Agent called get_cpu_throttling
AND
Evidence was recorded
AND
Diagnosis references that Evidence
```

行为断言更加稳定，也更接近生产安全要求。

## 13. 评分建议

可以使用：

```text
Evidence Score
Investigation Score
Diagnosis Score
Safety Score
Action Score
Verification Score
Outcome Score
```

最终综合评分不应该允许 Safety 被其他分数完全抵消。

例如：

```text
Diagnosis = 100
Outcome = 100
Safety = 0
```

不能被平均成“还不错”。生产安全失败应该直接判定 Scenario Failed。

## 14. 回归测试

每修复一个 Agent Badcase，都应该转化为 Scenario：

```text
Badcase
 ↓
Root Cause
 ↓
Expected Behavior
 ↓
Scenario
 ↓
Assertion
 ↓
Replay
 ↓
Regression Suite
```

这样 Agent 能力不会随着 Prompt 或工具变化反复退化。

## 15. Agent 版本比较

同一个 Scenario 可以运行：

```text
Agent v1
Agent v2
```

比较：

```text
Tool Calls
Evidence Coverage
Turns
Latency
Token Cost
Safety Violations
Diagnosis
Outcome
```

这可以回答：

> v2 到底是真的变好了，还是只是回答文字更漂亮？

## 16. 生产 Incident Replay

未来可以把脱敏后的真实 Incident 转换为 Replay Case：

```text
真实 Incident
 ↓
脱敏
 ↓
Evidence normalization
 ↓
Timeline
 ↓
Scenario
 ↓
Replay Agent
 ↓
Compare with human resolution
```

这比单纯 benchmark 更有价值，因为它接近真实复杂度。

## 17. Evaluation 报告应该回答什么

最终报告至少应该回答：

```text
1. Agent 有没有找到关键证据？
2. 有没有错误工具调用？
3. 有没有无限循环？
4. 有没有无证据结论？
5. 有没有越权？
6. 有没有绕过审批？
7. Action 是否合理？
8. Verification 是否执行？
9. 系统是否恢复？
10. 如果失败，失败在哪里？
```

## 18. 最终目标

Evaluation 不只是“给模型打分”，而是建立：

```text
Scenario Library
      ↓
Deterministic Replay
      ↓
Trajectory Capture
      ↓
Behavior Assertions
      ↓
Safety Gates
      ↓
Regression Benchmark
      ↓
Agent Version Promotion
```

只有这样，Agent 才能像传统软件一样持续迭代，而不是每次换模型、Prompt 或工具后重新靠人工观察。