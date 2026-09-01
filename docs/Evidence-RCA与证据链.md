# Evidence、RCA 与证据链设计

> 本文定义平台如何把“模型认为发生了什么”与“系统实际上观察到了什么”分离，并最终形成可以审计、复盘和验证的根因分析链路。

## 1. 为什么需要 Evidence 层

直接把 Tool Result 放进 Prompt 会产生三个问题：

1. 数据可能非常大。
2. 不同工具返回格式不同。
3. 模型很难区分事实、推断和历史信息。

因此平台应该形成：

```text
External System
   ↓
Tool
   ↓
Raw Result
   ↓
Evidence Adapter
   ↓
Normalized Evidence
   ↓
Evidence Store
   ↓
Context Reference
   ↓
Agent Reasoning
```

## 2. Evidence 与 Hypothesis 的区别

### Evidence

系统观察到的事实：

```text
CPU = 94%
observed_at = 10:05
source = prometheus
```

### Hypothesis

基于事实提出的解释：

```text
CPU throttling 可能导致延迟升高
```

不能把 Hypothesis 当 Evidence。

## 3. Evidence 数据结构

建议至少包含：

```text
Evidence ID
Source
Source Type
Observed At
Collected At
Resource
Query / Operation
Raw Reference
Normalized Fact
Quality
Confidence
Correlation ID
Incident ID
```

其中：

- `Observed At`：事实发生时间。
- `Collected At`：平台取得事实的时间。
- `Raw Reference`：原始数据位置或摘要引用。
- `Correlation ID`：用于关联一次调查过程。

## 4. 为什么必须保存时间

RCA 是时间问题。

例如：

```text
09:58 deployment
10:00 error rate ↑
10:01 latency ↑
10:05 alert
```

如果 Evidence 没有准确时间，就无法建立事件顺序。

## 5. Evidence Provenance

每个关键结论都应该能追溯到：

```text
Conclusion
   ↓
Evidence IDs
   ↓
Tool Calls
   ↓
External Source
```

例如：

```text
Diagnosis D001
  ↓
E101 CPU throttling
E104 latency increase
E108 deployment change
  ↓
T201 metrics query
T205 deployment history query
```

这样即使模型换了，也能重新检查原始事实。

## 6. Evidence 质量

建议定义质量维度：

```text
Freshness
Completeness
Source Reliability
Consistency
Directness
```

### Freshness
数据是否已经过时。

### Completeness
是否只采集到了部分数据。

### Source Reliability
来源本身是否可信。

### Consistency
多个来源是否互相一致。

### Directness
这是直接观测，还是二次推断。

## 7. 多来源交叉验证

单个来源可能误导 Agent。

例如：

```text
Prometheus：CPU 95%
Kubernetes：CPU limit 1000m
Metrics：throttled 42%
Application：latency P99 ↑
```

这些 Evidence 互相支持一个较强的判断：

```text
CPU throttling 与 latency increase 具有关联
```

而不是只根据一条告警判断。

## 8. RCA 不是“猜一个根因”

推荐 RCA 过程：

```text
Incident
 ↓
Timeline
 ↓
Symptoms
 ↓
Candidate Hypotheses
 ↓
Evidence Collection
 ↓
Hypothesis Testing
 ↓
Elimination
 ↓
Most Supported Explanation
 ↓
Action
 ↓
Verification
```

## 9. Timeline

先建立时间线，再讨论因果。

例如：

```text
09:40 正常
09:55 发布版本
09:57 CPU 开始升高
09:59 P99 开始升高
10:02 错误率升高
10:05 告警触发
```

Timeline 可以帮助 Agent 发现需要继续调查的窗口。

## 10. Correlation 不等于 Causation

```text
deployment at 09:55
error at 09:57
```

只能证明时间相关。

需要继续验证：

```text
版本差异
流量变化
资源限制
依赖变化
错误日志
历史基线
```

只有多个独立证据共同支持，才能提高因果置信度。

## 11. Hypothesis 生命周期

```text
PROPOSED
   ↓
TESTING
   ↓
SUPPORTED
   ├──→ DISPROVED
   └──→ INCONCLUSIVE
```

不要让一个 Hypothesis 一旦生成就永久成为“根因”。

## 12. 证据不足怎么办

合法结果应该是：

```text
INCONCLUSIVE
```

或者：

```text
INSUFFICIENT_EVIDENCE
```

并明确告诉用户：

```text
已确认事实
尚未确认事实
当前最可能假设
还需要什么数据
```

## 13. Evidence 去重

同一个 Tool 因重试可能产生相同结果。

应该通过：

```text
source
query
resource
observation window
content fingerprint
```

识别重复 Evidence。

否则 Context 会快速膨胀。

## 14. Evidence 与 Context 的关系

Context 不需要保存全部原始数据。

应该保存：

```text
Evidence ID
Fact Summary
Source
Timestamp
Relevance
```

需要详细内容时再通过 Evidence Store 获取。

## 15. Evidence 与 Memory 的关系

不是所有 Evidence 都应该进入长期 Memory。

```text
Evidence
  ↓
Incident-specific fact
  ↓
Verification
  ↓
稳定、可复用知识
  ↓
Long-term Memory
```

例如：

```text
“今天 CPU = 95%”
```

通常不应该成为长期知识。

而：

```text
“该服务在特定版本开启某功能后，CPU 使用率会显著增加；回滚后恢复。”
```

在得到充分验证后才有长期沉淀价值。

## 16. Action 也必须产生 Evidence

执行本身也是事实：

```text
Action started
Action completed
External request ID
Target state before
Target state after
```

然后 Verification 再产生：

```text
Service recovered
```

最终链路：

```text
Initial Evidence
 ↓
Diagnosis
 ↓
Action
 ↓
Execution Evidence
 ↓
Verification Evidence
 ↓
Resolution
```

## 17. Evidence-driven Agent 的最终输出

一个好的 RCA 输出不应该只是：

```text
根因是 CPU throttling。
```

而应该包含：

```text
结论：CPU throttling 是当前最可能的主要因素。

已确认事实：
- CPU 使用率持续升高
- CPU limit 已达到配置上限
- throttling 指标明显增加
- 延迟与 throttling 同时间窗口上升

支持证据：
E101 / E104 / E108

未确认：
- 是否存在代码级 CPU 回归

建议：
先执行低风险、可回滚的资源调整，并通过独立指标验证。
```

这才是可以被审计和复盘的 RCA。