# 实现对照指南

> 本文把“架构设计”和“当前源码”放到同一张地图上。目标不是描述理想中的 AI Reliability Platform，而是回答：一个设计为什么存在、当前由哪些代码承载、一次请求如何流动、哪些能力已经有代码和测试支撑、哪些仍属于目标设计。
>
> **阅读原则：** 本文优先记录仓库中可以确认的实现；尚未从源码确认或尚未完整落地的能力明确标记为“目标设计”。不要把架构文档中的目标能力误认为当前已经实现。

---

## 1. 先建立一张总地图

当前平台可以理解为下面这条链路：

```text
外部事件
  │
  ▼
Gateway
  │  RawEvent
  ▼
Parser / ParserRegistry
  │  StandardEvent
  ▼
Agent Runtime
  │
  ├── Agent Context
  ├── Agent / Pipeline
  ├── LLM Gateway
  └── Decision / Investigation
  │
  ▼
Evidence
  │
  ▼
Action / Approval / Execution
  │
  ▼
Verification
  │
  ▼
Incident Result
```

仓库同时包含一组用于可靠性验证和能力扩展的边界：

```text
Memory
Skills
MCP / Tools
Harness
Sandbox
Simulation / Replay
Evaluation
Connectors
Cloud adapters
Common domain models
```

这些组件的共同目的不是“让 Agent 更聪明”，而是把模型的不确定性限制在可验证的边界内。

---

## 2. 状态边界：先看数据，而不是先看模型

一个可靠性 Agent 最重要的不是 Prompt，而是状态边界。

建议把一次 Incident 看成：

```text
Raw Event
   ↓
Standard Event
   ↓
Incident State
   ↓
Evidence State
   ↓
Decision
   ↓
Action State
   ↓
Verification State
```

其中每一步都应该有明确的数据契约。

### 2.1 RawEvent

RawEvent 表示外部系统刚刚送进来的原始事件。

它的价值是保留原始事实，不让不同监控系统的数据格式直接污染 Agent Runtime。

例如 AlertManager 的 webhook 不应该直接成为 Agent 的输入对象，而应该先经过解析。

### 2.2 StandardEvent

Gateway 的 Parser 将不同来源转换成统一事件模型。

当前仓库已经可以确认：`BaseParser` 定义了 `parse(RawEvent) -> StandardEvent` 的抽象接口。fileciteturn30file0

因此架构边界是：

```text
AlertManager / 其他来源
        ↓
     RawEvent
        ↓
     BaseParser
        ↓
  StandardEvent
```

这样 Agent Runtime 不需要知道 AlertManager webhook 的字段结构。

---

## 3. Gateway：外部世界与内部领域模型之间的适配层

### 3.1 为什么需要 Gateway

生产环境通常存在多个事件来源：

```text
Prometheus / AlertManager
日志平台
云平台事件
Kubernetes Event
消息队列
ChatOps
人工创建 Incident
```

如果每个 Agent 自己解析来源，会迅速出现：

```text
Agent A → 解析 AlertManager
Agent B → 解析另一种 webhook
Agent C → 又自己解析
```

结果是领域模型无法统一，测试也很困难。

Gateway 的职责应该是：

```text
接收
 → 校验
 → 保存原始事件
 → 选择 Parser
 → 标准化
 → 交给 Runtime
```

### 3.2 ParserRegistry

当前代码中已经存在 `ParserRegistry`，内部维护名称到 Parser 的映射，并通过 `register()` 和 `get()` 完成注册和查找。fileciteturn31file0

这解决的是**扩展性问题**：

```text
if source == "alertmanager":
    ...
elif source == "xxx":
    ...
elif source == "yyy":
    ...
```

这种条件分支会随着来源增加不断膨胀。

注册表模式变成：

```text
ParserRegistry
 ├── alertmanager → AlertManagerParser
 ├── xxx          → XxxParser
 └── yyy          → YyyParser
```

### 3.3 AlertManagerParser 当前实现

当前 `AlertManagerParser` 从 webhook 的 alerts 中读取第一条 alert，提取 labels、summary、severity、pod、namespace、cluster 等信息，并组装 `Signal`、`Resource`、`Header`，最终返回 `StandardEvent`。fileciteturn32file0

这里有一个非常值得注意的实现边界：

```text
当前实现：取 alerts[0]
```

这意味着它目前并不是完整的 AlertManager 批量事件聚合器。

因此文档中不能把它描述成“已经完整支持 AlertManager 全量语义”。更准确的说法是：

> 当前已经存在 AlertManager → StandardEvent 的基础适配实现；多 Alert 聚合、去重、事件幂等、完整 webhook 校验等属于后续生产化增强项。

---

## 4. Agent Runtime：为什么不是一个大类

Agent Runtime 的设计目标是把以下职责分开：

```text
事件上下文
    ↓
Agent
    ↓
模型决策
    ↓
工具调用
    ↓
证据
    ↓
动作
    ↓
验证
```

不要设计成：

```text
Agent.run()
  ├── 调 LLM
  ├── 查 Kubernetes
  ├── 改 Deployment
  ├── 发消息
  └── 判断是否成功
```

因为这种“大类”最终会同时承担：

- 状态管理
- Prompt
- 工具执行
- 权限
- 重试
- 审批
- 业务逻辑
- 测试

最后几乎无法维护。

---

## 5. Agent 与 Pipeline：为什么保留编排边界

当前仓库已有 Agent / Pipeline 的实现思路：Agent 接收统一事件上下文，Pipeline 负责按顺序执行 Agent。

其核心思想是：

```text
Pipeline
  ↓
Agent 1
  ↓
Agent 2
  ↓
Agent 3
```

而不是让 Agent 之间互相直接调用。

这样做解决两个问题：

### 问题一：执行顺序不可控

如果 Agent 自己调用 Agent：

```text
A → B → C → A → B → ...
```

很容易产生隐式循环。

Pipeline 可以提供明确的边界。

### 问题二：测试困难

Pipeline 可以单独验证：

```text
输入
 → Agent A
 → 输出
 → Agent B
 → 输出
```

从而把 Agent 行为测试从完整系统中拆出来。

> 当前实现与未来自主 Agent Loop 并不是冲突关系。Pipeline 更适合确定性的阶段编排；自主调查则应该由 Runtime 根据状态和证据决定下一步。

---

## 6. AgentContext：让 Agent 不直接依赖 HTTP / Kubernetes / 数据库

Agent 不应该直接读取 FastAPI request，也不应该把 Kubernetes client 塞进每一个 Agent。

更合理的边界是：

```text
Gateway
  ↓
Domain Event
  ↓
AgentContext
  ↓
Agent
```

Agent 只关注：

```text
Incident 是什么？
目标是什么？
当前有什么 Evidence？
当前状态是什么？
```

而不是：

```text
HTTP header 是什么？
数据库连接怎么拿？
Kubernetes token 在哪里？
```

这样可以把 Agent 从基础设施实现中解耦出来，也方便使用 Mock Provider 和 Scenario Replay 做离线测试。

---

## 7. LLM Gateway：为什么模型不能直接散落在 Agent 中

Agent 不应该写成：

```text
Agent
  ↓
OpenAI client
```

否则以后切换：

```text
OpenAI
Azure OpenAI
Anthropic
本地模型
测试 Mock
```

都需要修改业务 Agent。

推荐边界：

```text
Agent
  ↓
LLM Gateway
  ↓
Provider
```

这样可以统一处理：

- 模型选择
- 超时
- 重试
- Token 统计
- 成本统计
- 结构化输出
- Provider fallback
- Trace

当前项目已经存在 `llm_sdk` / LLM Gateway 方向的模块，并在 Agent Runtime 测试中使用 Mock Provider 验证决策逻辑。

需要注意：**Provider 抽象存在不等于生产级多模型容灾已经完成。** 多 Provider 自动故障切换、成本路由、模型级限流属于目标设计。

---

## 8. Agent 如何“主动发现问题”

这里必须把概念讲清楚。

主动发现不是：

```text
Prompt：
请主动寻找更多问题。
```

真正可工程化的链路是：

```text
Incident Objective
        ↓
Current Evidence
        ↓
比较“已经知道什么”和“还需要知道什么”
        ↓
Evidence Gap
        ↓
选择下一步调查动作
        ↓
Tool
        ↓
新 Evidence
        ↓
重新判断
```

例如：

```text
告警：Pod CPU 92%
```

Agent 不能直接：

```text
CPU 高 → 扩容
```

因为至少还缺：

```text
CPU 趋势
CPU throttling
request / limit
流量变化
最近发布
错误率
```

所以 Runtime 应该把调查变成：

```text
缺少 CPU throttling 证据
        ↓
查询 metrics
        ↓
得到 Evidence
        ↓
更新假设
```

这是“主动性”的工程基础。

---

## 9. Tool：模型只能提出意图，Runtime 才决定是否执行

推荐完整调用链：

```text
LLM
 ↓
结构化 Tool Call
 ↓
Schema 校验
 ↓
语义校验
 ↓
目标校验
 ↓
权限校验
 ↓
风险策略
 ↓
Approval（如果需要）
 ↓
Executor
 ↓
Tool Result
 ↓
Evidence
```

这条链解决的是最核心的 Agent 安全问题：

> **模型输出不是可信指令。**

例如模型输出：

```json
{
  "tool": "restart_deployment",
  "namespace": "production",
  "deployment": "payment-api"
}
```

不能直接执行。

Runtime 还必须判断：

```text
参数合法吗？
目标存在吗？
Incident 是否允许操作它？
当前身份有权限吗？
生产环境是否必须审批？
这个动作是否幂等？
```

---

## 10. MCP：协议层，不应该变成上下文垃圾场

MCP 的合理边界是：

```text
Agent Runtime
    ↓
Tool Registry / MCP Client
    ↓
MCP Server
    ↓
实际系统
```

而不是：

```text
MCP Server
  ↓
把几十个工具定义和全部结果
  ↓
一次性塞给 LLM
```

工具应该按需发现或按场景暴露。

结果也应该经过：

```text
Tool Result
 ↓
Normalize
 ↓
Evidence
 ↓
Context Builder
```

而不是把原始 JSON 永久留在对话上下文里。

> 当前仓库具有 MCP / Tools 的架构方向；MCP Server 的生产级动态发现、租户隔离、工具级授权和大规模工具目录治理应视为持续建设能力，不能仅凭存在目录就认定全部已经落地。

---

## 11. Evidence：为什么它是整个系统的“事实层”

Agent 的自然语言不是事实。

Tool Result 也不应该直接成为最终结论。

应该经过：

```text
外部系统
 ↓
Tool
 ↓
Evidence
 ↓
Hypothesis
 ↓
Decision
```

Evidence 至少应该能够表达：

```text
事实内容
来源
时间
目标
查询条件
证据类型
可信度
关联 Incident
```

例如：

```text
Evidence E123

来源：Prometheus
目标：payment-api
时间：12:01 ~ 12:10
事实：CPU 持续 > 90%
查询：container_cpu_usage...
```

这样模型后面即使压缩 Context，也仍然可以保留：

```text
E123 = CPU 持续高
```

而不是把完整 Prometheus 返回值永远留在 Prompt 中。

---

## 12. Action / Approval：为什么执行和决策必须分开

推荐：

```text
Decision
  ↓
Action Proposal
  ↓
Policy
  ↓
Approval
  ↓
Action Executor
```

而不是：

```text
LLM
 ↓
直接 Shell
 ↓
生产环境
```

当前项目已经存在 Approval / Action Runtime / Scenario Replay 等能力方向，并已有对应测试验证基础流程。

它们解决的是一个非常现实的问题：

```text
Agent 可以调查
≠
Agent 可以修改生产
```

因此权限边界应该至少分成：

```text
只读调查
    ↓
低风险动作
    ↓
需要审批的动作
    ↓
高风险 / 禁止动作
```

---

## 13. Sandbox：为什么 Agent 不能拥有无限制 Shell

如果 Agent 能够：

```text
任意 Shell
任意网络
任意文件
任意凭据
```

那么整个 Agent Runtime 的安全边界实际上等于零。

Sandbox 应该限制：

```text
文件系统
网络
进程
资源
凭据
执行时间
命令白名单
```

目标执行模型：

```text
Agent
 ↓
受控 Action
 ↓
Sandbox
 ↓
实际命令
```

即使模型被 Prompt Injection 欺骗，也应该有第二道边界阻止危险操作。

---

## 14. Harness：为什么需要一个“控制器”

Agent Loop 很容易出现：

```text
重复调用
工具爆炸
上下文无限增长
一直重新调查
```

Harness 的职责就是控制这些边界。

建议的执行预算：

```text
最大轮数
最大 Tool Call 数
最大 Token
最大运行时间
最大 Retry
最大 Action 数
最大成本
```

同时使用：

```text
Repetition Guard
Progress Guard
Timeout
Stop Condition
```

因此：

```text
LLM = 决策者
Harness = 运行时监管者
```

这也是为什么一个真正可靠的 Agent 系统不能只是“一个 ReAct Prompt”。

---

## 15. Memory：不要把数据库变成 Prompt

Memory 应该解决：

```text
当前 Incident 需要知道哪些历史经验？
```

而不是：

```text
把所有历史 Incident 发给模型。
```

推荐：

```text
长期历史
   ↓
检索
   ↓
相关 Memory
   ↓
Context Builder
   ↓
LLM
```

同时保留当前 Incident 的短期状态：

```text
当前假设
当前 Evidence
最近工具结果
当前 Action
当前 Approval
```

这样可以同时解决：

```text
记忆不足
记忆过多
Context 爆炸
历史噪声
```

> 当前仓库存在 Memory 能力的架构方向；如果某个具体 Memory backend 尚未与 Runtime 完成生产级持久化闭环，应标记为目标设计，而不是把目录存在误认为完整功能。

---

## 16. Context Builder：Context 是“工作集”

正确理解：

```text
Incident Store
     ↓
Memory Retrieval
     ↓
Evidence Selection
     ↓
Current State
     ↓
Context Builder
     ↓
LLM
```

优先级建议：

```text
系统策略       永久保留
当前目标       永久保留
当前状态       高优先级
关键 Evidence  高优先级
最近结果       中优先级
历史 Memory    按需检索
旧原始结果     摘要 / 引用
```

当 Context 快满时：

```text
提取事实
 ↓
去重
 ↓
压缩
 ↓
保留 Evidence 引用
 ↓
删除原始大结果
```

核心原则：

> 可以删除原始数据，但不能删除支撑当前判断所需的事实。

---

## 17. Retry / Fallback / Checkpoint：不要让失败重新从零开始

一次 Agent Investigation 可能经历：

```text
Tool A 成功
Tool B 超时
Tool C 未授权
LLM Provider 暂时失败
```

如果整个 Incident 从头重跑，会产生：

- 重复查询
- 重复动作
- 成本增加
- 状态混乱

因此目标运行模型应该是：

```text
Checkpoint
   ↓
失败
   ↓
判断失败类型
   ├── 临时错误 → Retry
   ├── Provider 错误 → Fallback
   ├── 权限错误 → Stop / Escalate
   ├── 参数错误 → Repair / Replan
   └── 状态错误 → Reconcile
```

其中最重要的是：

```text
Retry 不是重新执行一切。
Retry 应该针对失败边界。
```

对于有副作用的 Action，还需要幂等键：

```text
incident_id + action_type + target + operation_id
```

防止 Agent 重试导致重复执行。

> Checkpoint、完整运行时恢复和跨进程故障恢复属于生产化增强项，不能仅凭已有测试场景就宣称已经具备完整灾备能力。

---

## 18. Verification：执行成功不等于 Incident 恢复

这是平台与普通自动化脚本最大的区别之一。

例如：

```text
Action：restart deployment
```

命令返回：

```text
success
```

只能说明：

```text
重启请求被接受
```

不能说明：

```text
CPU 恢复
错误率下降
Pod Ready
业务恢复
```

所以必须：

```text
Action
 ↓
Wait / Observe
 ↓
Evidence
 ↓
Verification
 ↓
Resolved / Not Resolved
```

这也是为什么平台的 Incident 状态不能只依赖 Tool Result。

---

## 19. Scenario Replay：为什么需要“离线生产事故”

Agent 系统不能只靠人工点几次测试。

应该能够把事故保存成：

```text
Scenario
 ├── 初始 Incident
 ├── Evidence
 ├── 可用工具
 ├── 环境状态
 ├── 期望调查路径
 ├── 允许动作
 └── 验证条件
```

然后：

```text
Scenario
 ↓
Replay
 ↓
Agent Runtime
 ↓
Trace
 ↓
Assertion
 ↓
Evaluation Report
```

这样可以验证：

```text
同一个事故
模型升级后是否退化？
Prompt 修改后是否退化？
Tool Schema 修改后是否退化？
Runtime 修改后是否退化？
```

这比单纯测试：

```text
函数输入 → 函数输出
```

更接近 Agent 的真实质量。

---

## 20. Evaluation：评价的不应该只是最终答案

普通 LLM 评测：

```text
问题 → 答案
```

Reliability Agent 应该评价：

```text
Incident
 ↓
Investigation Trace
 ↓
Evidence
 ↓
Decision
 ↓
Action
 ↓
Verification
```

至少关注：

```text
是否找到正确证据
是否避免无证据结论
是否选择正确工具
是否越权
是否进行了不必要的动作
是否成功恢复
是否正确停止
```

所以评价对象是：

> **行为轨迹，而不只是最终文本。**

---

## 21. 测试体系如何对应源码

建议把测试拆成四层：

```text
第一层：单元测试
    ↓
Parser / Model / Registry / Policy

第二层：组件测试
    ↓
Agent / Approval / Executor / Tool

第三层：Runtime 测试
    ↓
Incident → Agent → Evidence → Action → Verification

第四层：Scenario Replay
    ↓
完整事故轨迹
```

### 21.1 单元测试解决什么

验证：

```text
输入契约是否正确
错误是否正确抛出
边界值是否正确
```

例如 Parser Registry：

```text
register(name, parser)
get(name)
unknown parser → error
```

### 21.2 Agent 测试解决什么

使用 Mock Provider：

```text
固定输入
 ↓
固定模型行为
 ↓
验证 Agent 是否正确解释模型输出
```

这样测试不依赖真实 LLM 的随机性。

### 21.3 Runtime 测试解决什么

验证：

```text
状态是否正确流转
Approval 是否挡住危险动作
Action 后是否 Verification
失败是否进入正确状态
```

### 21.4 Replay 测试解决什么

验证完整事故：

```text
输入事件
 → 调查
 → 证据
 → 决策
 → 动作
 → 验证
```

这类测试最适合发现“单个函数都正确，但组合起来错误”的问题。

---

## 22. 当前实现与目标设计的边界

为了防止项目文档产生“过度宣传”，建议长期保持下面三种标签。

### 已实现

满足至少一个条件：

```text
源码存在
且
存在可执行路径
且/或
存在测试验证
```

### 部分实现

```text
基础能力已经存在
但生产能力不完整
```

例如：

```text
AlertManager → StandardEvent
```

已经有基础 Parser，但完整 webhook 安全、批量处理、幂等等还可以继续增强。

### 目标设计

```text
架构已经定义
但源码尚未形成完整可运行闭环
```

例如：

```text
多 Provider 自动故障切换
跨进程 Checkpoint 恢复
完整多租户 MCP 权限
生产级 Memory backend
```

这样项目在面试或技术评审中会更加可信。

---

## 23. 一次 CPU Incident 的源码级理解路径

以：

```text
Pod CPU High
```

为例，可以按下面方式理解源码调用链：

```text
AlertManager webhook
        ↓
Gateway endpoint
        ↓
RawEvent
        ↓
ParserRegistry.get("alertmanager")
        ↓
AlertManagerParser.parse()
        ↓
StandardEvent
        ↓
AgentContext
        ↓
Agent / Pipeline
        ↓
Noise / Classification
        ↓
LLM Gateway
        ↓
调查决策
        ↓
Tool
        ↓
Evidence
        ↓
新的调查决策
        ↓
Action Proposal
        ↓
Approval
        ↓
Action Runtime
        ↓
Verification
        ↓
Incident Result
```

需要注意：上图是**完整目标链路**。当前仓库已经有其中多个组件和测试，但不能因为每个组件分别存在，就宣称整条生产闭环已经完全打通。

---

## 24. 当前代码阅读顺序

如果第一次进入仓库，不建议从最复杂的 Agent 代码开始。

推荐顺序：

```text
① common/domain
   ↓
   先理解 StandardEvent / RawEvent / Domain Model

② gateway/parser
   ↓
   理解外部事件如何进入系统

③ agent_runtime/context
   ↓
   理解 Agent 的输入状态

④ agent_runtime/agents
   ↓
   理解 Agent 如何做决策

⑤ llm_sdk / gateway
   ↓
   理解模型如何被隔离

⑥ tools / mcp / skills
   ↓
   理解 Agent 如何获得能力

⑦ evidence
   ↓
   理解事实如何沉淀

⑧ action / approval / executor
   ↓
   理解为什么不能直接执行

⑨ harness
   ↓
   理解 Agent 如何被限制

⑩ replay / evaluation
   ↓
   理解如何验证整个 Agent
```

这样读源码的核心不是“每个文件都看一遍”，而是追踪：

> **数据从哪里来 → 被谁改变 → 为什么改变 → 最终由谁消费。**

---

## 25. 源码阅读时最应该问的 12 个问题

每看到一个模块，都问：

```text
1. 它的输入是什么？
2. 它的输出是什么？
3. 谁调用它？
4. 它调用谁？
5. 状态在哪里保存？
6. 失败怎么处理？
7. 是否可以重复执行？
8. 是否有副作用？
9. 是否需要权限？
10. 是否需要审批？
11. 是否有测试？
12. 它是已实现能力还是目标设计？
```

这 12 个问题基本可以覆盖生产系统设计评审的大部分关键点。

---

## 26. 面试时如何解释这个项目

不要说：

> “我们做了一个调用大模型的 SRE Agent。”

更准确的表达是：

> “我们把大模型放在一个受约束的 Reliability Runtime 里面。外部告警先通过 Gateway 标准化成统一事件，然后 Runtime 根据 Incident 状态组织 Evidence 和上下文，让 Agent 决定下一步调查；模型只能提出结构化意图，工具调用还要经过参数、目标、权限和风险策略校验，高风险动作进入人工审批，执行后再通过 Evidence 做恢复验证。与此同时，我们通过 Harness 控制 Agent 的轮数、工具调用、超时和停止条件，通过 Replay 和 Evaluation 对完整调查轨迹做回归验证。”

这段话比“用了 LangChain / MCP / RAG”更能体现工程能力。

---

## 27. 当前项目最重要的工程思想

最终可以把整个项目压缩成下面这张图：

```text
                 ┌───────────────┐
                 │      LLM      │
                 │   决策能力     │
                 └───────┬───────┘
                         │ 提出意图
                         ▼
              ┌────────────────────┐
              │   Reliability      │
              │      Runtime       │
              │                    │
              │ Context            │
              │ Policy             │
              │ Permission         │
              │ Budget             │
              │ Harness            │
              │ Approval           │
              └─────────┬──────────┘
                        │ 允许的动作
                        ▼
              ┌────────────────────┐
              │ Tools / MCP /      │
              │ Connectors         │
              └─────────┬──────────┘
                        │
                        ▼
              ┌────────────────────┐
              │   External System  │
              │ K8s / Cloud / DB   │
              └─────────┬──────────┘
                        │ 真实状态
                        ▼
              ┌────────────────────┐
              │      Evidence      │
              └─────────┬──────────┘
                        │
                        └──────────→ Runtime 再次决策
```

核心原则只有一句：

> **让模型负责“不确定的决策”，让确定性的代码负责边界、权限、状态、执行和验证。**

这也是整个 AI Reliability Platform 从“LLM Demo”走向“可控 Agent Runtime”的关键。

---

## 28. 后续实现优先级

根据当前架构，下一阶段建议按下面顺序推进：

```text
P0
└── 把 Agent Loop 的状态机真正统一起来

P1
├── Tool Registry + Schema + Permission
├── Evidence 统一数据契约
└── Action / Approval / Verification 闭环

P2
├── Harness：预算 / 超时 / Guard / Stop Condition
├── Context Builder / Compaction
└── Memory Retrieval

P3
├── MCP 动态工具接入
├── Sandbox
└── 多 Provider / Fallback

P4
├── Checkpoint / Recovery
├── Scenario Replay
└── Evaluation / Regression

P5
├── 多租户
├── 完整审计
├── 生产凭据隔离
└── 高风险动作治理
```

每完成一项，都应该同步修改本文的“已实现 / 部分实现 / 目标设计”状态，避免文档和源码再次出现偏差。
