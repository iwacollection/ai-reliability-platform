# Enterprise CI Validation Baseline

## Purpose

Define the minimum validation gate before merging code into main.

## Pipeline Stages

1. Checkout source
2. Provision Python 3.12 runtime
3. Install uv dependency environment
4. Run static validation
   - Ruff lint
   - Mypy type checking
5. Run automated tests
   - Pytest

## Merge Protection Goal

Future branch protection should require this workflow to pass before merge.

## Extension Roadmap

- Coverage threshold
- Security scanning
- Dependency vulnerability scanning
- Container image build validation
- Deployment workflow integration
