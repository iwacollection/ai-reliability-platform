# AI Reliability Platform

AI Reliability Platform 是面向 SRE / 运维场景的 AI Reliability Runtime，当前产品主线是：

```text
Alert / Conversation
        ↓
Incident
        ↓
Investigation
        ↓
Evidence
        ↓
RCA
        ↓
Human Approval
        ↓
Action
        ↓
Verification
```

核心原则：**ChatOps-first、Evidence-driven、Human-in-the-loop**。低风险场景可逐步演进到受控自动化，不把未经验证的推理直接变成生产动作。

## Repository Map

```text
services/          可独立运行的服务与 Runtime
packages/          跨服务复用的共享 Python 包
infrastructure/    IaC、部署与运行环境配置
scripts/           开发、验证、运维辅助脚本
docs/              当前有效的设计、运行和开发文档
data/              仅允许小型、可复现的静态样例/fixture
.github/workflows/ CI 与仓库验证
```

运行生成的 SQLite、日志、报告、临时备份不属于源码，不应提交到 Git。

详细目录规则见 `docs/repository-layout.md`。

## Major Capabilities

仓库当前包含的能力包括：

- Agent Runtime / Investigation workflow
- Evidence collection and evidence-driven reasoning
- Incident / conversation context
- Approval and controlled action execution
- Scenario replay / benchmark / validation harness
- MCP tool integration
- Kubernetes / cloud evidence connectors
- ChatOps integration
- Observability and evaluation support

## Development

要求 Python 3.12 与 `uv`。

```bash
uv sync
uv run ruff check .
uv run mypy .
uv run pytest
```

测试与对应模块共置，由 Pytest 自动发现 `test_*.py`，不维护一个虚假的根 `tests/` 入口。

## Repository Rules

- 不在工作树保留 `archive/backup/temporary_backups` 作为第二份 Git 历史。
- 不提交运行生成的数据库、日志、报告或 `.bak` 文件。
- `services/` 放可运行边界；`packages/` 放共享库，避免职责重复。
- Scenario / benchmark 的静态 fixture 可以版本化，但运行结果必须由 CI artifact 或外部存储承载。
- 结构性变化必须同步更新文档与 CI 验证规则。

## Status

项目持续演进中。当前仓库已经具备完整 Reliability Runtime 的多个组成部分；生产发布、安全强化与平台化能力继续通过独立 PR 演进，避免与日常仓库结构治理混在一起。
