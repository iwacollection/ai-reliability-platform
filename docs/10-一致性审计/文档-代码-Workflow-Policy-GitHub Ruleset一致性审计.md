# 文档 ↔ 实际代码 ↔ Workflow ↔ Policy ↔ GitHub Ruleset 一致性审计

> 审计目标：验证 `docs/` 中描述的架构和生产安全能力，是否真实存在于代码、测试、GitHub Actions、Policy 与 GitHub 仓库保护中。
>
> 审计原则：**文档写了不算实现；代码存在不算接通；测试通过不算生产闭环；Workflow 存在不算 Branch Gate；Policy 类存在不算执行链路已经强制接入。**

## 1. 审计结论

截至本次审计，仓库已经存在较完整的 Agent Runtime、Action、Approval、Policy、MCP、Connector、Evidence、Evaluation 等代码目录，但文档中的“生产级完整闭环”与实际仓库状态仍存在明显差距。

总体判定：

```text
架构代码存在             → 部分通过
Agent Runtime             → 部分通过
Action Execution          → 部分通过
Approval                  → 部分通过
Policy                    → 部分通过
MCP                       → 有代码骨架
Kubernetes Connector      → 有实现目录
Azure Connector           → 文档存在，未发现对应 Azure Connector 实现目录
GitHub Connector          → 文档存在，未发现对应 GitHub Connector 实现目录
Evaluation / Replay       → 有实现
Workflow                  → 有基础质量检查
Policy Rego               → 本次仓库结构中未发现可证明的 Rego Policy 执行链
GitHub Ruleset             → 当前 API 返回空数组，不能证明存在 Ruleset
Branch Protection         → API 读取受到权限限制，无法独立证明已启用
生产级安全闭环           → 尚未达到可证明的 Production Ready
```

最重要的结论：**当前不能把文档中的 Kubernetes / Azure / GitHub / Policy Gate / GitHub Ruleset / Production Ready 描述全部当作“已经落地”。其中一部分是设计规范，一部分是代码能力，一部分仍是待实现或待接线能力。**

## 2. 审计证据边界

本次审计直接检查了 GitHub `main` 分支中的：

```text
README.md
docs/
services/
services/agent_runtime/
services/agent_runtime/app/action/
services/agent_runtime/app/approval/
services/agent_runtime/app/policy/
services/mcp/
services/connectors/
.github/workflows/
```

同时读取了仓库 Rulesets API。

需要特别说明：GitHub Branch Protection API 在当前集成权限下返回 403，因此不能据此断言“没有 Branch Protection”；但是 Rulesets API 明确返回 `[]`，因此当前**没有可观察到的 Repository Ruleset**。

## 3. 文档目录本身的一致性问题

此前设计目标是：

```text
01-架构/
02-Agent运行时/
03-Evidence与RCA/
04-Tool-MCP-Skill/
05-Agent安全/
06-Incident/
07-Evaluation/
08-Connectors/
09-生产运行/
```

但当前 `docs/` 实际仍存在大量顶层 Markdown 文件，同时已经出现部分编号目录。

因此存在：

```text
规划目录
≠
实际目录
```

这不会直接影响代码执行，但会影响：

- 文档导航
- 文档引用
- 自动检查
- 新成员学习路径
- 文档与实现的映射

后续应建立唯一的 Canonical Documentation Tree，避免同一主题同时存在多个版本。

## 4. Architecture ↔ Code

### 文档声明

平台采用：

```text
Gateway
 ↓
Incident
 ↓
Agent Runtime
 ↓
Evidence / Context / Tools
 ↓
Decision
 ↓
Action
 ↓
Policy / Approval
 ↓
Executor
 ↓
Verification
```

### 代码证据

`services/agent_runtime/app` 已经存在多个对应领域：

```text
action
agent
agents
approval
audit
change
contexts
conversation
core
evaluation
events
evolution
policy
```

同时 `services` 下存在：

```text
agent_runtime
connectors
evidence
gateway
harness
mcp
sandbox
simulator
```

### 判定

```text
PASS：架构模块有实际代码落点
```

但这只能证明“模块存在”，不能证明所有模块已经形成端到端运行链路。

## 5. Agent Runtime ↔ Harness ↔ Context

当前 Runtime 目录存在：

```text
services/agent_runtime/harness
services/agent_runtime/memory
services/agent_runtime/skills
services/agent_runtime/app/core/context
services/agent_runtime/app/core/orchestration
services/agent_runtime/app/core/state
```

因此文档中的 Runtime / Context / Memory / Harness 并非完全虚构。

但是生产文档中声明的：

```text
max_iterations
max_runtime
max_tool_calls
max_repeated_same_call
context_budget
```

需要逐项对应真实配置、执行点和测试。当前不能因为目录存在就判定全部已经强制生效。

判定：

```text
PARTIAL
```

## 6. Action Execution ↔ 文档

代码已经存在：

```text
execution_models.py
execution_service.py
execution_store.py
executor.py
kubernetes_production_executor.py
kubernetes_production_factory.py
kubernetes_preflight.py
```

尤其 `ActionExecutionService` 明确采用：

```text
claim
→ execute
→ complete / succeed / fail
→ indeterminate
→ reconcile
```

并明确规定外部执行结果不确定时不能自动 Retry。

这与生产 Runbook 中：

```text
Executor Timeout
→ 查询实际状态
→ Applied / Not Applied / Unknown
```

高度一致。

判定：

```text
PASS（执行状态模型层）
```

但还需要确认真正的生产 Executor 调用前是否始终经过 Approval / Policy，而不是只有 `ActionExecutionService` 自己保持状态安全。

## 7. Approval ↔ Action

实际代码存在：

```text
approval/manager.py
approval/models.py
approval/service.py
approval/store.py
approval/transition_guard.py
```

说明 Approval 不是纯 Markdown 设计，而有真实实现。

但是生产安全要求的是：

```text
Action
 ↓
Policy
 ↓
Approval
 ↓
Fingerprint
 ↓
Executor
```

必须证明 Executor 入口无法绕过 Approval。

当前 `ActionExecutionService.claim()` 接收：

```text
approval_id
operator_id
idempotency_key
action
```

它负责建立执行 Claim，但其自身并没有在该方法中重新执行完整 Policy / Approval 语义判断。

因此：

```text
Approval 数据模型存在       → PASS
执行状态与幂等保护存在       → PASS
“所有 Mutation 强制 Approval” → 尚不能仅凭该 Service 证明
```

判定：

```text
PARTIAL / HIGH PRIORITY
```

## 8. Policy ↔ 实际代码

代码确实存在：

```text
services/agent_runtime/app/policy/
├── base.py
├── engine.py
├── factory.py
├── models.py
├── risk_engine.py
└── rules.py
```

`DefaultHealingPolicy` 已实现：

```text
LOW
→ allowed=true
→ approved=true
→ 不要求人工

MEDIUM
→ allowed=true
→ approved=false
→ require_human=true

HIGH
→ allowed=false
→ require_human=true
```

这证明当前仓库存在真实 Policy Engine，而不是只有文档。

### 关键问题

当前 `PolicyEngine.evaluate()` 遍历 policy 后立即 `return decision`，意味着实际只有第一个 Policy 被评价。

如果未来增加多个 Policy：

```text
Policy A
Policy B
Policy C
```

当前实现不会继续评估 B/C。

因此它目前更像：

```text
Single Default Policy
```

而不是成熟的 Policy Composition Engine。

判定：

```text
PARTIAL
```

## 9. Policy Fail-Closed ↔ 实际执行链

生产文档要求：

```text
Policy unavailable
 ↓
FAIL CLOSED
 ↓
禁止 Mutation
```

代码层面目前能够看到 Policy 默认没有匹配时返回：

```text
allowed=false
approved=false
require_human=true
```

这属于 Fail Closed 的局部实现。

但是还必须验证：

```text
Policy exception
Policy timeout
Policy dependency unavailable
```

是否真的阻断 Executor。

“默认返回 deny”与“Policy 服务故障时 Mutation 一定不能执行”是两个不同问题。

判定：

```text
PARTIAL
```

## 10. Rego / OPA 一致性

用户要求的审计标准明确包括：

```text
Policy Rego
```

本次检查到的 Runtime Policy 是 Python：

```text
services/agent_runtime/app/policy/*.py
```

当前没有足够仓库证据证明存在并执行独立的：

```text
OPA
Rego
Policy Bundle
Policy Decision Service
```

因此如果其他文档声称“已经使用 Rego Policy Gate”，必须降级为：

```text
DESIGN / NOT VERIFIED
```

不能写成已生产落地能力。

## 11. MCP ↔ 实际代码

`services/mcp` 实际存在：

```text
adapters
client
federation
```

说明 MCP 不是纯概念文档。

但是文档中的：

```text
Tool Discovery
Schema
Context Isolation
Permission
Audit
Error Contract
```

需要逐项检查实现和测试。

目前可以判定：

```text
MCP architecture exists
≠
All external tools are MCP-backed
```

判定：

```text
PARTIAL
```

## 12. Connector ↔ 实际代码

当前 `services/connectors` 目录可观察到：

```text
kubernetes/
```

因此 Kubernetes Connector 有真实代码目录。

但本次仓库结构检查没有看到对应：

```text
services/connectors/azure/
services/connectors/github/
```

因此前面的 Azure Connector.md 与 GitHub Connector.md 应明确标记为：

```text
Architecture / Target Design
```

而不能写成已经完成的真实 Connector。

判定：

```text
Kubernetes → PARTIAL / IMPLEMENTED AREA
Azure      → NOT VERIFIED
GitHub     → NOT VERIFIED
```

## 13. Evidence ↔ Connector

仓库存在：

```text
services/evidence
services/connectors/kubernetes
```

说明 Evidence 与 Connector 都有独立领域。

但生产级要求：

```text
Connector
 ↓
Normalize
 ↓
Evidence
 ↓
Provenance
 ↓
Context Projection
```

必须进一步验证实际调用关系，而不能仅凭两个目录判断已经接通。

判定：

```text
PARTIAL
```

## 14. Evaluation ↔ Replay

`services/agent_runtime/app/evaluation` 存在真实代码目录，仓库同时存在 Scenario / Replay 相关实现和测试资源。

因此：

```text
Evaluation concept → implemented area
```

但 Production Ready 要求的：

```text
Baseline
vs
Candidate
↓
固定 Scenario Dataset
↓
Regression Replay
↓
Gate
```

必须由 CI 明确执行，才可以成为发布门禁。

当前 `.github/workflows/enterprise-validation.yml` 主要执行：

```text
ruff
mypy
pytest
pip-audit
```

没有看到明确的：

```text
Scenario Replay Gate
Benchmark Gate
Badcase Regression Gate
```

判定：

```text
Evaluation implementation → PARTIAL
CI release gate → NOT VERIFIED
```

## 15. GitHub Actions ↔ 文档

当前有三个 Workflow：

```text
enterprise-validation.yml
reusable-python-validation.yml
terraform-validation.yml
```

### Enterprise Validation

实际执行：

```text
Checkout
Python 3.12
uv sync
ruff
mypy
pytest + coverage
pip-audit
```

这能证明仓库具有基础 CI 质量门禁能力。

### Reusable Python Validation

存在 reusable workflow，但当前审计到的仓库结构中没有证明它已经被主 CI 广泛调用。

### Terraform Validation

该 Workflow 针对：

```text
infra/**
terraform/**
```

并执行 Terraform Validate / Plan。

**这与本 Agent 仓库的核心 Runtime 文档没有直接关系。**

因此 Agent 文档不能引用该 Workflow 作为 Agent 安全 Gate。

判定：

```text
CI quality validation → PASS
Agent-specific release gate → NOT VERIFIED
```

## 16. 一个非常重要的问题：Terraform Workflow

当前仓库确实存在 Terraform Validation Workflow。

但本项目文档定位是 Agent Reliability Platform。

因此必须明确：

```text
Terraform Workflow
≠
Agent Production Safety Gate
```

后续应决定：

1. 如果 Terraform/IaC 已经不是本仓库职责，应删除或迁移；
2. 如果确实属于仓库基础设施，应放入独立 Infrastructure 文档，并与 Agent Runtime 文档解耦。

不能再次出现：

```text
Agent 文档
→ 推导 IaC Production Workflow
```

## 17. GitHub Ruleset ↔ 文档

本次直接读取仓库 Rulesets API：

```text
GET /repos/iwacollection/ai-reliability-platform/rulesets
```

返回：

```text
[]
```

因此当前没有可观察到的 Repository Ruleset。

这意味着如果文档写：

```text
main 已通过 GitHub Ruleset 强制保护
```

目前不能作为事实成立。

正确状态应为：

```text
GitHub Ruleset → NOT VERIFIED / NOT OBSERVED
```

## 18. Branch Protection

尝试读取：

```text
/repos/iwacollection/ai-reliability-platform/branches/main/protection
```

当前 GitHub 集成返回 403。

因此不能据此证明：

```text
有 Branch Protection
```

也不能证明：

```text
没有 Branch Protection
```

必须由仓库管理员在 GitHub UI / GitHub 管理 API 中确认。

## 19. Workflow 存在 ↔ Workflow 真正保护 main

这是一个必须纠正的认知：

```text
Workflow 文件存在
≠
Workflow 是 Required Check
```

例如当前：

```text
Enterprise Validation
```

即使 PR 会触发 Workflow，也不代表：

```text
PR 一定不能绕过它合并
```

真正需要证明的是：

```text
GitHub Ruleset / Branch Protection
 ↓
Required Status Check
 ↓
Enterprise Validation / Quality Gate
 ↓
Merge blocked when failed
```

当前缺少这一层可验证证据。

## 20. 文档 ↔ 测试

目前测试目录真实存在，且历史验证已经证明 Runtime / Approval / Action 等存在测试。

但测试通过只能证明测试场景通过。

例如：

```text
test_policy_low_risk
```

不能自动证明：

```text
生产 Executor 永远无法绕过 Policy
```

要证明后者，必须存在集成测试：

```text
Unauthorized Action
→ Executor
→ MUST DENY
```

以及：

```text
Policy unavailable
→ Executor
→ MUST NOT EXECUTE
```

## 21. 最关键的安全闭环验收矩阵

| 能力 | 文档 | 代码 | 测试 | Workflow | GitHub Ruleset | 当前判定 |
|---|---|---|---|---|---|---|
| Agent Runtime | 有 | 有 | 有部分 | 有基础 CI | 未证明 | PARTIAL |
| Action Execution | 有 | 有 | 有 | 基础测试 | 未证明 | PARTIAL |
| Approval | 有 | 有 | 有 | 基础 CI | 未证明 | PARTIAL |
| Policy | 有 | 有 | 有部分 | 未见专门 Gate | 未证明 | PARTIAL |
| Rego/OPA | 有设计诉求 | 未证明 | 未证明 | 未证明 | 不适用 | NOT VERIFIED |
| MCP | 有 | 有 | 部分 | 基础 CI | 未证明 | PARTIAL |
| Kubernetes Connector | 有 | 有目录 | 需补强 | 未见专门 Gate | 未证明 | PARTIAL |
| Azure Connector | 有 | 未发现对应实现目录 | 未证明 | 未证明 | 未证明 | NOT VERIFIED |
| GitHub Connector | 有 | 未发现对应实现目录 | 未证明 | 未证明 | 未证明 | NOT VERIFIED |
| Evidence | 有 | 有 | 部分 | 基础 CI | 未证明 | PARTIAL |
| Evaluation | 有 | 有 | 有 | 未见 Regression Gate | 未证明 | PARTIAL |
| Scenario Replay | 有 | 有 | 有部分 | 未见 Gate | 未证明 | PARTIAL |
| Production Runbook | 有 | 部分 | 不适用 | 不适用 | 不适用 | DOCUMENTED |
| Production Acceptance | 有 | 部分 | 部分 | 未形成 Release Gate | 未证明 | PARTIAL |

## 22. 当前最高优先级问题

### P0：GitHub Merge Gate 未形成可证明闭环

目标：

```text
PR
 ↓
Required CI
 ↓
Policy / Security / Regression
 ↓
GitHub Ruleset
 ↓
Merge Allowed / Blocked
```

当前 Ruleset API 返回空数组。

### P0：Policy 是否真正挡住 Executor 尚未完成证明

必须建立：

```text
Policy Allow
Policy Deny
Policy Timeout
Policy Error
Approval Missing
Approval Expired
Fingerprint Changed
```

到 Executor 的集成测试。

### P1：文档中 Azure / GitHub Connector 与代码状态不一致

必须二选一：

```text
真正实现 Connector
```

或者：

```text
文档明确标记为 Target Architecture / Planned
```

### P1：Scenario Replay 尚未成为 CI Regression Gate

当前存在 Evaluation 能力，但没有足够证据证明：

```text
历史 Badcase
 ↓
自动 Replay
 ↓
失败阻止发布
```

### P1：Agent Runtime 安全参数需要配置化并测试

必须将：

```text
max_iterations
max_runtime
max_tool_calls
context_budget
loop guard
```

变成明确配置，并在测试中验证超过阈值一定停止。

## 23. 推荐最终安全链

仓库最终应该达到：

```text
Agent
 ↓
Tool Registry
 ↓
Schema Validation
 ↓
Permission
 ↓
Resource Scope
 ↓
Evidence
 ↓
Decision
 ↓
Action Plan
 ↓
Risk Classification
 ↓
Policy Gate
 ↓
Approval
 ↓
Action Fingerprint
 ↓
Execution Claim
 ↓
Executor
 ↓
Verification
 ↓
Audit
```

其中任何一个安全节点失败：

```text
FAIL CLOSED
```

## 24. 最终 CI/CD Gate

建议最终把 Agent 仓库的发布门禁设计为：

```text
                    PR
                     ↓
              ┌──────────────┐
              │ Static Check │
              └──────┬───────┘
                     ↓
               Unit / Type
                     ↓
             Integration Tests
                     ↓
             Security Tests
                     ↓
             Policy Tests
                     ↓
          Approval Bypass Tests
                     ↓
             Scenario Replay
                     ↓
           Regression Dataset
                     ↓
              Benchmark Gate
                     ↓
             GitHub Ruleset
                     ↓
             Required Checks
                     ↓
                MERGE
```

## 25. 审计规则

以后所有新增文档都必须避免以下写法：

```text
“系统支持 X”
```

除非同时能指出：

```text
实现文件
→ 调用入口
→ 测试
→ CI
→ 生产约束
```

推荐改成：

```text
状态：IMPLEMENTED
代码：xxx
测试：xxx
CI：xxx
生产约束：xxx
```

或者：

```text
状态：DESIGN / PLANNED
```

## 26. 最终判定

当前仓库不是“什么都没有”，恰恰相反，已经有相当多 Runtime / Action / Approval / Policy / MCP / Evaluation 的真实代码。

问题在于：

```text
模块存在
```

与：

```text
模块真正串起来
```

以及：

```text
真正串起来
```

与：

```text
生产环境无法绕过
```

是三件完全不同的事情。

本次审计的下一阶段不应该继续堆文档，而应该优先把以下链路做成可测试、可阻断、可证明：

```text
Policy
 ↓
Permission
 ↓
Approval
 ↓
Fingerprint
 ↓
Executor
 ↓
Verification
 ↓
Audit
```

以及：

```text
PR
 ↓
CI
 ↓
Regression
 ↓
Security
 ↓
GitHub Ruleset
 ↓
Merge
```

只有这两条链真正落地，`Production Ready` 才能从文档描述变成仓库事实。
