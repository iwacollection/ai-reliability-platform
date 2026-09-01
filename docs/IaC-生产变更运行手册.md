# IaC 生产变更运行手册

> 本手册是 Terraform 生产变更的实际操作标准。它解决的不是“Terraform 命令怎么执行”，而是生产环境中谁可以变更、变更前看什么、出现异常怎么办、如何停止继续扩大影响、如何验证恢复以及如何留下证据。

## 1. 适用范围

适用于所有通过 Terraform 管理的生产 Azure 基础设施，包括网络、计算、AKS、存储、数据库、监控、身份权限以及其他受 Terraform 管理的资源。

## 2. 生产变更总流程

```text
需求提出
  ↓
修改 Terraform
  ↓
Pull Request
  ↓
自动检查
  ├─ fmt
  ├─ validate
  └─ plan
  ↓
Plan 人工审查
  ↓
Policy Gate
  ↓
生产 Approval
  ↓
绑定已审查 Commit / Plan
  ↓
Apply
  ↓
Azure 资源验证
  ↓
业务验证
  ↓
变更关闭
```

任何一步失败，都不能默认进入下一步。

## 3. 变更前检查

### 3.1 变更对象

必须回答：

- 改什么？
- 为什么改？
- 谁要求？
- 影响哪些资源？
- 是否涉及网络？
- 是否涉及权限？
- 是否涉及数据？
- 是否可能 Replace？
- 是否可能 Destroy？

### 3.2 当前状态

执行 Plan 前确认：

```text
当前生产是否有其他 Terraform Apply？
是否存在人工 Azure 变更？
State 是否正常？
最近一次 Apply 是否成功？
```

如果存在并发变更，先协调窗口。

## 4. Pull Request 阶段

PR 必须包含：

```text
变更背景
变更资源
Plan 摘要
风险等级
影响范围
回滚方案
验证方案
```

Reviewer 不能只看 `.tf` 代码，必须结合 Plan 看真实资源影响。

## 5. Plan 审查

重点关注：

```text
+ create
~ update
-/+ replace
- destroy
```

风险通常按以下方式提升：

| Plan 类型 | 风险判断 |
|---|---|
| 仅新增非关键资源 | 低/中 |
| 修改普通配置 | 中 |
| 修改网络 | 高 |
| 修改权限 | 高 |
| Replace 生产资源 | 高 |
| Destroy 生产资源 | 极高 |
| 数据资源 Destroy | 极高 |

## 6. Policy Gate

Policy Gate 是自动化安全门，不允许通过修改 Prompt、参数或手工执行绕过。

至少检查：

- 生产 Destroy；
- 生产 Replace；
- 高风险 RBAC；
- 高风险网络开放；
- 未授权资源类型；
- 未受保护的 Apply；
- 执行身份不符合要求。

## 7. Approval

Approval 必须发生在 Apply 之前，并绑定明确的变更对象。

正确关系：

```text
Commit SHA
   +
Terraform Plan
   +
风险说明
   +
批准人
   ↓
允许 Apply
```

如果批准后代码发生变化，原则上重新 Plan 和重新审批。

## 8. Apply

Apply 开始后记录：

- 开始时间；
- Commit SHA；
- Plan 标识；
- 执行人/执行身份；
- Terraform 版本；
- Provider 版本；
- 目标环境；
- Job 地址或运行记录。

禁止在生产环境临时修改 Terraform 文件后直接 Apply。

## 9. Apply 中出现错误

第一原则：**先判断真实资源状态，再决定下一步。**

不要看到命令失败就立即重跑。

判断：

```text
Terraform 报错
  ↓
请求是否已经发送到 Azure？
  ↓
Azure 是否已经执行部分变更？
  ↓
State 是否已经更新？
  ↓
资源当前真实状态是什么？
```

## 10. Apply 部分成功

这是生产变更最危险的情况之一。

例如：

```text
资源 A 成功
资源 B 成功
资源 C 失败
资源 D 未执行
```

此时不能简单理解为“整个 Apply 都失败了”。必须核对：

- Azure Reality；
- Terraform State；
- Terraform Plan；
- 失败资源；
- 依赖关系。

之后重新 Plan，确认 Terraform 现在认为需要做什么。

## 11. State Lock 冲突

如果出现锁冲突：

```text
另一个 Terraform 操作正在使用 State
```

必须：

1. 查看 GitHub Actions；
2. 确认是否有运行中的 Apply；
3. 确认是否存在人工 Terraform；
4. 等待正常任务结束；
5. 只有确认锁属于陈旧状态时才执行受控解锁。

禁止直接删除 State 或随意强制解锁。

## 12. Azure API 暂时失败

如果 Azure 返回限流、临时网络错误或服务暂时不可用：

```text
停止盲目重试
→ 保存错误日志
→ 判断资源是否已经改变
→ 等待服务恢复
→ 重新 Plan
→ 根据新状态决定是否重试
```

重复 Apply 可能把一次临时故障变成状态不一致。

## 13. 生产事故中的止损

出现以下情况立即暂停后续 Apply：

- 大量资源出现非预期变更；
- 出现生产 Destroy；
- 网络配置异常；
- RBAC 大范围变化；
- 数据资源出现 Replace/Destroy；
- State 疑似损坏；
- Terraform Provider 行为异常。

止损动作：

```text
停止 Pipeline
→ 保留日志
→ 保存 Plan
→ 保存 State 版本/备份
→ 冻结相关变更
→ 确认 Azure Reality
→ 再决定恢复策略
```

## 14. 回滚

### 情况 A：代码错误但资源没有 Apply

直接修正或 Revert，然后重新 Plan。

### 情况 B：代码已经 Apply

不能只 Git Revert。必须：

```text
Git Revert
→ 新 Plan
→ 审查反向变更
→ Approval
→ Apply
→ 验证
```

### 情况 C：资源已部分成功

必须先重新建立现实状态，再决定恢复，不允许假设“回滚就是再执行一次旧版本”。

### 情况 D：数据已经发生变化

Terraform 不能替代数据库/存储的数据恢复方案。需要调用独立的数据恢复流程。

## 15. 变更后验证

### 基础设施层

验证：

- 资源存在；
- 状态正确；
- 网络连通；
- 权限正确；
- 健康状态正常。

### Kubernetes 层

如果变更影响 AKS：

```text
Node Ready
Pod Ready
Deployment Available
Service / Endpoint 正常
Ingress 正常
```

### 业务层

最终必须确认：

```text
错误率
延迟
可用性
关键业务接口
依赖服务
```

## 16. 变更完成判定

满足：

```text
Apply 成功
+ Terraform State 正常
+ Azure Reality 正常
+ 业务指标正常
+ 无遗留 Lock
+ 无异常 Drift
+ 审计证据完整
```

才可以关闭变更。

## 17. 变更证据包

每次生产变更至少保留：

```text
Commit SHA
Terraform Plan
Approval 记录
Apply 日志
资源变更摘要
验证结果
异常记录
回滚记录（如有）
```

## 18. 常见错误与禁止行为

### 禁止 1：为了通过 Pipeline 删除资源定义

这可能导致 Terraform 计划销毁真实资源。

### 禁止 2：为了解决 Plan 差异大量使用 ignore_changes

这会把真实 Drift 隐藏起来。

### 禁止 3：Apply 失败就连续重试

先判断部分成功和 State 状态。

### 禁止 4：批准后修改代码继续 Apply

批准的是某个变更，不是“任何后续代码”。

### 禁止 5：生产直接 Portal 修改

紧急情况也必须记录，并在事后回写 IaC。

## 19. 紧急变更

紧急变更允许缩短等待时间，但不能取消审计、安全和验证。

```text
紧急事件
→ 判断是否必须立即修改
→ 记录负责人
→ 最小范围变更
→ 人工确认
→ 执行
→ 立即验证
→ 事后回写 Terraform
→ Drift 清理
```

## 20. 一页式操作清单

```text
[变更前]
□ 需求明确
□ 影响范围明确
□ 当前 State 正常
□ 无并发 Apply
□ PR 已 Review
□ Plan 已检查
□ Destroy/Replace 已确认
□ Policy Gate PASS
□ Approval 完成

[变更中]
□ Commit / Plan 一致
□ Apply 日志正常
□ 无异常资源变化
□ 失败时先止损
□ 部分成功已核对

[变更后]
□ State 正常
□ Azure Reality 正常
□ 业务指标正常
□ Drift 已检查
□ 审计证据归档
□ 变更关闭
```
