# Terraform Backend 标准化规范

> 本规范定义本项目 Terraform 状态文件（State）的存储、锁、访问控制、环境隔离、备份、恢复、迁移和故障处理标准。目标是让 IaC（基础设施即代码）在团队协作和生产环境中具备可审计、可恢复、可并发控制的工程能力。

## 1. 为什么必须标准化 Backend

Terraform State 不是普通缓存，而是 Terraform 对“代码声明的资源”和“真实基础设施”的映射记录。没有可靠的 State，Terraform 无法准确判断哪些资源已经创建、资源 ID 是什么、哪些属性发生了漂移，也无法安全执行 Plan/Apply。

生产环境禁止使用本地 State 作为唯一状态源，因为本地 State 会导致：

- 多人执行时状态分叉；
- CI 与人工操作使用不同 State；
- State 丢失后无法可靠管理既有资源；
- 并发 Apply 可能互相覆盖；
- 离职、机器损坏或目录误删造成状态不可恢复；
- 无法形成统一的审计与恢复机制。

## 2. 标准架构

```text
开发者 / GitHub Actions
        |
        | Terraform CLI
        v
+-----------------------+
| Terraform Root Module |
+-----------+-----------+
            |
            v
+-----------------------+
| Remote Backend        |
| State + Lock          |
+-----------+-----------+
            |
            v
+-----------------------+
| Azure Resource APIs   |
+-----------------------+
```

Backend 只负责 Terraform State 的持久化与并发控制，不应该承担业务资源编排逻辑。

## 3. State 隔离原则

环境至少按以下维度隔离：

```text
环境
├── dev
├── staging
└── prod
```

更严格的生产环境建议继续按订阅、区域或业务域拆分 Root Module，避免一个 State 管理过多无关资源。

禁止使用一个 State 同时管理开发、测试、生产资源。

推荐：

```text
terraform/
├── environments/
│   ├── dev/
│   ├── staging/
│   └── prod/
└── modules/
```

## 4. Azure Storage Backend 标准

Azure 环境推荐使用 Azure Storage Account + Blob Container 保存 State，并启用 Blob 租约实现状态锁。

标准组件：

- Storage Account：专用于 Terraform State；
- Blob Container：例如 `tfstate`；
- State Blob：每个 Root Module 一个逻辑 State；
- Blob Lease：用于并发锁；
- Storage Account 防护：HTTPS、最小权限、网络限制、软删除/版本能力（若组织策略允许）。

不要把 State 放进应用数据 Storage Account 中，避免业务权限和 IaC 权限混杂。

## 5. 权限模型

推荐使用身份而不是长期静态密钥：

```text
GitHub Actions
      |
      v
OIDC / Workload Identity
      |
      v
Azure 身份
      |
      +--> State 读写权限
      +--> 资源变更权限（按环境控制）
```

生产环境必须区分：

- State 访问权限；
- Terraform Plan 权限；
- Terraform Apply 权限；
- 人工批准权限。

不能因为 Terraform 需要写 State，就给所有执行主体整个订阅的 Owner 权限。

## 6. State 安全要求

State 可能包含资源 ID、连接信息以及敏感属性。即使配置使用 Secret 管理，也不能假设 State 一定没有敏感数据。

必须：

1. 禁止把 `terraform.tfstate` 提交 Git；
2. 禁止把 State 打包进构建制品；
3. 限制 Storage Account 网络访问；
4. 限制 State 读取人员；
5. CI 日志禁止打印 State 内容；
6. 对 State 存储启用组织要求的保留、恢复和审计能力。

## 7. 并发控制

同一个生产 State 同时只能允许一个 Apply。

```text
Job A ---- Apply ----> LOCKED
                         |
Job B ---- Apply ------> WAIT
                         |
                         v
                      UNLOCK
```

如果锁等待超时，不允许通过删除锁文件等方式强行继续。必须先确认持锁进程是否仍然运行。

## 8. State Lock 异常处理

排查顺序：

1. 查看当前 GitHub Actions 是否存在运行中的 Apply；
2. 查看是否存在人工 Terraform 进程；
3. 判断任务是否已经退出但锁没有释放；
4. 确认没有活动 Apply 后，再按 Backend 提供方机制清理陈旧锁；
5. 清理后执行 `terraform plan`，检查 State 是否正常。

禁止在不确认持锁任务的情况下强制解锁。

## 9. State 备份与恢复

恢复前必须确认：

- 当前 State 是否损坏；
- 最近一次成功 Apply 的时间；
- 是否存在 State 版本；
- 真实 Azure 资源是否发生变化；
- 是否存在近期 Import、Move、Destroy 操作。

恢复不是简单地“把旧文件覆盖回来”。恢复后必须重新执行 Refresh/Plan，并核对真实资源。

## 10. Backend 迁移

Backend 迁移必须经过：

```text
现状确认
  ↓
备份 State
  ↓
冻结 Apply
  ↓
配置新 Backend
  ↓
State Migration
  ↓
读取验证
  ↓
Plan 验证
  ↓
解除冻结
```

严禁先删除旧 State，再尝试从新 Backend 恢复。

## 11. 验收标准

Backend Ready 至少满足：

- [ ] 生产 State 不在本地；
- [ ] 不同环境 State 隔离；
- [ ] Apply 存在并发锁；
- [ ] CI 使用受控身份；
- [ ] State 读取权限最小化；
- [ ] State 具备恢复能力；
- [ ] Backend 变更可审计；
- [ ] 锁异常有标准 Runbook；
- [ ] Terraform Plan 不产生非预期大规模变更。

## 12. 事故处理原则

State 出问题时，优先保护 State 和真实资源，不要为了让流水线“变绿”而盲目执行 Apply。

标准顺序：

```text
停止变更
→ 保存现状
→ 备份 State
→ 判断 State/代码/真实资源三者关系
→ 恢复或修正 State
→ Plan
→ 人工审查
→ Apply
→ 验证
```
