# Skill 系统设计

## 1. Skill 是什么

Skill 是面向任务的可复用工作方法，而不是一个具体 API。它描述“遇到什么问题时，如何组织目标、证据、工具和验证”。

```text
Tool = 能做什么
Skill = 怎么做一类事情
Agent = 当前由谁决定下一步
Harness = 谁约束 Agent
```

## 2. Skill 与 Tool

例如 Kubernetes CPU 事故：

```text
Skill: Kubernetes CPU Investigation
  ├── get_pod
  ├── get_container_metrics
  ├── get_events
  ├── get_logs
  └── compare_deployment
```

Skill 不应该直接实现 Kubernetes API；它引用 Tool 能力。

## 3. Skill 结构

建议包含：

```text
name
version
purpose
trigger_conditions
required_context
allowed_tools
investigation_steps
exit_conditions
safety_constraints
verification_requirements
failure_handling
examples
```

## 4. Skill 不应该写死 Agent

Skill 可以提供推荐调查路径，但不能强制 Agent 无视实际 Evidence。

例如：

```text
默认：先查 CPU → 再查 Pod → 再查日志
```

如果 CPU 正常而 OOM 已被确认，Agent 可以跳过 CPU 深挖，直接进入内存证据调查。

## 5. Skill 调用流程

```text
Incident Classification
 ↓
Skill Discovery
 ↓
Skill Eligibility Check
 ↓
Tool Capability Resolution
 ↓
Agent Execution
 ↓
Evidence Collection
 ↓
Verification
```

## 6. Eligibility

Skill 激活至少检查：

- Incident 类型
- 资源类型
- 当前环境
- Agent 身份
- 所需工具是否可用
- 所需 Evidence 是否存在
- 风险级别

## 7. 安全

Skill 不能提升权限。Skill 中即使写了“重启生产 Pod”，也必须经过 Tool Authorization、Policy 和 Approval。

## 8. Skill 版本

Skill 是生产行为的一部分，必须版本化。Incident 运行记录应保存使用的 Skill 版本，以保证历史结果可复现。

## 9. Skill 与 MCP

Skill 不关心底层 Tool 来自本地 Python、HTTP API 还是 MCP Server：

```text
Skill
 ↓
Logical Tool
 ↓
Registry
 ├── Local Executor
 ├── HTTP Adapter
 └── MCP Adapter
```

这样可以替换底层工具而不重写 Skill。

## 10. Skill 与 Evaluation

每个重要 Skill 都应该有 Scenario Replay：

```text
Scenario
 ↓
Skill
 ↓
Agent
 ↓
Tool Calls
 ↓
Evidence
 ↓
Expected Assertions
```

验证的不只是最终答案，还包括是否调用了合理工具、是否越权、是否在证据不足时错误执行 Action。

## 11. Skill 失败

Skill 找不到所需工具时不能伪造结果，应进入：

```text
SKILL_UNAVAILABLE
 ↓
Alternative Investigation
 ↓
或
 ↓
Human Escalation
```

## 12. Skill 注册

Registry 可以保存 Skill Metadata，但运行时仍需要做版本解析、权限过滤和 Tool Resolution。

## 13. 验收标准

- [ ] Skill 与 Tool 明确分层。
- [ ] Skill 可版本化。
- [ ] Skill 不可提升权限。
- [ ] Skill 可以引用 MCP / HTTP / Local Tool。
- [ ] Skill 失败不会伪造证据。
- [ ] 关键 Skill 有 Scenario Replay。
- [ ] Incident 记录 Skill 版本。
