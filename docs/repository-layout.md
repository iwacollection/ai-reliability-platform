# Repository Layout

本文件定义 AI Reliability Platform 的工作树职责，目标是保证仓库长期只有一套清晰的源码事实源。

## Canonical Directories

| Path | Responsibility |
| --- | --- |
| `services/` | 可独立启动的 Gateway、Agent Runtime、Connector 等服务边界 |
| `packages/` | 跨服务复用的公共模型、协议与工具库 |
| `infrastructure/` | IaC、Kubernetes、云基础设施与部署配置 |
| `scripts/` | 开发、诊断、迁移和验证辅助脚本 |
| `docs/` | 当前有效的架构、开发、运行文档 |
| `data/` | 小型、确定性、可复现的 JSON/YAML fixture 或示例输入 |
| `.github/workflows/` | CI / repository validation / delivery orchestration |

## What Must Not Be Tracked

以下内容属于运行产物或历史副本，不进入 Git 工作树：

- `logs/`
- `reports/`
- `data/*.db`, `*.sqlite*`
- `*.bak`
- `architecture_v2_archive/` 或其他 archive/temporary_backups 目录
- PowerShell/curl 误生成的参数名文件，例如 `-Method`, `-Uri`, `-ContentType`

历史代码通过 Git commit、tag、branch 查找，不在仓库中再维护一份 archive。

## Tests

测试与模块共置，例如：

```text
services/<service>/tests/test_*.py
packages/<package>/tests/test_*.py
```

Pytest 从仓库根自动发现，不要求额外的根 `tests/` 目录。

## Generated Evidence and Benchmarks

Scenario、benchmark、replay 所需的**静态输入 fixture**可以版本化；运行过程中产生的结果、trace DB、报告和日志应作为 CI artifact 或外部可观测数据保存。

## Change Rule

新增顶层目录前必须能够说明它不与上述职责重叠；如果只是临时输出、备份或实验副本，应使用本地 ignored 路径而不是新增工作树事实源。
