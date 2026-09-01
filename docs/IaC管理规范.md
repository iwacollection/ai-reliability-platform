# IaC 管理规范

> 本规范定义 Terraform 等 IaC（基础设施即代码）在本项目中的目录、模块、资源、变量、环境、变更、评审、导入、漂移、销毁和生产发布标准。

## 1. 核心原则

IaC 不是“把 Terraform 文件放进 Git”这么简单，而是建立：

```text
需求
→ 代码
→ Review
→ Validate
→ Plan
→ 风险判断
→ Approval
→ Apply
→ 验证
→ State / 审计
```

生产基础设施必须做到：代码是期望状态，State 是管理映射，Azure 是真实状态，三者可以被持续比较。

## 2. 仓库结构

推荐结构：

```text
terraform/
├── modules/
│   ├── network/
│   ├── compute/
│   ├── aks/
│   ├── storage/
│   └── monitoring/
├── environments/
│   ├── dev/
│   ├── staging/
│   └── prod/
└── README.md
```

模块只封装稳定的资源组合；环境目录负责实例化模块和注入环境参数。

## 3. 模块设计

一个模块必须有清晰的输入、资源实现和输出：

```text
module
├── variables.tf   输入参数
├── main.tf        资源
├── outputs.tf     输出
├── versions.tf    Provider / Terraform 约束
└── README.md      使用说明
```

模块不得隐式读取其他环境的变量或 State。

### 模块什么时候应该拆

如果多个环境重复出现相同资源组合，或者资源组合有明确生命周期，可以考虑模块化。

不要为了“代码看起来高级”而把每一个 Azure Resource 都包装成一个模块。过度封装会让真实资源关系难以理解。

## 4. 环境隔离

生产、测试、开发必须拥有明确边界：

- 独立 State；
- 独立变量；
- 独立凭据/身份；
- 独立审批策略；
- 生产变更必须经过更严格的保护。

生产配置不能依赖开发环境文件。

## 5. Provider 与版本

Terraform 和 Provider 版本必须显式约束，避免执行机器自动升级导致 Plan 发生变化。

版本升级必须单独提交并验证：

```text
升级前 Plan
→ Provider 升级
→ Init
→ Validate
→ Plan
→ 检查资源差异
→ Review
```

## 6. 变量管理

变量分为：

1. 环境配置；
2. 资源参数；
3. 功能开关；
4. 敏感信息引用。

密码、Token、连接密钥禁止硬编码到 `.tf`、`.tfvars` 或 GitHub Actions 日志。

## 7. 资源命名与标签

资源命名必须能反推出：

```text
业务 / 环境 / 区域 / 资源类型
```

标签至少建议包含：

- environment；
- application；
- owner；
- managed-by=terraform；
- cost-center（如组织需要）。

标签不是装饰，它用于成本分析、资源归属和未纳管资源识别。

## 8. Existing Resource：已有资源纳管

发现 Azure 已存在资源时，不允许直接写同名 Terraform Resource 后 Apply。

标准流程：

```text
Azure 现状扫描
→ 判断是否应该纳管
→ 创建 Terraform Resource 定义
→ Import 到 State
→ Plan
→ 修正代码使 Plan 接近 No Change
→ Review
→ 纳管完成
```

Import 的目标不是“让 Terraform 不报错”，而是让：

```text
Terraform Code
      ≈
Terraform State
      ≈
Azure Reality
```

## 9. Import 后为什么还会出现变更

Import 只建立 State 中的资源地址与真实 Azure Resource ID 映射，并不会自动替你写出完整 Terraform 配置。

因此 Import 后必须执行 Plan。

如果出现：

```text
~ update
```

要判断到底是：

- Terraform 配置遗漏；
- 默认值与真实值不同；
- Provider 读取方式变化；
- Azure 真实漂移；
- 不应该由 Terraform 管理的字段。

不能简单用 `ignore_changes` 把所有差异隐藏掉。

## 10. Drift：配置漂移

漂移是指 Azure 真实状态已经与 Terraform 记录或代码声明不一致。

处理顺序：

```text
发现 Drift
→ 判断变更来源
→ 判断是否符合预期
→ 如果符合：更新 IaC
→ 如果不符合：通过 IaC 恢复
→ Plan
→ Apply
→ 验证
```

如果经常发生人工改 Azure，应治理权限，而不是不断增加 `ignore_changes`。

## 11. 生命周期与销毁保护

生产资源必须审慎使用 `destroy`。

数据库、生产存储、关键网络组件等资源必须建立删除保护策略，并在 CI 中阻止明显危险的计划。

例如发现：

```text
Plan: 1 to destroy, 0 to add
```

如果对象属于生产关键资源，必须进入人工审查，不能仅因为 Pipeline 能执行就执行。

## 12. Pull Request 评审要求

每个 IaC PR 至少说明：

- 为什么修改；
- 修改哪些资源；
- Plan 预计发生什么；
- 是否包含 Destroy；
- 是否包含 Replace；
- 是否影响网络、权限、数据；
- 回滚方式；
- 验证方式。

## 13. CI 检查

最低要求：

```text
格式检查
→ 初始化
→ Validate
→ Plan
→ Plan Artifact
→ Policy Gate
```

生产 Apply 不应该在任意 Pull Request 中自动执行。

## 14. 生产变更

推荐：

```text
Pull Request
   ↓
自动检查
   ↓
Terraform Plan
   ↓
风险分类
   ↓
人工 Approval
   ↓
Apply
   ↓
资源验证
   ↓
结果归档
```

生产 Apply 必须绑定明确的 Commit SHA，避免“代码已经变了，但执行的是旧 Plan”。

## 15. 变更失败

失败时首先判断：

```text
Terraform CLI 失败？
Provider 失败？
Azure API 失败？
资源已经部分创建？
State 是否已经更新？
```

禁止简单重复 Apply 多次碰运气。

## 16. 回滚

IaC 回滚不是简单执行 `git revert`。

必须区分：

- Git 代码回滚；
- Terraform State 回滚；
- Azure 资源实际回滚；
- 数据回滚。

其中数据库数据通常不能通过 Terraform 回滚，因此需要独立的数据恢复方案。

## 17. 生产 Ready 标准

- [ ] 所有生产资源有明确 Owner；
- [ ] State 使用远程 Backend；
- [ ] 生产 State 与非生产隔离；
- [ ] 关键资源已有纳管清单；
- [ ] 未纳管资源已经分类；
- [ ] Import 后 Plan 已收敛；
- [ ] CI 存在 Validate/Plan；
- [ ] Apply 需要生产保护；
- [ ] Destroy/Replace 有策略门禁；
- [ ] 变更有审计记录；
- [ ] 有失败处理与回滚 Runbook；
- [ ] 有最终验收报告。
