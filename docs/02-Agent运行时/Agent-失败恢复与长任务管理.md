# Agent 失败恢复与长任务管理

> 本文定义 AI Reliability Platform 中 Agent 长任务的生命周期、失败分类、重试、降级、检查点、恢复、超时、取消和人工接管机制。

## 1. 为什么需要这套机制

Agent 不是一次请求一次回答。生产排障通常需要连续读取证据、比较假设、调用多个工具、形成行动计划、执行动作并验证结果。任何一步都可能失败，因此不能把 Agent 设计成“模型调用成功就算任务成功”。

核心原则：

1. **模型失败与任务失败分离**：一次模型超时不等于 Incident 失败。
2. **可恢复失败优先恢复**：网络抖动、限流、临时连接错误可以重试。
3. **不可恢复失败快速停止**：参数非法、权限拒绝、策略阻断不能盲目重试。
4. **长任务必须可检查点恢复**：进程重启后不能丢失调查进度。
5. **执行与恢复必须幂等**：尤其是生产 Action，不能因为 Retry 重复执行危险操作。
6. **失败必须留下证据**：每次失败、重试、降级和接管都必须可审计。

## 2. 长任务生命周期

```text
CREATED
  ↓
RUNNING
  ↓
┌───────────────┐
│ 调查 / 决策循环 │
└───────────────┘
  ↓
CHECKPOINT
  ↓
继续运行
  ├── 成功 → COMPLETED
  ├── 可恢复失败 → RETRY / BACKOFF
  ├── 工具降级 → FALLBACK
  ├── 超时 → TIMEOUT
  ├── 策略阻断 → BLOCKED
  ├── 需要人工 → WAITING_APPROVAL
  └── 不可恢复 → FAILED / ESCALATED
```

## 3. 失败分类

### 3.1 模型类失败

包括模型超时、服务不可用、返回格式错误、输出无法通过 Schema 校验、上下文超限。

处理顺序：格式修复 → 缩减 Context → 切换模型/Provider → 人工接管。

### 3.2 工具类失败

包括连接失败、权限不足、目标不存在、参数错误、API 限流、目标系统内部错误。

工具返回必须区分：`retryable`、`non_retryable`、`permission_denied`、`not_found`、`rate_limited`、`timeout`、`unknown`。

### 3.3 Agent 推理失败

例如模型持续产生相同 Tool Call、假设无法获得支持证据、调查没有进展。

此类失败不能简单增加轮数，应触发 No-Progress 检测，重新规划调查或升级人工。

### 3.4 Action 执行失败

Action 已经进入真实执行阶段时，必须记录执行意图、目标、参数摘要、执行结果和幂等键。失败后先判断“是否已经执行成功但响应丢失”，不能直接再次执行。

## 4. Retry 设计

Retry 必须由 Runtime 控制，不能让 LLM 自己决定无限重试。

建议字段：

```text
retry_count
max_retries
backoff_seconds
retryable
idempotency_key
last_error
```

指数退避示例：

```text
1s → 2s → 4s → 8s → 16s
```

对于限流错误，应优先使用服务端提供的 Retry-After；对于权限错误、参数错误、策略阻断，不重试。

## 5. Fallback

Fallback 的目标不是“随便换一个工具”，而是降低任务目标而不是降低安全标准。

例如：

```text
Prometheus 查询失败
    ↓
尝试缓存的历史指标
    ↓
尝试 Kubernetes 当前状态
    ↓
只能形成诊断结论
    ↓
禁止进入自动修复
```

Fallback 后必须明确证据质量下降，不能把低质量证据伪装成正常实时证据。

## 6. Checkpoint

每一个高价值状态变化都应该产生检查点，至少包括：

- incident_id
- run_id
- 当前状态
- 当前假设
- 已确认事实
- 已收集 Evidence ID
- 已执行 Tool Call 摘要
- 尚未验证的假设
- 下一步计划
- retry 状态
- approval 状态
- action 状态
- token / tool / time budget

Checkpoint 不应该保存完整 Prompt，而应该保存结构化状态和必要引用，避免 Context 无限膨胀。

## 7. 恢复流程

```text
Runtime 重启
 ↓
读取 run checkpoint
 ↓
检查状态版本
 ↓
恢复 Incident Context
 ↓
检查未完成 Tool Call
 ↓
检查 Action 是否可能已经执行
 ↓
恢复调查循环
```

如果发现一个 Action 状态为 `UNKNOWN`，必须先查询目标系统确认实际状态，再决定是否继续，而不是直接 Retry。

## 8. 超时

至少需要四级预算：

| 预算 | 作用 |
|---|---|
| 单 Tool Timeout | 防止单个外部调用卡死 |
| 单模型 Timeout | 防止模型请求长期阻塞 |
| 单 Agent Run Timeout | 限制一次调查 |
| Incident Deadline | 限制整个事件处理周期 |

下层 Timeout 必须小于上层 Timeout，否则上层没有机会做清理和状态落盘。

## 9. 取消与人工接管

取消不是直接杀进程。Runtime 应先阻止新的 Tool Call，然后等待当前安全边界完成，记录 `CANCELLED`，并保留已有证据。

人工接管至少发生在：

- 高风险 Action
- 策略不明确
- 证据不足
- 连续 No-Progress
- 多个假设无法区分
- 生产执行状态未知
- 超过自动化预算

## 10. 防止“重试把事故放大”

生产 Action 必须使用幂等设计：

```text
Action Request
  ↓
生成 idempotency_key
  ↓
Executor 查询历史执行记录
  ├── 已成功 → 返回历史结果
  ├── 执行中 → 等待 / 查询
  └── 未执行 → 执行
```

例如扩容、重启、切流、修改配置都不能因为网络超时就默认“没执行”。

## 11. No-Progress 检测

以下情况应视为调查无进展：

- 连续多轮重复同一个 Tool Call
- 新增 Evidence 数量长期为零
- 假设置信度没有变化
- 连续输出相同结论
- 已经证明工具无法提供目标证据却继续调用

处理方式：重新规划 → 更换证据源 → 降低自动化级别 → 人工接管。

## 12. 审计要求

每次 Retry、Fallback、Checkpoint、恢复、取消和升级必须产生事件：

```text
run_id
incident_id
event_type
from_state
to_state
reason
evidence_refs
timestamp
actor
```

## 13. 当前实现与目标实现

当前仓库已经具备 Runtime、Scenario Replay、Approval、Action Runtime 等基础能力；本文描述的是统一的生产运行约束。后续实现应优先将 Retry、Checkpoint、Budget、No-Progress 和 Action Idempotency 统一收敛到 Runtime 层，而不是散落在具体 Agent 中。

## 14. 验收标准

- [ ] 单 Tool 超时不会导致整个 Incident 立即丢失。
- [ ] Retry 有明确上限。
- [ ] 非幂等 Action 不允许无条件重试。
- [ ] Runtime 重启可以从 Checkpoint 恢复。
- [ ] No-Progress 可以被检测。
- [ ] Fallback 会降低证据等级而不是伪装成功。
- [ ] 所有失败和恢复都有审计记录。
- [ ] 高风险场景可以人工接管。
