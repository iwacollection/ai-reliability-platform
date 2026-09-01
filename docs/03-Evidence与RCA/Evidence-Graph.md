# Evidence Graph 设计

> Evidence Graph 用来解决“单条证据无法解释复杂事故”的问题，把事件、资源、指标、日志、变更、假设、动作和验证结果组织成可追溯关系。

## 1. 为什么需要 Evidence Graph

生产事故通常不是：

```text
一个告警 → 一个原因
```

而更接近：

```text
部署变更
   ↓
配置变化
   ↓
Pod 重启
   ↓
连接池耗尽
   ↓
错误率上升
   ↓
告警
```

如果只把每条查询结果放进文本上下文，Agent 很难持续维护这些关系。Evidence Graph 将它们显式建模。

## 2. 核心节点

```text
Incident
Event
Evidence
Resource
Metric
Log
Change
Hypothesis
Action
Verification
Service
Dependency
```

每个节点至少需要：

- 唯一 ID
- 类型
- 时间
- 来源
- 对象
- 摘要
- 可信度
- 原始数据引用

## 3. 核心关系

```text
Event ──triggered──▶ Incident
Evidence ──supports──▶ Hypothesis
Evidence ──contradicts──▶ Hypothesis
Evidence ──observed_on──▶ Resource
Change ──affected──▶ Service
Service ──depends_on──▶ Dependency
Action ──targets──▶ Resource
Action ──produced──▶ Verification
Verification ──confirms──▶ Incident
```

## 4. Evidence Provenance

每一条证据必须回答“从哪里来的”：

```text
Evidence
├── source_type
├── source_id
├── collected_at
├── query
├── collector
├── raw_ref
└── transformation
```

例如模型不能只说“CPU 很高”，而应该能够追溯：

```text
Prometheus
 → 查询 payment-api
 → 时间范围 10 分钟
 → CPU 92%
 → Evidence ev-123
 → 支持 Hypothesis h-7
```

## 5. 证据支持与反驳

RCA 不能只有支持证据，也必须允许反证：

```text
Hypothesis: 内存不足
   │
   ├── + Pod OOMKilled
   ├── + memory working set 持续上升
   └── - 节点剩余内存充足
```

如果存在强反证，Agent 应降低该假设置信度，而不是继续寻找支持自己的信息。

## 6. 时间关系

事故分析高度依赖时间：

```text
14:00 Deployment changed
14:03 Pod restarted
14:05 latency increased
14:06 error rate increased
14:07 alert fired
```

因此 Evidence Graph 应支持时间排序，并允许查询：

- 某个告警之前发生了什么？
- 某次变更之后出现了什么？
- 哪些 Evidence 同时发生？
- 修复后指标是否恢复？

## 7. 从 Graph 到 RCA

Agent 可以沿图进行调查：

```text
Alert
 ↓
Service
 ↓
Recent Changes
 ↓
Affected Resource
 ↓
Metrics / Logs
 ↓
Hypothesis
 ↓
Counter Evidence
 ↓
Root Cause
```

这比让 LLM 单纯阅读一大段日志更可控。

## 8. 防止幻觉

Evidence Graph 本身不能消除 LLM 幻觉，但可以把“结论”和“证据”分开：

```text
Fact
 └── Evidence

Hypothesis
 └── supporting / contradicting Evidence

Conclusion
 └── references Hypotheses
```

没有 Evidence 引用的关键生产结论应被标记为“未证实”。

## 9. 数据生命周期

原始数据可以进入对象存储或日志系统，Graph 保存索引和关系：

```text
Graph
  ├── metadata
  ├── relationships
  └── raw_ref
          ↓
       Raw Store
```

这样不会为了保存关系而把所有日志复制到 Agent Context。

## 10. 当前实现与演进

当前仓库已经建立 Evidence、Incident、Evaluation、Scenario Replay 等代码边界。Evidence Graph 是对这些对象关系的长期统一模型，后续可以逐步从内存结构演进到持久化图模型，而不要求 Agent 直接依赖具体数据库。

## 11. 验收标准

- 每条 Evidence 可追溯来源；
- 支持 Evidence 支持/反驳假设；
- 支持时间关联；
- Action 与 Verification 可以关联；
- RCA 结论可以反查证据；
- 原始大数据不必全部进入 LLM Context；
- Evidence 生命周期和敏感信息策略明确。
