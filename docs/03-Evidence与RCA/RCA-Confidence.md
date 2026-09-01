# RCA 置信度与证据评分规范

## 1. 目标

RCA（根因分析）不能等同于模型生成一句“可能原因”。平台需要把结论拆成假设、证据、反证和验证结果，并给出可解释的置信度。

## 2. 核心模型

```text
Evidence
  ↓ supports / contradicts
Hypothesis
  ↓ confidence
RCA Candidate
  ↓ verification
Confirmed Root Cause
```

置信度表达“当前证据对该假设的支持程度”，不是模型自信程度。

## 3. 证据质量

建议将证据分为：

| 等级 | 含义 | 示例 |
|---|---|---|
| A | 直接、实时、可验证 | 目标 Pod 当前 OOMKilled 事件 |
| B | 强相关、可交叉验证 | 指标与日志同时异常 |
| C | 间接证据 | 历史相似事故 |
| D | 推测或未经验证 | 模型常识、经验判断 |

自动修复至少要求关键结论存在 A/B 级证据，并且没有关键反证。

## 4. 评分维度

一个假设可以从以下维度计算内部评分：

```text
支持强度
× 时间相关性
× 来源可信度
× 独立来源数量
× 直接性
− 反证强度
```

这不是要求模型直接算数学分数，而是 Runtime 用结构化规则约束模型结论。

## 5. 时间相关性

证据必须与 Incident 时间窗口关联。例如 CPU 在事故前 30 秒持续升高，比一周前同一服务 CPU 升高更有价值。

至少保存：

- observed_at
- collected_at
- incident_window
- source_latency

## 6. 独立性

同一底层数据被不同 API 返回，不应该简单计为两份独立证据。平台应尽可能记录 `source_id` 和 `derived_from`，避免重复计权。

## 7. 反证机制

每个重要假设必须支持：

```text
supporting_evidence
contradicting_evidence
unknowns
required_verification
```

例如“数据库导致接口延迟”不能只收集数据库慢查询，还要检查是否存在网络丢包、线程池耗尽、下游服务异常等反证。

## 8. 置信度状态

推荐使用：

```text
UNASSESSED
LOW
MEDIUM
HIGH
CONFIRMED
REJECTED
```

`HIGH` 不等于 `CONFIRMED`。只有经过独立验证或实际结果验证，才能进入 `CONFIRMED`。

## 9. 防止幻觉

以下内容不得直接作为 RCA 事实：

- 模型猜测
- 没有 Evidence ID 的数字
- 没有来源的历史信息
- 工具未执行却声称工具结果
- 无法回溯来源的上下文摘要

模型可以提出假设，但 Runtime 必须要求事实绑定 Evidence。

## 10. RCA 决策门

```text
是否存在直接证据？
  ↓ 否 → 继续调查
  ↓ 是
是否存在关键反证？
  ↓ 是 → 降低置信度 / 调查反证
  ↓ 否
是否完成验证？
  ↓ 否 → 允许诊断，不允许高风险 Action
  ↓ 是
CONFIRMED
```

## 11. RCA 输出结构

```text
Root Cause
Confidence
Supporting Evidence
Contradicting Evidence
Affected Resources
Time Window
Verification
Remaining Unknowns
```

## 12. 验收标准

- [ ] 每个 RCA 都能追溯 Evidence。
- [ ] 支持证据和反证分开记录。
- [ ] 历史经验不能直接充当当前事故事实。
- [ ] HIGH 与 CONFIRMED 有明确区别。
- [ ] 没有证据的模型推测不能进入自动修复依据。
