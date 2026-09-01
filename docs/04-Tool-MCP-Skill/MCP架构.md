# MCP 架构

## 1. MCP 在平台中的定位

MCP（Model Context Protocol，模型上下文协议）用于标准化 Agent 与外部能力提供方之间的工具、资源和提示能力交互。它不是 Agent 本身，也不是权限系统，更不是把所有外部数据直接塞进上下文。

## 2. 推荐架构

```text
                    Agent Runtime
                         │
                  Tool Registry
                         │
                  MCP Client Layer
                         │
        ┌────────────────┼────────────────┐
        ↓                ↓                ↓
   Kubernetes MCP    GitHub MCP      Observability MCP
        │                │                │
        ↓                ↓                ↓
   Kubernetes API    GitHub API     Prometheus / Logs
```

平台内部仍应保留统一 Tool Contract。MCP 是一种接入协议，而不是内部业务模型。

## 3. 为什么不能直接让 Agent 连接所有 MCP Server

直接暴露全部 MCP Server 会造成：

- Context 中工具描述过多
- 权限边界难以管理
- Tool 数量爆炸
- Agent 更容易选错工具
- 不同 MCP Server 错误格式不统一
- 审计入口分散

因此推荐：

```text
MCP Server
 ↓
MCP Adapter
 ↓
Platform Tool Contract
 ↓
Registry / Policy / Audit
 ↓
Agent
```

## 4. MCP Adapter

Adapter 负责协议转换，不负责替 Agent 做业务决策。

主要职责：

1. 建立连接
2. 获取能力列表
3. 转换 Input Schema
4. 调用 MCP Tool
5. 转换结果
6. 统一错误
7. 记录来源

## 5. 多 Agent 复用

多个 Agent 不应该各自维护一套 MCP Client。推荐：

```text
Incident Agent ─┐
RCA Agent ──────┼→ Tool Registry → MCP Adapter
Remediation ────┘
```

这样权限、限流、审计和连接池可以集中治理。

## 6. 权限

MCP Server 声明的能力不能自动获得平台执行权限。最终权限必须由平台 Runtime 决定：

```text
Agent Identity
 + Tool
 + Operation
 + Target
 + Environment
 + Policy
 → Allow / Deny / Approval
```

## 7. MCP 连接故障

连接失败属于依赖故障，不应让 Agent 认为“没有资源”。必须返回明确的 `DEPENDENCY_UNAVAILABLE`。

Agent 可以选择其他证据源，但不能把“工具不可用”解释成“系统正常”。

## 8. MCP Tool Discovery

能力发现应该是按需的：

```text
Incident Type
 ↓
Capability Filter
 ↓
Permission Filter
 ↓
Policy Filter
 ↓
Context Budget Filter
 ↓
Expose Tools
```

## 9. MCP 与 Evidence

MCP 返回的原始数据进入 Evidence 层，Agent Context 只保留必要摘要和 Evidence 引用：

```text
MCP Result
 ↓
Evidence Record
 ↓
Evidence ID
 ↓
Context Summary
```

## 10. 安全边界

MCP Server 不应成为绕过平台 Policy 的旁路。例如 Agent 没有生产重启权限时，不能因为某个 MCP Server 暴露了 `restart_pod` 就直接执行。

## 11. 验收标准

- [ ] MCP 是接入协议，不直接替代内部 Tool Contract。
- [ ] MCP Tool 经过 Registry。
- [ ] MCP Tool 经过权限与 Policy。
- [ ] 多 Agent 可以复用 Adapter。
- [ ] MCP 结果可以进入 Evidence。
- [ ] MCP 故障有明确错误语义。
- [ ] 工具发现受 Context Budget 限制。
