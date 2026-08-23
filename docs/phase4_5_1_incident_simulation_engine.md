# Phase 4.5.1 Incident Simulation Engine

## Goal

Create a deterministic production incident replay framework for AI Reliability Platform.

Flow:

Alert

→ Incident Creation

→ Investigation Replay

→ Evidence Collection

→ RCA Evaluation

→ Remediation

→ Verification

→ Memory Update

## Scenario Model

Supported scenarios:

- payment-api OOM
- database connection exhaustion
- latency degradation
- pod crash loop

## Components

```
services/simulator/
├── incident_generator.py
├── signal_generator.py
├── evidence_fixture.py
└── scenario_runner.py
```

## Replay Objective

Measure:

- RCA accuracy
- investigation latency
- evidence completeness
- remediation correctness
- regression detection
