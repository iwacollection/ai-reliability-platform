# Production Runbook：Agent 生产运行手册

> 本手册定义 AI Reliability Platform 上线后的日常运行、监控、容量、安全、降级、恢复与审计标准。

## 1. 生产运行目标

生产环境不是“Agent 能调用模型”就算运行正常，而是必须持续保证：

```text
Incident 能进入 Runtime
Evidence 能获取
Agent 能调查
Tool 能调用
Policy 能判断
Approval 能流转
Action 能执行
Verification 能确认
Audit 能追溯
```

任意一个环节不可用，都可能导致完整闭环中断。

## 2. 生产架构运行面

```text
Alert / ChatOps
      ↓
Gateway
      ↓
Incident
      ↓
Agent Runtime
      ├── Context / Memory
      ├── Evidence
      ├── Tool Registry
      ├── MCP
      ├── Policy Gate
      ├── Approval
      └── Action Executor
              ↓
      Kubernetes / Azure / GitHub / Monitoring
              ↓
          Verification
              ↓
            Audit
```

## 3. 核心 SLI

至少监控：

```text
Incident ingestion success rate
Agent run success rate
Agent investigation completion rate
Tool success rate
Evidence retrieval success rate
Policy decision latency
Approval latency
Action execution success rate
Verification success rate
End-to-end incident resolution rate
```

## 4. Agent 特有指标

建议增加：

```text
agent_loop_iterations
agent_run_duration
tool_calls_per_incident
repeated_tool_call_rate
context_size
context_compaction_count
model_latency
model_error_rate
fallback_rate
abstention_rate
policy_deny_rate
approval_required_rate
verification_failure_rate
```

## 5. 健康检查

健康检查不能只检查 HTTP 200。

至少分层：

```text
L1 Process
L2 Runtime
L3 Dependency
L4 Functional
L5 End-to-End
```

例如：

```text
Process alive
 ↓
Runtime accepts Incident
 ↓
Model reachable
 ↓
Tool Registry available
 ↓
Policy available
 ↓
Replay / synthetic Incident succeeds
```

## 6. Agent 自身故障

Agent Runtime 挂起或崩溃时：

```text
Detect
 ↓
Stop accepting new work
 ↓
Preserve Incident state
 ↓
Restart / failover
 ↓
Resume from checkpoint
 ↓
Re-evaluate stale state
 ↓
Continue or escalate
```

不能简单重启后从头执行可能具有副作用的 Action。

## 7. Agent 无限循环

必须设置：

```text
max_iterations
max_runtime
max_tool_calls
max_repeated_same_call
context_budget
```

达到上限：

```text
STOP
 ↓
保存 Trace
 ↓
标记 NEEDS_HUMAN / FAILED
 ↓
通知人工
```

## 8. 模型不可用

模型异常包括：

```text
timeout
429
5xx
invalid response
context overflow
provider unavailable
```

处理：

```text
Retry
 ↓
Fallback Model（如果允许）
 ↓
Degrade to Evidence Collection
 ↓
Human Escalation
```

模型 Fallback 不得自动提升权限。

## 9. MCP / Tool 不可用

如果单个 Tool 故障：

```text
Tool timeout
 ↓
Classify error
 ↓
Retry if retryable
 ↓
Alternative evidence source
 ↓
继续调查 / 标记 Evidence Gap
```

不能因为一个 Tool 挂了就让 Agent 编造结果。

## 10. Policy Gate 不可用

生产写操作必须：

```text
Policy unavailable
 ↓
FAIL CLOSED
 ↓
DENY / HOLD
```

不能采用：

```text
Policy 挂了
→ 默认 Allow
```

这是核心安全原则。

## 11. Approval 不可用

Approval 服务不可用时：

```text
READ → 可以继续（视系统状态）
WRITE → HOLD
```

已有 Approval 也需要检查有效期与 Action Fingerprint。

## 12. Executor 不可用

Executor 故障时：

```text
不重复执行未知状态 Action
 ↓
查询执行状态
 ↓
确认是否已经生效
 ↓
再决定 Retry / Resume / Rollback
```

这是防止：

```text
请求已成功
但响应丢失
Agent 认为失败
再次执行
```

## 13. Verification 故障

Action 成功但 Verification 不可用：

```text
ACTION_EXECUTED
≠
RECOVERY_CONFIRMED
```

Incident 应进入：

```text
VERIFYING / UNKNOWN
```

不能直接标记 RESOLVED。

## 14. 降级策略

推荐优先级：

```text
Full Agent Investigation
 ↓
Evidence-only Investigation
 ↓
Read-only Diagnosis
 ↓
Human Approval / Manual Operation
 ↓
Incident Escalation
```

降级必须优先保护安全边界，而不是优先保证自动化率。

## 15. 回滚

回滚 Agent Runtime 时必须考虑：

```text
Agent version
Prompt version
Tool Registry version
Policy version
Schema version
Memory format
Scenario version
```

不能只回滚容器镜像而忽略兼容性。

## 16. 发布策略

建议：

```text
Replay
 ↓
Canary
 ↓
Limited incidents
 ↓
Observe
 ↓
Expand
```

新 Agent 版本不应该一次性处理所有生产 Incident。

## 17. Canary 指标

重点比较：

```text
Recovery Rate
Wrong Diagnosis Rate
Wrong Action Rate
Policy Violation Rate
Verification Failure Rate
Tool Call Count
Latency
Cost
```

安全指标恶化应立即停止扩大流量。

## 18. 容量管理

需要关注：

```text
Concurrent Incidents
Agent workers
Model QPS
Tool QPS
MCP connections
Evidence Store IOPS
Context Store
Queue depth
```

Agent 并发不能只按照 CPU 计算，还受模型、外部 API 和工具配额限制。

## 19. Backpressure

Incident 激增时：

```text
Queue
 ↓
Rate Limit
 ↓
Priority
 ↓
Admission Control
```

高优先级生产事故优先于低优先级分析任务。

## 20. 数据与日志

必须保存：

```text
Incident ID
Run ID
Trace
Evidence references
Tool calls
Policy decisions
Approvals
Actions
Verification
```

敏感数据必须脱敏。

## 21. 审计

至少可以回答：

```text
谁触发了 Agent？
Agent 调查了什么？
用了哪些 Evidence？
调用了什么 Tool？
为什么做这个 Decision？
Policy 如何判断？
谁批准了 Action？
实际执行了什么？
最终是否恢复？
```

## 22. 每日运行检查

```text
□ Runtime 健康
□ 模型错误率正常
□ Tool 错误率正常
□ MCP 正常
□ Policy 正常
□ Approval 正常
□ Executor 正常
□ Verification 正常
□ Queue 无异常积压
□ Evidence Store 正常
□ Audit 正常
```

## 23. 生产事故原则

发生故障时优先级：

```text
安全
 ↓
停止错误动作
 ↓
保存状态和证据
 ↓
恢复控制面
 ↓
恢复服务
 ↓
Verification
 ↓
Postmortem
 ↓
Regression Scenario
```
