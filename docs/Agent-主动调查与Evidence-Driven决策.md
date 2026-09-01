# Agent 主动调查与 Evidence-Driven 决策设计

> 本文重点回答：Agent 到底如何“主动发现问题”？为什么不会变成凭空猜测？什么时候继续查、什么时候停止、什么时候可以提出动作？

## 1. 先定义“主动发现”

主动发现不是：

```text
让大模型自己想一个问题
```

而是：

> 给定 Incident Objective、当前状态和已有 Evidence，Agent 判断完成当前目标还缺少哪些可验证事实，并主动选择能够减少这些证据缺口的调查动作。

所以主动性来自：

```text
Objective
   ↓
Current Evidence
   ↓
Evidence Gap
   ↓
Candidate Investigation
   ↓
Tool Selection
   ↓
New Evidence
   ↓
Hypothesis Update
```

## 2. 告警不是根因

例如收到：

```text
payment-api CPU > 90%
```

错误 Agent：

```text
CPU 高 → CPU 不够 → 扩容
```

可靠 Agent 至少要区分：

```text
流量增长？
CPU limit throttling？
代码版本变化？
线程/GC？
下游重试？
异常循环？
节点资源竞争？
```

告警只是 Incident 的起点，不是 RCA 的结论。

## 3. 从 Hypothesis 出发，而不是直接执行

Agent 可以维护多个候选假设：

```text
H1: traffic growth
H2: CPU throttling
H3: recent deployment regression
H4: downstream retry amplification
H5: node contention
```

每个假设都需要定义：

```text
支持它需要什么 Evidence
反驳它需要什么 Evidence
当前已经有什么 Evidence
还缺什么
下一步最便宜的验证方式是什么
```

## 4. Evidence Gap 模型

推荐将证据缺口显式表示：

```json
{
  "hypothesis": "CPU throttling causes latency increase",
  "required_evidence": [
    "cpu_usage",
    "cpu_limit",
    "cpu_throttled",
    "latency"
  ],
  "available_evidence": [
    "cpu_usage",
    "latency"
  ],
  "missing_evidence": [
    "cpu_limit",
    "cpu_throttled"
  ],
  "next_investigation": "get_container_cpu_throttling"
}
```

这样 Agent 的“主动调查”就变成可以测试的行为。

## 5. 下一步工具怎么选

不能只根据工具描述选择。应该综合：

```text
Evidence Gap
+ Tool Capability
+ Expected Information Gain
+ Cost
+ Risk
+ Timeout
+ Permission
```

例如当前需要区分“流量增长”和“CPU throttling”：

```text
get_logs
    信息增益：中
    成本：中

get_cpu_metrics
    信息增益：高
    成本：低

restart_pod
    信息增益：低
    风险：高
```

因此调查阶段优先调用 `get_cpu_metrics`，而不是因为“重启可能有效”就执行重启。

## 6. 调查应该优先低风险、高信息增益

建议调查顺序：

```text
现有 Evidence
  ↓
只读、低成本查询
  ↓
时间趋势
  ↓
资源状态
  ↓
日志 / Trace
  ↓
最近变更
  ↓
依赖状态
  ↓
跨系统关联
  ↓
形成高置信度假设
  ↓
才进入 Action
```

这相当于把 Agent 的探索空间从“所有工具”压缩成“能够验证当前假设的工具”。

## 7. 时间窗口非常重要

单点指标经常无法证明原因。

例如：

```text
10:05 CPU = 95%
```

只能说明此刻高。

更有价值的是：

```text
09:30 CPU = 40%
09:45 CPU = 45%
09:58 发布新版本
10:00 CPU = 70%
10:03 CPU = 85%
10:05 CPU = 95%
```

这时“发布后开始升高”成为一个有证据支持的相关性。

但仍不能直接写成因果：

```text
deployment = root cause
```

还应该继续做：

```text
Compare before / after
检查流量是否同时变化
检查错误率
检查依赖
必要时比较历史版本
```

## 8. Evidence 的可信等级

建议至少区分：

| 等级 | 含义 | 示例 |
|---|---|---|
| A | 系统直接观测 | Prometheus 指标、K8s API |
| B | 多个独立来源一致 | 指标 + 日志一致 |
| C | 历史经验 | 相似 Incident |
| D | 模型推断 | LLM 自己的判断 |

自动动作的依据应该尽量由 A/B 级 Evidence 支撑。

C 级可以帮助缩小调查范围，但不能直接证明当前故障。

D 级只能作为 Hypothesis，不能伪装成事实。

## 9. 如何降低幻觉

### 9.1 事实与推断分离

```text
Fact:
CPU throttled = 42%

Inference:
CPU limit 可能导致应用延迟
```

模型不能把：

```text
“可能”
```

写成：

```text
“已经证明”
```

### 9.2 所有关键结论绑定 Evidence ID

例如：

```text
Diagnosis:
CPU throttling is a likely contributor.

Evidence:
E102
E105
E107
```

### 9.3 无证据结论不能触发高风险 Action

```text
No evidence
   ↓
Hypothesis only
   ↓
Continue investigation / escalate
```

而不是：

```text
No evidence
   ↓
Restart production
```

## 10. 什么时候继续调查

建议继续调查的条件：

```text
关键 Evidence 缺失
OR
多个 Hypothesis 无法区分
OR
当前置信度不足
OR
Verification 需要更多前置事实
```

## 11. 什么时候停止调查

可以停止的情况：

```text
已满足 Incident Objective
AND
关键结论有充分 Evidence
AND
不存在必须解决的关键不确定性
```

停止并升级人工的情况：

```text
证据互相矛盾
工具不可用
权限不足
风险超过自动化边界
持续无进展
达到预算
```

## 12. “不确定”是合法结果

可靠 Agent 必须允许：

```text
INSUFFICIENT_EVIDENCE
```

例如：

```text
当前确认：
- CPU 持续升高
- throttling 明显

无法确认：
- 是流量增长还是代码回归导致 CPU 增长

建议：
继续采集 deployment / traffic / profile 数据
```

这比编造一个确定根因更加可靠。

## 13. 主动调查的伪代码

```text
while budget_available:
    state = load_incident_state()
    evidence = state.evidence

    objective = state.objective
    hypotheses = update_hypotheses(objective, evidence)
    gaps = calculate_evidence_gaps(hypotheses, evidence)

    if objective_satisfied(hypotheses, evidence):
        return READY_FOR_PLAN

    if critical_conflict(gaps):
        return ESCALATE

    candidate_tools = find_tools_for(gaps)
    tool = rank_by_information_gain_cost_risk(candidate_tools)

    decision = model_decide(tool, state)
    validated = runtime_validate(decision)

    if not validated:
        repair_or_stop()

    result = execute_readonly_tool(tool)
    normalized = evidence_adapter(result)
    persist_evidence(normalized)

    if no_progress_detected():
        return INSUFFICIENT_EVIDENCE
```

## 14. 生产上最重要的边界

```text
Agent 可以决定调查方向
Agent 不能决定自己的权限

Agent 可以提出 Action
Agent 不能绕过 Policy

Agent 可以解释 Evidence
Agent 不能伪造 Evidence

Agent 可以请求执行
Runtime 决定是否允许执行

Action 可以成功
Verification 仍然必须独立判断恢复
```

这组边界是整个平台“Agent-first 但不失控”的核心。