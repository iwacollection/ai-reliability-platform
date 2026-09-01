# Incident 时间线与事件关联规范

## 1. 为什么需要时间线

事故调查的关键不是罗列日志，而是回答“先发生了什么、随后发生了什么、哪些变化与故障存在时间关系”。时间线是 Evidence、RCA 和 Verification 的共同骨架。

## 2. 时间线对象

每个 Timeline Event 至少包含：

```text
id
timestamp
source
event_type
resource
summary
evidence_refs
confidence
causality_hint
```

`causality_hint` 只能表示候选关系，不能把时间先后直接当成因果关系。

## 3. 标准时间线

```text
T0  正常基线
T1  配置 / 发布 / 扩缩容变化
T2  指标开始异常
T3  日志出现错误
T4  Alert 触发
T5  Agent 开始调查
T6  找到关键 Evidence
T7  执行 Action
T8  指标恢复
T9  Verification 完成
```

## 4. 多来源时间统一

不同系统可能使用不同时间精度、时区和采集延迟。平台应统一保存：

- 原始时间戳
- 标准化时间戳
- 时区
- 采集时间
- 数据源
- 时间精度

不能因为日志晚到，就错误认为日志发生在指标之后。

## 5. 事件类型

建议至少支持：

- `ALERT`
- `METRIC_ANOMALY`
- `LOG_ERROR`
- `TRACE_ANOMALY`
- `DEPLOYMENT`
- `CONFIG_CHANGE`
- `RESOURCE_CHANGE`
- `TOOL_CALL`
- `ACTION`
- `APPROVAL`
- `VERIFICATION`
- `HUMAN_INTERVENTION`

## 6. 时间窗口

Incident 至少需要：

```text
baseline_window
pre_incident_window
incident_window
recovery_window
post_recovery_window
```

例如分析发布导致的延迟问题时，不能只看告警后的 5 分钟，还应该比较发布前基线和恢复后的状态。

## 7. 因果分析

时间线只建立候选关系：

```text
Deployment
   ↓ 30s
Error Rate ↑
   ↓ 10s
Latency ↑
```

这只能说明时间相关性。要形成根因，还需要资源关系、指标关系、日志证据或实验验证。

## 8. Timeline 与 Evidence Graph

Timeline 解决“什么时候发生”；Evidence Graph 解决“这些事实之间是什么关系”。两者结合：

```text
Timeline Event
   ↓ references
Evidence
   ↓ supports
Hypothesis
   ↓ verified by
Verification
```

## 9. Agent 调查过程也必须进入时间线

不能只记录业务系统事件。Agent 自己的行为也必须可审计：

- 调用了什么工具
- 为什么调用
- 得到什么结果
- 发现什么 Evidence
- 何时修改假设
- 为什么执行 Action
- 谁批准
- 如何验证

## 10. 时序冲突处理

如果两个数据源给出的时间顺序冲突，保留原始数据，不强行修正。系统可以生成 `TIME_ORDER_CONFLICT`，要求 Agent 将其作为调查不确定性。

## 11. 验收标准

- [ ] 所有关键 Incident 都可以生成完整 Timeline。
- [ ] 原始时间与标准时间均可追溯。
- [ ] Agent 行为进入 Timeline。
- [ ] Timeline 不把时间相关性直接当因果关系。
- [ ] 恢复前后都有验证窗口。
- [ ] 时间冲突不会被静默覆盖。
