# Azure 资源未纳管 Import 清单模板

> 用于盘点 Azure 中已经存在、但当前尚未进入 Terraform State 的资源。目标不是盲目 Import，而是判断资源归属、生命周期和是否应该由 IaC 管理，并留下可审计证据。

## 1. 使用原则

发现资源后先问三个问题：

1. 这个资源是谁创建的？
2. 这个资源是否应该长期存在？
3. 这个资源是否应该由 Terraform 管理？

只有确认应纳管的资源才执行 Import。

## 2. 资源清单

| 编号 | Azure Resource ID | 资源类型 | 名称 | 资源组 | 订阅 | 区域 | Owner | 当前创建方式 | 是否纳管 |
|---|---|---|---|---|---|---|---|---|---|
| 001 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | Portal/CLI/其他 | 是/否 |

## 3. 纳管决策

| 编号 | 是否应该纳管 | 原因 | 生命周期 | 风险 | 决策人 | 备注 |
|---|---|---|---|---|---|---|
| 001 | 是/否 | 待填写 | 长期/临时 | 低/中/高 | 待填写 | 待填写 |

### 建议分类

- `IMPORT_REQUIRED`：必须纳管；
- `EXCLUDE_BY_DESIGN`：明确不由 Terraform 管理；
- `TEMPORARY`：临时资源，计划删除；
- `OWNER_UNKNOWN`：Owner 不明确，先治理；
- `BLOCKED`：存在权限、依赖或安全问题，暂缓 Import。

## 4. Import 前检查

每个资源执行 Import 前确认：

- [ ] Resource ID 正确；
- [ ] 资源属于正确订阅；
- [ ] 资源组正确；
- [ ] Owner 已确认；
- [ ] 生产/非生产环境已确认；
- [ ] 依赖资源已识别；
- [ ] Terraform Provider 支持该资源；
- [ ] Terraform Resource 类型正确；
- [ ] 不会因为 Import 影响真实 Azure 资源；
- [ ] 已保存 Azure 当前配置快照。

## 5. Import 记录

| 编号 | Terraform Resource 地址 | Azure Resource ID | Import 时间 | 操作者 | 结果 | State 验证 |
|---|---|---|---|---|---|---|
| 001 | 待填写 | 待填写 | 待填写 | 待填写 | PASS/FAIL | PASS/FAIL |

Import 本身只是建立映射：

```text
Terraform Address
       ↕
Terraform State
       ↕
Azure Resource ID
```

它不代表 Terraform 配置已经完整。

## 6. Import 后 Plan

Import 后必须执行 Plan，并记录：

```text
Before Import
  State: 无

After Import
  State: 有

Plan
  Add: ?
  Change: ?
  Destroy: ?
  Replace: ?
```

### 差异分类

- `EXPECTED_CONFIGURATION_GAP`：代码还没有声明真实配置；
- `DRIFT`：Azure 已经偏离预期；
- `PROVIDER_DEFAULT`：Provider 默认行为造成；
- `IGNORE_CANDIDATE`：确实属于外部控制字段，但必须人工批准；
- `UNKNOWN`：原因未知，不允许直接 Apply。

## 7. Plan 收敛

目标：Import 后经过代码补齐和必要的配置调整，使 Plan 达到预期状态。

理想结果：

```text
Plan: 0 to add, 0 to change, 0 to destroy
```

如果必须存在 Change，也必须说明原因并经过 Review。

禁止：

```text
Import
→ Plan 有大量变更
→ 直接 Apply
```

## 8. 关键资源特别检查

### 网络

重点检查：

- VNet；
- Subnet；
- Route Table；
- NAT；
- Firewall；
- Load Balancer；
- Private Endpoint；
- DNS。

### 身份与权限

重点检查：

- Managed Identity；
- Role Assignment；
- Service Principal 相关配置；
- Key Vault 访问权限。

### 数据

重点检查：

- Storage Account；
- Database；
- Backup；
- Recovery 配置；
- 数据保留策略。

这些资源出现 Replace/Destroy 时必须升级为高风险评审。

## 9. 不纳管资源

如果明确不纳管，必须记录原因：

```text
资源：待填写
Owner：待填写
原因：例如由平台服务自动创建
生命周期：待填写
谁负责管理：待填写
如何监控：待填写
```

“不纳管”不能等于“无人负责”。

## 10. Import 完成标准

单个资源只有满足以下条件才能标记 `IMPORTED_READY`：

- [ ] Resource ID 正确；
- [ ] Terraform 地址稳定；
- [ ] State 已记录；
- [ ] Terraform 配置完整；
- [ ] Plan 已审查；
- [ ] 无未解释 Destroy/Replace；
- [ ] Owner 已确认；
- [ ] 标签符合标准；
- [ ] 后续变更可以由 IaC 管理。

## 11. 汇总

| 状态 | 数量 |
|---|---:|
| IMPORT_REQUIRED | 待填写 |
| IMPORTED_READY | 待填写 |
| EXCLUDE_BY_DESIGN | 待填写 |
| TEMPORARY | 待填写 |
| OWNER_UNKNOWN | 待填写 |
| BLOCKED | 待填写 |

## 12. 最终结论

```text
发现资源总数：待填写
应该纳管：待填写
已经完成 Import：待填写
仍未纳管：待填写
存在高风险差异：待填写
```

结论：`待填写`
