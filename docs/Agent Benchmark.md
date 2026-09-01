# Agent Benchmark：Reliability Agent 的基准评测体系

## 1. Benchmark 的目标

Benchmark 不是比较“哪个模型回答更像人”，而是建立稳定、可重复、可解释的 Agent 能力基线。

```text
Scenario Dataset
 ↓
固定评测协议
 ↓
Agent Version
 ↓
统一 Replay
 ↓
指标计算
 ↓
版本比较
```

## 2. Benchmark 的最小单位

一个 Benchmark Case 应包含：

```text
case_id
scenario_id
category
initial_state
available_evidence
available_tools
policy
expected_behavior
forbidden_behavior
success_criteria
```

## 3. 指标体系

### 正确性

- 关键 Evidence 覆盖率
- 正确诊断率
- 正确 Action 率
- 恢复成功率
- Verification 正确率

### 安全性

- 未授权操作率
- Policy 绕过率
- Approval 绕过率
- 禁止 Action 率

### 效率

- Tool Call 数
- Investigation Step 数
- Token 消耗
- 延迟

### 稳定性

- Replay 成功率
- 同场景结果一致性
- Tool Failure Recovery Rate

## 4. Hard Gate

以下指标不能通过其他维度抵消：

```text
Unauthorized Action
Policy Bypass
Approval Bypass
Forbidden Destructive Action
False Verification Success
```

出现任意一种都可以直接判定 Benchmark Case Failed。

## 5. 成功不等于正确

例如：

```text
Agent 删除整个 Namespace
 ↓
服务恢复
```

Outcome 看起来成功，但 Safety 必须失败。

因此 Benchmark 必须同时评价：

```text
Outcome
+
Safety
```

## 6. Abstention

Agent 在证据不足时应该能够说：

```text
当前证据不足，无法安全执行。
```

这是正确行为，而不是失败。

但如果证据已经充分却一直拒绝行动，则属于：

```text
Unnecessary Abstention
```

需要单独统计。

## 7. 版本比较

必须保证：

```text
同一 Dataset
同一 Scenario
同一 Policy
同一 Tool Contract
```

然后比较：

```text
v1 → v2
```

避免“换了测试集以后分数变高”。

## 8. 防止数据泄漏

Benchmark Dataset 不应该被 Agent Prompt 直接包含 expected answer。

否则测到的是记忆能力，而不是推理和调查能力。

## 9. 分层 Benchmark

建议分为：

```text
L0 Tool Calling
L1 Evidence Gathering
L2 Diagnosis
L3 Action Planning
L4 Safety / Approval
L5 Verification
L6 End-to-End Incident
```

从局部能力逐步升级到完整 Incident。

## 10. Release Gate

Agent 新版本发布前至少执行：

```text
Critical Safety Suite
Core Reliability Suite
Regression Suite
Representative Production-like Suite
```

任何 Critical Safety Case 失败都应该阻止发布。

## 11. Benchmark 报告

必须保存：

```text
agent_version
model_version
prompt_version
tool_version
policy_version
dataset_version
case_results
aggregate_metrics
failed_cases
```

否则无法解释版本差异。

## 12. Benchmark 的最终意义

Benchmark 的价值是把：

```text
“感觉这个 Agent 好像更聪明了”
```

变成：

```text
Recovery +8%
Wrong Diagnosis -12%
Tool Calls -18%
Safety Violation = 0
Verification Failure -20%
```

从主观体验变成可比较工程指标。
