# OOMKilled Production Pilot v1 Runbook

状态：默认禁止真实写入。未经变更审批和现场 Go/No-Go 复核，不得启用。

## 1. 试点边界

- 只允许一个明确的 `cluster / namespace / Deployment / container`。
- 只允许 `increase_memory_limit`，增幅不超过现有安全策略上限。
- 只允许已完成 OOMKilled 证据确认、Kubernetes `dryRun=All` 和人工审批的 Action。
- Preflight 与 Production Execution 必须使用不同的 ServiceAccount 凭据。
- 真实 PATCH 不自动重试；结果不明确时必须进入 `INDETERMINATE` 并人工对账。
- 每个 Pilot 只有一个持久化真实写入预算；预算一旦 `reserved` 或 `consumed` 不会自动释放。

## 2. 启用前配置

生产执行配置仍由 `remediation.kubernetes_production_execution` 控制。Pilot 清单使用以下非密钥环境变量：

- `KUBERNETES_PRODUCTION_PILOT_ENABLED=true`
- `KUBERNETES_PRODUCTION_PILOT_ID`
- `KUBERNETES_PRODUCTION_CHANGE_TICKET`
- `KUBERNETES_PRODUCTION_RUNBOOK_VERSION=oom-runbook-v1`
- `KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT=I_HAVE_READ_AND_ACCEPT_OOM_PILOT_RUNBOOK_V1`
- `KUBERNETES_PRODUCTION_KILL_SWITCH_FILE`
- `KUBERNETES_PRODUCTION_AUTHORIZED_OPERATORS`
- `KUBERNETES_PRODUCTION_PILOT_STARTS_AT`
- `KUBERNETES_PRODUCTION_PILOT_EXPIRES_AT`

试点窗口最长四小时。Operator ID 必须与 API 认证主体完全一致。

## 3. Kill Switch

Kill Switch 文件只接受两个精确值：

- `ENGAGED`：阻止生产 Claim 或真实写入。
- `DISENGAGED_FOR_OOM_PILOT_V1`：仅在其他检查全部通过时允许继续。

文件缺失、不可读、过大、包含额外空格或内容不匹配时，一律视为 `ENGAGED`。

启动应用和进行准备工作时必须保持 `ENGAGED`。解除前必须完成双人复核。

## 4. Go/No-Go 检查

使用具备只读权限的认证身份调用：

```text
GET /production-actions/pilot-readiness
```

解除 Kill Switch 前必须确认：

- `ready_for_enablement=true`
- `ready_for_execution=false`
- 唯一阻断项为 `kill_switch_engaged`
- `exact_target_count=1`
- `credential_references_separate=true`
- `window_state=active`
- Pilot ID、变更单和 Runbook 版本与现场记录一致

解除后再次查询，只有 `ready_for_execution=true` 才允许执行一次 Resume。

## 5. 零写入启用演练

保持 Kill Switch 为 `ENGAGED`，使用 Pilot 白名单中的 Executor 身份调用：

```text
POST /production-actions/pilot-rehearsal
```

演练通过必须同时满足：

- `passed=true` 与 `zero_write=true`
- `budget_state=available`
- `durable_claim_created=false`
- `external_call_count=0`
- `real_write_attempted=false`
- Operator 与认证主体完全一致

演练接口不得创建 Approval、Action Execution、预算预留或 Verification，也不得访问 Kubernetes。Kill Switch 已解除时演练必须失败。

## 6. 零写入崩溃恢复演练

在任何生产执行开关变更前，由 Executor 或 Admin 使用与认证主体一致的 `X-Operator-ID` 调用：

```text
POST /production-actions/pilot-crash-recovery-rehearsal
```

这是纯策略演练，不是现场 Go/No-Go 检查。响应必须确认：

- `passed=true`、`synthetic_rehearsal=true`、`live_state_checked=false`
- `checkpoint_count=13` 且 `passed_checkpoint_count=13`
- `storage_read_count=0`、`storage_write_count=0`
- `external_call_count=0`、`kubernetes_call_count=0`
- `production_executor_call_count=0`、`verification_call_count=0`
- `budget_reservation_count=0`、`real_write_attempted=false`
- `authorizes_enablement=false`、`authorizes_execution=false`
- `automatic_action_replay_allowed=false`
- `report_sha256` 校验通过

13 个持久化切点必须全部出现，并使用以下固定状态标识：

- `preflight_artifact_committed`
- `approval_pending`
- `approval_approved_ceremony_ready`
- `action_execution_claimed`
- `ceremony_activated`
- `pilot_budget_reserved`
- `pilot_budget_consumed`
- `action_execution_succeeded`
- `action_execution_failed`
- `action_execution_indeterminate`
- `verification_claimed`
- `verification_passed`
- `verification_not_passed`

只有 `approval_approved_ceremony_ready` 允许一次人工、认证的首次 Resume；这仍要求重新执行现场 Readiness 检查。Claim 创建后的任何中断都禁止自动重放 Action。`RUNNING`、`ACTIVATED`、`RESERVED`、`CONSUMED` 或 `INDETERMINATE` 必须恢复 Kill Switch 并人工对账。Action 已成功时只能恢复 Exactly-once Verification，不得再次执行 Action。演练报告本身绝不授权启用或执行。

## 7. Pilot 启用仪式证据

零写入演练通过后，保持 Kill Switch 为 `ENGAGED`。由 Approver 或 Admin 作为复核人，提交已批准 Approval 的启用清单：

```text
POST /production-actions/{approval_id}/pilot-activation-checklist
```

请求必须使用唯一 `Idempotency-Key`，`X-Operator-ID` 必须与认证主体一致，并包含以下精确确认值：

```text
I_CONFIRM_OOM_PILOT_CANARY_ACTIVATION_CHECKLIST_V1
```

复核人必须与清单指定的 Executor 不同；Executor 必须在 Pilot 精确白名单内。所有目标、凭据隔离、回滚、监控、Kill Switch、预算与 Runbook 检查都必须显式为 `true`。

成功响应必须确认：

- `created=true` 或完全相同请求的 `idempotent_replay=true`
- `ceremony.status=ready`
- `ceremony.kill_switch_state=engaged`
- `ceremony.budget_state=available`
- `action_execution_created=false`
- `external_call_count=0`
- `real_write_attempted=false`

该接口只持久化不可变的双人复核证据，不解除 Kill Switch，不创建 Action Execution Claim，不预留预算，不访问 Kubernetes，也不启动 Verification。冲突请求必须停止，不得换用新的幂等键绕过既有证据。

## 8. 最终 Pre-enable Evidence 与零写入签署

在任何生产执行开关变更前，保持 Kill Switch 为 `ENGAGED`，先使用只读身份获取最终证据包：

```text
GET /production-actions/{approval_id}/pilot-pre-enable-evidence
```

响应必须同时确认：

- `ready_for_sign_off=true` 且 `evidence_blockers` 为空。
- `artifact_state=approval_bound`、`approval_state=approved`、`incident_state=confirmed`。
- `ceremony_state=ready`、`budget_state=available`。
- `action_execution_state=not_created`、`verification_state=not_created`。
- `contract_clock_state=valid`、`ceremony_clock_state=valid`、`pilot_window_state=active`。
- `kill_switch_state=engaged`。
- `production_execution_enabled=false` 且 `production_executor_configured=false`。
- `bindings_consistent=true`、`executor_allowlisted=true`。
- 审批人、Ceremony 复核人与精确 Executor 满足职责分离。
- `enablement_rehearsal_passed=true`。
- `crash_recovery_rehearsal_passed=true` 且 `crash_recovery_checkpoint_count=13`。
- `storage_read_only=true`、`storage_write_count=0`。
- `durable_claim_created=false`、`budget_reservation_count=0`。
- `external_call_count=0`、`kubernetes_call_count=0`、`production_executor_call_count=0`、`verification_call_count=0`。
- `real_write_attempted=false`、`authorizes_enablement=false`、`authorizes_execution=false`、`automatic_resume_allowed=false`。
- `evidence_sha256` 已通过模型完整性校验。

随后只有 Ceremony 中记录的精确 Executor 可以提交同一证据摘要：

```text
POST /production-actions/{approval_id}/pilot-pre-enable-sign-off
```

`X-Operator-ID` 必须与认证主体一致，请求必须包含刚刚读取的 `expected_evidence_sha256` 和以下精确确认值：

```text
I_CONFIRM_OOM_PILOT_PRE_ENABLE_EVIDENCE_V1
```

成功响应必须确认 `sign_off_passed=true`、`persisted=false`、`storage_write_count=0`、所有外部调用计数为零、`real_write_attempted=false`、`authorizes_enablement=false` 和 `authorizes_execution=false`。相同状态、相同 Executor 与相同摘要的重放必须返回同一 `sign_off_sha256`；状态发生任何变化后，旧摘要必须返回冲突。

该签署只生成一个有界、非持久化的现场确认结果。Operator 必须将完整响应保存到受控变更单证据中；平台不会把它写入 SQLite。它不创建 Claim、不预留预算、不调用 Kubernetes、不启动 Verification，也不授权启用生产开关或执行 Action。Admin 虽具备 RBAC Resume 权限，也不能代替 Ceremony 中精确记录的 Executor 完成此签署。任何后续 Resume 前仍必须重新执行实时 Readiness、有效期、Kill Switch、Ceremony 和预算检查。

## 9. 最终部署、凭据、TLS 与 Operator 交接演练

Pre-enable Evidence 签署通过后，生产开关仍保持禁用、Kill Switch 仍保持 `ENGAGED`，并确认 Runtime 与 ActionRuntime 都没有装配生产 Executor。只有 Ceremony 中精确记录的 Executor 可以调用：

```text
POST /production-actions/{approval_id}/pilot-final-handoff-rehearsal
```

`X-Operator-ID` 必须与认证主体一致。请求必须绑定当前 `evidence_sha256`、Pilot ID、变更单、Runbook 版本、待部署镜像或发布物的 `sha256:<64 hex>` 摘要，并提交彼此不同的 on-call、rollback 与 reconciliation owner。三个 owner 都不得是本次 Executor。部署发布物证据、所有凭据引用、TLS、Security Matrix、监控、回滚和对账确认必须显式为 `true`，并包含以下精确确认值：

```text
I_CONFIRM_OOM_PILOT_FINAL_ZERO_WRITE_HANDOFF_V1
```

成功报告必须同时确认：

- `passed=true`、`blockers` 为空、`zero_write=true`、`storage_read_only=true`。
- `feature_gate_disabled=true`、`production_executor_absent=true`、`action_runtime_production_executor_absent=true`。
- `kill_switch_engaged=true`、`pilot_window_active=true`、`exact_single_target=true`。
- Preflight Runtime 与不可变 Evidence 的 cluster、target 和 policy 绑定一致。
- Kubernetes API 是无用户信息、查询和片段的 HTTPS origin；TLS 验证已强制且 Runtime 与配置一致。
- Preflight 与 Production credential reference 类型有效、彼此不同，引用元数据可用。
- `credential_content_read_count=0`、`credential_content_validated=false`、`tls_handshake_performed=false`。
- 审批人、Ceremony 复核人、Executor 和三名 handoff owner 满足职责分离。
- `security_route_count=22`、`security_role_count=7` 且 Security Matrix 已复核。
- `storage_write_count=0`、`durable_claim_created=false`、`budget_reservation_count=0`。
- `network_call_count=0`、`kubernetes_call_count=0`、`production_executor_call_count=0`、`verification_call_count=0`。
- `authorizes_feature_enablement=false`、`authorizes_execution=false`、`automatic_resume_allowed=false`。
- `configuration_sha256` 与 `report_sha256` 通过完整性校验。

该接口只检查环境变量或文件引用是否存在、文件是否为有界普通文件，不读取、解析或验证任何凭据内容，也不执行 TLS handshake 或任何网络访问。报告必须明确 `requires_guarded_startup_credential_validation=true` 和 `requires_live_tls_recheck_before_enablement=true`。凭据内容校验与实时 TLS 验证只能在后续受控的一次性启动流程中进行，且失败必须保持 Fail-closed。

响应不得包含 credential reference 名称或路径、credential value、CA 路径、原始 PATCH、workload UID、resourceVersion、请求头或幂等键。相同现场状态和相同请求必须返回同一 `report_sha256`；Evidence、配置或部署摘要发生变化必须重新演练。Operator 必须把完整响应保存到受控变更单。该报告不授权开启功能开关，也不授权执行 Action。

## 10. 一次性只读实时探测与最终人工 Go/No-Go

最终 handoff rehearsal 通过后，生产执行配置和 Kill Switch 仍保持禁用/`ENGAGED`。现场只能通过一个独立的、只读的短时启动步骤开启实时探测：

- `KUBERNETES_PRODUCTION_LIVE_PROBE_ENABLED=true`
- `KUBERNETES_PRODUCTION_LIVE_PROBE_ACKNOWLEDGEMENT=I_ENABLE_READ_ONLY_OOM_PILOT_LIVE_PROBE_V1`
- `remediation.kubernetes_production_execution.enabled=false`

该独立开关只允许加载 Preflight 与 Production 两套不同凭据，并以各自身份对同一精确 Deployment 执行一次 HTTPS GET。TLS 验证必须开启、redirect 必须关闭、响应必须有界。成功时总计必须是 `network_call_count=2`、`kubernetes_read_count=2`、`kubernetes_write_count=0`、`patch_request_count=0` 和 `dry_run_request_count=0`。任何凭据、TLS、目标 UID、resourceVersion、generation、container 或当前 memory limit 不一致都必须 Fail-closed。

只有 Ceremony 中记录的精确 Executor 可以使用唯一 `Idempotency-Key` 调用：

```text
POST /production-actions/{approval_id}/pilot-live-readiness-probe
```

请求必须绑定最新 handoff `report_sha256`、完整 handoff 请求，并包含：

```text
I_AUTHORIZE_READ_ONLY_OOM_PILOT_LIVE_PROBE_V1
```

平台必须先持久化唯一 `RUNNING` probe claim，再执行外部 GET。成功或明确失败分别持久化为 `PASSED` 或 `FAILED`；完全相同的重放只返回既有记录，绝不再次访问 Kubernetes。进程在 claim 后终止时，记录保持 `RUNNING`，禁止自动重试实时探测，必须恢复现场安全状态并人工审阅。该接口不创建 Action Execution Claim、不预留 Pilot 预算、不启动 Verification、不修改功能开关或 Kill Switch。

实时探测 `PASSED` 后，由 Approver 或 Admin 作为最终复核人提交独立决定。该复核人必须不同于精确 Executor、原 Approval 决策人、Ceremony 复核人和三名 handoff owner：

```text
POST /production-actions/{approval_id}/pilot-go-no-go-decision
GET /production-actions/{approval_id}/pilot-go-no-go-decision
```

POST 请求必须绑定最新 `probe_record_sha256`，显式复核 live probe、monitoring、rollback、reconciliation 和 controlled change window，并包含：

```text
I_CONFIRM_OOM_PILOT_FINAL_GO_NO_GO_DECISION_V1
```

`GO` 只在 live probe 与全部上游证据仍有效时创建，最长有效期五分钟。其唯一含义是 `allows_guarded_enablement_procedure=true`；同时必须保持 `authorizes_action_execution=false`、`automatic_enablement_allowed=false`、`feature_gate_changed=false`、`kill_switch_changed=false` 和所有写入/Claim/预算/Verification 计数为零。`GO` 不打开生产开关、不解除 Kill Switch，也不允许直接 Resume。`NO_GO` 是无过期时间的安全终态，可在实时探测失败后记录，并始终保持 `allows_guarded_enablement_procedure=false`。

最终决定 GET 是纯只读查询。响应不得包含凭据、credential reference、Authorization、原始 PATCH、workload UID、resourceVersion、幂等键或完整 handoff 请求。实时探测开关默认关闭；关闭时不得读取凭据或 CA、不得创建 Go/No-Go 数据库。完成现场决定后应立即关闭只读实时探测开关。

## 11. 执行顺序

1. 创建 Preflight Artifact，确认补丁摘要、目标和过期时间。
2. 审批人完成 Approval；审批人与执行人职责分离。
3. 保持 Kill Switch 为 `ENGAGED`，完成 Go/No-Go 双人复核。
4. 执行一次零写入启用演练并确认预算为 `available`。
5. 由不同的复核人记录一次不可变 Pilot 启用仪式证据。
6. 保持开关禁用且 Kill Switch 为 `ENGAGED`，获取最终 Pre-enable Evidence Pack。
7. 由 Ceremony 中精确记录的 Executor 对同一 `evidence_sha256` 完成一次零写入签署，并将响应保存到受控变更单。
8. 由同一精确 Executor 完成最终零写入 handoff rehearsal，绑定部署摘要、凭据/TLS 元数据与三名独立 handoff owner；将响应保存到受控变更单。
9. 在生产执行配置仍禁用且 Kill Switch 仍为 `ENGAGED` 时，精确 Executor 运行一次持久化只读 live probe；必须是 `PASSED` 且两个凭据各完成一次 GET。
10. 独立 Approver 或 Admin 对最新 `probe_record_sha256` 持久化一次最终 `GO`；该决定最长五分钟且只允许进入后续受控启用步骤。
11. 当前 Runbook 到此仍不授权启用。只有下一阶段独立实现并验证的一次性受控启用程序，才可以消费未过期 `GO`；在此之前不得修改生产执行开关或解除 Kill Switch。
12. 未来受控启用程序必须重新确认全部现场状态未变化，才可按变更流程原子更新 Kill Switch 为 `DISENGAGED_FOR_OOM_PILOT_V1`。
13. Executor 使用唯一 `Idempotency-Key` 调用一次 Resume。
14. ActionRuntime 先创建唯一、持久化的 `RUNNING` Action Execution Claim。
15. ActionRuntime 将 `ceremony.status=ready` 原子切换为 `ceremony.status=activated`，并绑定该 Execution ID、执行幂等键与激活摘要。
16. 只有本次新建 Claim 与本次新激活 Ceremony 的调用方才可继续；激活重放永远不能获得 Executor 调用权限。
17. ActionRuntime 在 Ceremony 激活成功后、调用生产 Executor 前原子预留 Pilot 预算。
18. Executor 在真实 PATCH 前原子消费 Pilot 预算。
19. 不论响应成功、失败或超时，立即恢复 Kill Switch 为 `ENGAGED`。
20. 查询 Approval Workflow 和 Incident Workflows，保存 Ceremony、Action Execution 与 Verification 审计结果。

如果进程在 Ceremony 激活后、预算预留前终止，持久化状态将保持 `RUNNING + ACTIVATED`。重启后的相同 Resume 只能返回已有执行状态，不得自动预留预算或调用 Executor；Operator 必须按 `INDETERMINATE` 风险执行人工对账。Ceremony 缺失、过期、摘要或绑定不一致时，Execution 必须持久化为失败，预算和 Kubernetes 调用都必须保持为零。

两个现有只读查询都会返回 `production_pilot_ceremony_audit`。该快照必须确认 `binding_consistent=true`、`clock_consistent=true` 和 `automatic_resume_allowed=false`，并只包含 Ceremony、Pilot、Approval、Incident、Artifact、Contract、Execution 的 ID、摘要、主体、时间与固定恢复动作。不得包含原始 PATCH、workload UID、resourceVersion、幂等键、请求头或凭据。

恢复状态按以下规则处理：

- `ready_for_first_resume`：仍按 Runbook 完成一次人工 Resume，不代表系统可自动执行。
- `claim_not_activated`：Claim 已存在但 Ceremony 未激活，立即恢复 Kill Switch，禁止重试 Resume，进入人工对账。
- `activated_outcome_unconfirmed`：保持 `RUNNING + ACTIVATED`，按真实写入结果不明确处理。
- `execution_indeterminate` 或 `inconsistent`：执行只读 Kubernetes 状态确认，并通过 Reconciliation API 对账。
- `execution_succeeded`：禁止重试 Resume，只观察 Exactly-once Verification。
- `execution_failed`：禁止重试 Resume，审阅持久化失败结果；需要再次尝试时必须创建新的 Pilot、Preflight、Approval 与 Ceremony。

## 12. 立即终止条件

出现任一情况立即恢复 `ENGAGED`，不得重试真实 PATCH：

- API 返回 `INDETERMINATE`、503 或网络超时。
- Deployment `resourceVersion`、UID、generation 或内存配置发生漂移。
- 重复 dry-run 与不可变 Artifact 不一致。
- Incident、Approval、Action Execution 或 Verification 链接不一致。
- Pod 可用率下降、重启数继续增长或出现新的高严重性告警。
- 试点窗口过期、Operator 不在白名单或审计主体不一致。
- Pilot 预算已经由另一个 Execution 预留或消费。
- Pilot 启用仪式证据缺失、过期、摘要不匹配或绑定了不同 Approval、Artifact、复核人、Executor。
- Ceremony 已绑定其他 Execution，或激活重放试图重新取得 Executor 调用权限。
- 最终 Evidence Pack 存在阻断项、摘要变化、签署主体不是精确 Executor，或签署结果被误用为启用/执行授权。
- 最终 handoff rehearsal 存在阻断项、部署摘要变化、凭据引用不可用或相同、TLS 策略不一致、职责分离失效，或报告被误用为启用/执行授权。
- live probe claim 停留在 `RUNNING`、两个 GET 未全部成功、两套凭据相同、实时 Deployment 绑定发生漂移、最终 Go/No-Go 复核人职责不分离，或 `GO` 已过期。

## 13. 对账与回滚

- `INDETERMINATE`：先从 Kubernetes 读取实际 Deployment 状态，禁止重新 Resume。
- `claim_not_activated`、`activated_outcome_unconfirmed` 或 `inconsistent`：执行 `engage_kill_switch`、`do_not_retry_resume`、`inspect_deployment_state_read_only`、`reconcile_existing_action_execution`，只有确认成功后才能启动 Verification。
- 已生效：使用 Artifact 中记录的 rollback memory limit，在独立变更审批下人工回滚。
- 未生效：通过 Reconciliation API 记录失败结果，不启动 Verification。
- 对账成功：记录外部证据并进入 Exactly-once Verification。
- 预算状态不得通过自动流程重置；需要新的 Pilot ID 和新的变更审批才能再次尝试真实写入。

## 14. 收尾

- Kill Switch 最终必须为 `ENGAGED`。
- 生产执行功能开关恢复为禁用。
- 保存 Pilot ID、变更单、Ceremony ID、审批人、复核人、执行人、执行 ID、Verification ID 和最终 Incident 状态。
- 完成复盘后才能规划下一次试点；不得把本次成功视为自动扩大范围的授权。
