# ChatOps 与 Human-in-the-loop：生产故障中的人工协作设计

## 1. 核心目标

Agent 的目标不是消灭人工，而是把人工从：

```text
不停查日志
不停敲命令
不停确认状态
```

提升到：

```text
审核证据
判断风险
批准高风险动作
处理异常边界
```

## 2. 人机分工

```text
Agent
├── 收集 Evidence
├── 形成 Hypothesis
├── 调查
├── 生成 Action Plan
└── 提出建议

Human
├── 提供业务上下文
├── 审核 Diagnosis
├── 审核高风险 Action
├── 拒绝不合理方案
└── 处理 Runtime 无法确定的情况
```

## 3. 什么情况下必须人工

典型条件：

```text
高风险生产写操作
不可逆操作
高爆炸半径
证据不足
多个 Hypothesis 无法区分
Policy 要求审批
Verification 持续失败
External State UNKNOWN
```

## 4. Approval 请求

ChatOps 中应该展示结构化审批卡片：

```text
Incident: INC-123

问题：payment-api 错误率升高

证据：
- error rate +35%
- pod restart count abnormal
- recent deployment detected

Diagnosis：新版本启动参数异常

Action：rollback deployment
Target：payment/payment-api
Risk：high

Verification：
- rollout complete
- error rate recovered
- latency recovered

[批准] [拒绝] [查看证据]
```

## 5. 为什么不能让“聊天回复”直接执行

危险模式：

```text
用户：帮我回滚
 ↓
LLM
 ↓
执行 rollback
```

正确模式：

```text
用户提出意图
 ↓
Agent 调查
 ↓
生成结构化 Action
 ↓
Policy
 ↓
Approval
 ↓
Executor
 ↓
Verification
```

聊天是入口，不是授权机制。

## 6. 审批必须绑定 Action

审批对象必须包含：

```text
action_id
action_fingerprint
target
parameters
risk
policy_decision
expires_at
approver
```

如果 Action 改变：

```text
旧 Approval 无效
→ 重新 Policy
→ 重新 Approval
```

## 7. 多人审批

对于极高风险动作可以：

```text
require 2 approvers
```

或者：

```text
业务 Owner
+
SRE / 平台 Owner
```

具体规则由 Policy 决定，而不是 Agent 决定。

## 8. 审批超时

Approval 不是永久授权。

```text
WAITING_APPROVAL
 ↓ timeout
EXPIRED
```

过期后不能自动执行。

## 9. 拒绝后的行为

```text
REJECTED
 ↓
记录原因
 ↓
Agent 重新评估
```

如果人工提供新信息，可以重新形成 Action。

不能通过不断生成相同 Action 绕过拒绝。

## 10. ChatOps 与 Incident State

ChatOps 只是一个控制面：

```text
Feishu / Slack / Teams / Web UI
            ↓
       ChatOps Adapter
            ↓
      Incident Runtime
            ↓
 State / Evidence / Policy / Action
```

不同聊天平台不应该各自实现一套 Incident 逻辑。

## 11. 人工输入可信度

Human 也不是所有身份拥有同样权限。

需要区分：

```text
普通观察者
Incident Contributor
Approver
Service Owner
Platform Administrator
```

人工审批同样需要身份认证和授权。

## 12. 审批后的执行保护

批准后仍然要重新检查：

```text
Approval 是否有效
Action fingerprint 是否一致
Incident 是否仍然允许执行
Target 是否仍存在
Policy 是否仍有效
```

因为审批与执行之间可能发生状态变化。

## 13. 紧急操作

紧急流程也不能等于“完全绕过安全”。

可以设计：

```text
Emergency Policy
 ↓
缩短审批链
 ↓
更严格审计
 ↓
执行后强制复盘
```

紧急模式应该是显式 Policy，而不是 Agent 自己宣布“这是紧急情况”。

## 14. 完整闭环

```text
Alert
 ↓
Incident
 ↓
Agent Investigation
 ↓
Evidence
 ↓
Diagnosis
 ↓
Action Proposal
 ↓
Policy
 ↓
Human Approval
 ↓
Execution
 ↓
Verification
 ↓
Incident Resolved
 ↓
Audit / Postmortem
```

## 15. 生产原则

```text
ChatOps 是入口
Agent 是调查与决策辅助
Policy 是安全边界
Human 是高风险决策者
Executor 是执行者
Verification 是最终事实判断
Audit 是事后证据
```
