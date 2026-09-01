# IaC 生产 Ready 验收报告

> 本模板用于证明 Terraform/IaC 在进入生产变更阶段前，已经完成代码、State、真实资源、流水线、安全、审批、回滚和运行验证。

## 1. 验收信息

| 项目 | 内容 |
|---|---|
| 项目名称 | 待填写 |
| 环境 | prod |
| Azure 订阅 | 待填写 |
| Terraform 版本 | 待填写 |
| AzureRM Provider 版本 | 待填写 |
| Backend | 待填写 |
| 验收 Commit SHA | 待填写 |
| 验收时间 | 待填写 |
| 验收人 | 待填写 |

## 2. 验收结论

状态只能填写：

- `READY`：允许进入生产变更；
- `READY_WITH_EXCEPTION`：存在已批准例外；
- `NOT_READY`：禁止进入生产。

最终结论：`待填写`

## 3. 仓库结构验收

- [ ] 生产 Root Module 明确；
- [ ] 模块边界清晰；
- [ ] 生产变量与非生产变量隔离；
- [ ] Provider 版本明确；
- [ ] Terraform 版本明确；
- [ ] 敏感信息未进入 Git；
- [ ] 资源命名和标签符合标准。

证据：

```text
待填写：目录、Commit、检查结果
```

## 4. Backend 验收

- [ ] 使用远程 State；
- [ ] State 与其他环境隔离；
- [ ] Backend 访问身份明确；
- [ ] State 锁可用；
- [ ] State 备份/恢复策略明确；
- [ ] State 不出现在 Git；
- [ ] CI 不输出 State 敏感内容。

验证记录：

```text
待填写
```

## 5. Azure 资源纳管验收

必须明确回答：生产订阅中有哪些资源已经由 Terraform 管理？哪些没有？

| 资源 ID | 资源类型 | Terraform 地址 | 是否纳管 | 处理方式 | 结果 |
|---|---|---|---|---|---|
| 待填写 | 待填写 | 待填写 | 是/否 | Import/排除/待治理 | 待填写 |

Import 完成后必须执行 Plan，并记录差异。

## 6. Drift 验收

检查：

```text
Azure Reality
    ↓
Terraform Refresh / Plan
    ↓
Configuration Difference
```

- [ ] 未发现非预期 Drift；
- [ ] 已发现 Drift 均已分类；
- [ ] 合法人工配置已经回写 IaC；
- [ ] 不合法人工变更已经恢复或进入治理；
- [ ] 未通过大量 `ignore_changes` 掩盖问题。

## 7. Terraform Validate

执行：

```bash
terraform fmt -check -recursive
terraform init
terraform validate
```

结果：`PASS / FAIL`

失败日志：

```text
待填写
```

## 8. Terraform Plan 验收

必须保存生产 Plan 的证据。

```text
Plan Commit SHA: 待填写
Plan 时间: 待填写
Add: 待填写
Change: 待填写
Destroy: 待填写
Replace: 待填写
```

特别检查：

- [ ] 无意外 Destroy；
- [ ] 无意外 Replace；
- [ ] 无大规模资源变更；
- [ ] 网络路由无意外变化；
- [ ] 防火墙/安全组无意外放开；
- [ ] IAM/RBAC 无意外扩大；
- [ ] 数据资源无危险变更。

## 9. Policy Gate 验收

生产 Plan 必须经过策略门禁。

建议阻断：

```text
Destroy 关键生产资源
Replace 关键生产资源
开放高风险网络入口
扩大生产权限
修改关键 DNS / 路由
绕过 Approval
使用非受控身份
```

Policy Gate 结果：`PASS / FAIL`

## 10. GitHub Actions 验收

- [ ] Pull Request 自动 Validate；
- [ ] Pull Request 自动 Plan；
- [ ] Plan 与 Commit 绑定；
- [ ] Apply 不允许绕过生产保护；
- [ ] Environment Approval 已启用；
- [ ] 失败日志可追踪；
- [ ] 制品/Plan 留存策略明确。

## 11. 权限验收

检查最小权限：

```text
谁可以 Plan？
谁可以 Apply？
谁可以批准生产？
谁可以读取 State？
谁可以修改 Backend？
```

必须有明确答案。

## 12. 失败场景验收

至少验证或演练：

1. Terraform Validate 失败；
2. Plan 失败；
3. Apply 中断；
4. Azure API 暂时失败；
5. State Lock 冲突；
6. CI Runner 中断；
7. Apply 部分成功；
8. 资源发生 Drift；
9. 生产 Plan 出现 Destroy；
10. 需要回滚代码。

## 13. 回滚验收

回滚必须写清楚：

```text
触发条件
→ 停止进一步变更
→ 保存当前 State / Plan / 日志
→ 判断代码回滚还是资源回滚
→ 执行批准后的回滚
→ 验证 Azure Reality
→ 再次 Plan
```

## 14. 生产变更运行验收

- [ ] 变更窗口明确；
- [ ] 变更负责人明确；
- [ ] 审批人明确；
- [ ] 影响范围明确；
- [ ] 监控指标明确；
- [ ] 回滚条件明确；
- [ ] 变更后验证项明确。

## 15. 最终签字

### 技术负责人

- 姓名：
- 结论：
- 时间：

### 生产负责人

- 姓名：
- 结论：
- 时间：

### IaC 维护负责人

- 姓名：
- 结论：
- 时间：

## 16. Ready 判定规则

只有满足以下条件才允许标记 `READY`：

```text
代码可验证
+ State 可恢复
+ 资源已纳管或有明确例外
+ Plan 可审查
+ Policy Gate 可阻断高风险变更
+ Apply 受生产保护
+ 权限最小化
+ 回滚路径存在
+ 变更后可验证
= Production Ready
```
