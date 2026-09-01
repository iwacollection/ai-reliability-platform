# AI Reliability Platform

An enterprise AI Reliability Platform for building an **evidence-driven, human-in-the-loop, Agent-first SRE runtime**.

The project is designed around a complete reliability loop rather than a single LLM call:

```text
Alert / Conversation
        ↓
Incident Context
        ↓
Investigation
        ↓
Evidence
        ↓
Agent Decision
        ↓
Approval
        ↓
Action
        ↓
Verification
        ↓
Audit / Evaluation
        ↓
Memory / Evolution
```

## 1. What the platform solves

Traditional monitoring automation is usually written as fixed workflows:

```text
Alert A → Script B → Action C
```

This works for known cases but becomes difficult to maintain when the number of alerts, tools, infrastructure types and remediation strategies grows.

This project uses an Agent-first architecture:

- the **Agent decides what to investigate next**;
- **Evidence provides facts** instead of allowing the model to guess;
- **MCP / Tools expose capabilities** without hardcoding business workflows;
- **Harness controls Agent execution** so it cannot loop forever;
- **Action is structured and policy-controlled** instead of arbitrary shell execution;
- **Approval provides human control** for risky changes;
- **Verification proves recovery** instead of trusting the action result;
- **Audit records the complete decision and execution trail**;
- **Evaluation / Simulator / Replay make Agent behavior testable**.

## 2. Main capabilities

- Alert Noise Reduction
- Root Cause Analysis
- AI Auto Healing
- Incident Investigation
- Workflow / Agent Orchestration
- Multi-Agent Runtime
- MCP Tool Integration
- Evidence-driven Diagnosis
- Human-in-the-loop Approval
- Safe Action Execution
- Verification and Recovery Validation
- Agent Memory
- Scenario Replay and Evaluation
- Sandbox Execution

## 3. Architecture at a glance

```text
External Systems
   │
   ▼
┌───────────────┐
│    Gateway    │  Event ingestion / parsing / normalization
└───────┬───────┘
        │ StandardEvent
        ▼
┌─────────────────────────────────────────────────────┐
│                 Agent Runtime                       │
│                                                     │
│ Context → Agent Loop → Evidence → Tool/MCP          │
│    ↑              │                    │             │
│    │              ▼                    ▼             │
│ Memory        Orchestration        Connectors        │
│                   │                                  │
│                   ▼                                  │
│              Action Plan                             │
│                   │                                  │
│          Policy / Approval                           │
│                   │                                  │
│                   ▼                                  │
│             Action Runtime                           │
│                   │                                  │
│                   ▼                                  │
│              Verification                            │
│                   │                                  │
│              Audit / Eval                            │
└─────────────────────────────────────────────────────┘
        │                 │                 │
        ▼                 ▼                 ▼
   Sandbox           Simulator           Cloud
```

## 4. Repository structure

```text
services/
├── gateway/          External event ingestion and normalization
├── agent_runtime/    Core Agent runtime and reliability control plane
├── evidence/         Evidence collection / normalization
├── connectors/       External-system adapters
├── mcp/              Tool protocol and capability exposure
├── sandbox/          Isolated execution
├── harness/          Agent execution guardrails
├── simulator/        Incident scenarios and replay
└── cloud/            Cloud / infrastructure capabilities

packages/
├── common/           Shared generic/domain utilities
├── models/           Cross-service contracts
└── llm_sdk/          Provider-neutral LLM access

architecture_v2_archive/
└── Historical architecture and evaluation implementations
```

## 5. Important design principles

### Agent-first

The platform does not hardcode every investigation workflow. The Agent chooses the next useful step according to the current objective and available evidence.

### Evidence-driven

The Agent must distinguish facts from hypotheses. External system observations are converted into traceable Evidence objects before being used as the basis for decisions.

### Action ≠ Investigation

Read-only investigation and state-changing remediation have different risk levels and therefore different permissions, policies, approvals and execution paths.

### Verification is mandatory

A successful API call is not the same as a recovered service. Every remediation should define observable postconditions and verify them independently.

### Runtime controls the model

The model can decide, but the runtime controls maximum turns, tool calls, time, retries, permissions, checkpoints and termination.

## 6. Example incident flow

For a Kubernetes CPU alert:

```text
AlertManager
  ↓
Gateway
  ↓
StandardEvent
  ↓
Incident Context
  ↓
Agent notices evidence is insufficient
  ↓
Query metrics
  ↓
Query Pod / resource limits
  ↓
Query logs
  ↓
Query recent changes
  ↓
Build and test hypotheses
  ↓
Generate structured remediation plan
  ↓
Policy / Approval
  ↓
Execute action
  ↓
Verify Pod + error rate + latency + alert state
  ↓
Resolve or escalate
```

The important difference is that the system does **not** blindly implement:

```text
CPU high → scale out
```

because CPU pressure can have many causes: traffic growth, GC pressure, retry storms, application bugs, CPU limits, node contention, or a recent deployment.

## 7. Documentation

### Architecture

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — overall architecture, Incident lifecycle, module boundaries and production evolution.
- [`docs/MODULES.md`](docs/MODULES.md) — module-by-module responsibilities, engineering problems and design trade-offs.

### Agent Runtime

- [`docs/AGENT_RUNTIME.md`](docs/AGENT_RUNTIME.md) — Agent Loop、主动发现问题、Evidence Gap、Harness、Context、Memory、Tool Registry、MCP、Retry、Fallback、Checkpoint、Idempotency、Approval、Multi-Agent 和 Verification。

### Production Engineering

- [`docs/PRODUCTION_DESIGN.md`](docs/PRODUCTION_DESIGN.md) — 生产可靠性、安全边界、权限、Prompt Injection、防重、并发、限流、降级、可观测性、灾备、SLO 和生产发布策略。

阅读顺序建议：

```text
README
  ↓
ARCHITECTURE
  ↓
MODULES
  ↓
AGENT_RUNTIME
  ↓
PRODUCTION_DESIGN
  ↓
源码 + Tests + Scenario Replay
```

When adding a new major capability, update the relevant architecture documentation together with the implementation and tests.

## 8. Technology stack

- Python 3.12
- FastAPI
- Pydantic v2
- uv
- Kubernetes
- OpenTelemetry
- GitHub Actions

## 9. Project status

🚧 Under Development

The repository contains working runtime components and validation scenarios while the platform continues to evolve toward a production-grade reliability runtime.
