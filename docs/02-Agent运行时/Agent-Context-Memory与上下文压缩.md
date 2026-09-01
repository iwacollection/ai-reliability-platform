# Agent Context、Memory 与上下文压缩设计

> 本文回答一个核心问题：Agent 为什么不能把所有历史信息永久塞进上下文，以及平台如何在“记得足够多”和“上下文不过载”之间取得平衡。

## 1. 问题背景

Agent 调查一个生产事故时，会不断产生信息：告警、日志、指标、Kubernetes 对象、工具调用结果、用户对话、历史事件、动作计划和验证结果。如果每一步都把完整结果原样追加到模型上下文，会出现三个问题：

1. 上下文越来越大，最终达到模型窗口限制。
2. 大量低价值工具输出挤压真正重要的证据。
3. 历史错误判断、重复信息和已经失效的状态继续影响后续决策。

因此 Context 不是简单的“聊天记录”，而是 Agent 当前可用于决策的工作集；Memory 则保存跨轮次或跨 Incident 仍有价值的信息。

## 2. Context 与 Memory 的区别

```text
                    Agent Runtime
                         │
              ┌──────────┴──────────┐
              │                     │
          Working Context        Memory
          当前事故工作集           可长期复用知识
              │                     │
       当前事件/假设/证据      历史事故/经验/摘要
       工具结果/计划/状态      已验证模式/偏好
              │                     │
              └──────────┬──────────┘
                         ▼
                       LLM
```

### Context

Context 服务于“现在这一轮决策”。它必须能够回答：

- 当前 Incident 是什么？
- 当前目标是什么？
- 已经知道什么？
- 哪些假设成立/不成立？
- 已经调用过什么工具？
- 最近一次工具结果是什么？
- 当前 Agent 处于什么状态？
- 还剩多少调查预算？

### Memory

Memory 服务于“未来是否值得再次使用”。并不是所有 Incident 数据都应该进入长期记忆。

适合记忆：

- 已验证的故障模式
- 稳定的服务拓扑知识
- 经人工确认的修复经验
- 可复用的调查路径
- 已知误报模式

不应该直接长期记忆：

- 未验证的模型猜测
- 一次性的临时状态
- 大量原始日志
- 包含敏感信息的原始凭据
- 没有来源的自然语言结论

## 3. Context 分层

建议将 Context 分成四层：

```text
L0 事件层
    原始告警 + StandardEvent

L1 事实层
    当前已验证事实 + Evidence

L2 推理层
    假设 + 当前判断 + 下一步调查计划

L3 执行层
    Tool Call + Action + Approval + Verification
```

其中 L0/L1 应尽量稳定，L2 可以随着调查变化，L3 必须保留完整审计记录但不一定全部进入每次 LLM Prompt。

## 4. 为什么不能简单截断历史

错误做法：

```text
messages[-20:]
```

这种做法虽然简单，却可能把事故开始时的关键告警、基线和已经验证的事实截掉，只留下最近的噪声。

更合理的方法是“语义压缩 + 重要性保留”：

```text
原始历史
   ↓
分类
   ├── 永久关键事实 → 保留
   ├── 当前假设 → 保留
   ├── 最近工具结果 → 保留
   ├── 重复输出 → 合并
   ├── 已失效状态 → 降级
   └── 原始大结果 → 摘要 + 外部引用
```

## 5. 上下文预算

每次 Agent Loop 都应该有预算概念，而不是无限追加：

```text
Context Budget
├── System / Policy
├── Incident Summary
├── Critical Evidence
├── Current Hypotheses
├── Recent Tool Results
└── Current User Request
```

预算控制的目标不是单纯追求更短，而是保证模型看到的信息与当前决策高度相关。

## 6. Evidence 与 Context 的关系

Evidence 应该保存为结构化对象，而不是只把工具输出复制进 Prompt：

```json
{
  "id": "ev-123",
  "type": "metric",
  "source": "prometheus",
  "timestamp": "...",
  "subject": "payment-api",
  "summary": "5xx 在过去 10 分钟持续升高",
  "raw_ref": "evidence://ev-123",
  "confidence": 0.96
}
```

模型上下文可以只携带 `summary + metadata`，需要详细内容时再根据 `raw_ref` 获取。这种设计同时降低上下文污染和重复传输。

## 7. 记忆写入原则

Memory 不应该由 LLM 随意写入。建议经过以下流程：

```text
Incident 完成
   ↓
候选经验提取
   ↓
Evidence 来源检查
   ↓
是否可复用？
   ├── 否 → 丢弃
   └── 是
       ↓
人工确认 / 规则校验
       ↓
写入 Memory
```

尤其不能因为模型说“以后记住这个”就直接形成长期事实。

## 8. 记忆过头与记忆不足

### 记忆过头

表现：每个事故都产生大量记忆，检索时返回几十条类似经验，模型反而不知道哪些重要。

治理：

- 设置记忆生命周期
- 对相似记忆合并
- 保存来源和验证次数
- 设置置信度
- 删除过期知识
- 限制单次检索数量

### 记忆不足

表现：同一个服务反复发生同一种故障，Agent 每次都从零调查。

治理：

- 对已验证 RCA 建立结构化经验
- 将事故模式与服务/指标/错误特征关联
- 允许从历史 Incident 检索相似案例
- 将人工确认的修复经验提升为高优先级知识

## 9. 长任务恢复

Context 不能只存在进程内存。长任务应该持久化检查点：

```text
Loop N
 ↓
Checkpoint
 ├── Incident state
 ├── Context summary
 ├── Evidence IDs
 ├── Hypotheses
 ├── Tool history
 ├── Pending approval
 └── Budget
```

Runtime 重启后应从最近一个安全检查点恢复，而不是重新让模型猜测过去发生了什么。

## 10. 当前实现与演进方向

当前仓库已经存在 Context、Memory、Conversation、Evaluation 等代码边界；本文定义的是这些边界应该遵循的长期工程契约。具体实现逐步向“结构化 Context + Evidence 引用 + 可恢复 Checkpoint + 分层 Memory”演进。

## 11. 验收标准

一个可生产使用的 Context/Memory 系统至少应证明：

- 上下文不会无限增长。
- 压缩后关键事实仍然存在。
- Evidence 可以追溯原始来源。
- 模型猜测不会自动成为长期事实。
- Runtime 重启可以恢复长任务。
- 历史 Memory 不会覆盖当前 Incident 的实时事实。
- 敏感信息不会因为摘要或 Memory 写入而扩大暴露范围。
