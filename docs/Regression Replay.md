# Regression Replay：防止 Agent 修复后再次退化

## 1. 目标

Regression Replay 解决一个核心问题：

> Agent 这次修好了一个 Badcase，下一次改 Prompt、Tool、模型或 Runtime 后，会不会又坏掉？

因此每一个重要 Badcase 都应该沉淀成可重复 Replay Case。

## 2. 闭环

```text
Badcase
 ↓
Root Cause
 ↓
Expected Behavior
 ↓
Regression Scenario
 ↓
Assertion
 ↓
Replay
 ↓
Pass / Fail
 ↓
Release Gate
```

## 3. Regression Case 来源

主要来源：

```text
历史 Badcase
生产 Incident
Scenario Replay
安全测试
Tool Failure
Prompt Injection
人工发现问题
```

## 4. Replay 的确定性

需要尽量固定：

```text
Scenario
Evidence
Tool responses
Policy
Permission
Expected assertions
```

如果依赖真实时间、随机数据或实时外部系统，需要记录或模拟这些变量。

## 5. 什么变化必须触发 Replay

以下变化建议触发完整 Regression：

```text
Model version
Prompt
Agent logic
Context strategy
Memory strategy
Tool schema
Tool implementation
MCP adapter
Policy
Permission
Verification logic
```

## 6. Replay 层级

### 快速回归

每次 PR：

```text
Critical Safety Cases
Changed component cases
```

### 完整回归

发布前：

```text
All Critical Cases
Core Reliability Cases
Historical Badcases
Representative Scenarios
```

### 扩展回归

重大版本：

```text
Production-like Dataset
Long-running Cases
Adversarial Cases
Tool Failure Matrix
```

## 7. Assertion 设计

Regression 不应该只检查最终文本。

应该检查：

```text
行为
Evidence
Tool Calls
Policy
Action
Verification
Final State
```

例如：

```text
assert forbidden_action_count == 0
assert approval_required == true
assert verification_completed == true
assert final_state == RESOLVED
```

## 8. 防止“修复一个、坏十个”

每次 Replay 都应保留：

```text
baseline result
new result
metric delta
new failures
```

例如：

```text
Wrong Diagnosis: -8%
Tool Calls: -15%
Safety Violation: 0 → 0
Latency: +20%
```

如果安全没有退化但成本显著上升，也需要人工评估。

## 9. Safety Regression

安全回归优先级最高。

必须持续测试：

```text
未授权 Action
Policy DENY
Approval 绕过
Scope 越权
Prompt Injection
危险 Tool 参数
```

## 10. Flaky Scenario

如果同一 Scenario 多次运行结果不同，需要区分：

```text
真实 Agent 非确定性
Scenario 不稳定
Tool Simulator 不稳定
Assertion 有问题
```

不能简单把偶发失败标成 flaky 后忽略。

## 11. 多次 Replay

对于存在模型随机性的场景，可以运行 N 次：

```text
Scenario X
 ↓
Run 1
Run 2
...
Run N
```

然后统计：

```text
success rate
safety violation rate
variance
```

## 12. Regression Failure 处理

```text
Regression Failed
 ↓
Block Release
 ↓
Compare baseline
 ↓
Find first divergence
 ↓
Classify Badcase
 ↓
Fix
 ↓
Replay
```

Critical Safety Failure 不应允许通过“重新跑几次直到成功”来掩盖。

## 13. Regression Dataset 版本化

Regression Dataset 本身也需要版本：

```text
dataset-v1
 dataset-v2
 dataset-v3
```

每次新增重要 Badcase 都应该产生可追踪变更。

## 14. Release Decision

最终发布判断建议：

```text
Critical Safety = PASS
Regression = PASS
Core Reliability ≥ threshold
No unexplained severe regression
```

其中 Safety Gate 属于硬门槛。

## 15. 完整工程闭环

```text
代码 / Prompt / Model / Tool 发生变化
 ↓
Regression Replay
 ↓
Evaluation
 ↓
Badcase
 ↓
Root Cause
 ↓
Fix
 ↓
新增 Regression Case
 ↓
Replay
 ↓
Release
```

这使 Agent 的迭代方式逐渐接近传统软件工程：

```text
Bug
→ Test
→ Fix
→ Regression
→ Release
```

而不是：

```text
Prompt 改了
→ 看起来好一点
→ 上生产
```
