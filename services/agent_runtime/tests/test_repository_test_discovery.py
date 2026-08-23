"""Repository-level pytest discovery contract."""

from pathlib import Path


def test_repository_contains_runtime_test_layout() -> None:
    root = Path(__file__).resolve().parents[3]

    assert (root / "services" / "agent_runtime" / "tests").exists()
    assert (root / "services" / "gateway" / "tests").exists()


def test_pyproject_exists() -> None:
    root = Path(__file__).resolve().parents[3]

    assert (root / "pyproject.toml").exists()
