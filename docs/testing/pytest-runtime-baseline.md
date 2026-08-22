# Pytest Runtime Discovery Baseline

## 问题

仓库测试并非集中在根目录 `tests/`，实际测试分布在：

- `services/agent_runtime/tests`
- `services/gateway/tests`

原配置只扫描：

```toml
testpaths = ["tests"]
```

导致执行 pytest 时无法发现已有测试。

## 修复

统一通过 `pyproject.toml` 配置：

- 扩展 `pythonpath`
- 扩展 `testpaths`
- 排除虚拟环境目录

## 验收标准

- pytest 能发现 services 下测试
- 不扫描 `.venv`
- CI 可以复用同一配置

## 常见坑

- 不要复制依赖目录中的测试
- 不要让本地 IDE 配置覆盖 pytest 配置
- 新增测试必须放入约定测试目录
