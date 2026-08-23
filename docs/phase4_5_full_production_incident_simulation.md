# Phase 4.5: Full Production Incident Simulation

## Goal

Validate the complete AI Reliability Operating Loop with a production-like incident scenario.

Scenario: payment-api OOM

```
Alert
  |
  v
Incident Creation
  |
  v
Investigation Agent
  |
  v
Evidence Collection
  |
  v
RCA Reasoning
  |
  v
Remediation Plan
  |
  v
Human Approval
  |
  v
Action Runtime
  |
  v
Verification Agent
  |
  +---- success -> Incident Close -> Memory Update
  |
  +---- failure -> Rollback -> Recovery Validation
```

## Simulation Components

### Signal Generation

Generate:

- Kubernetes OOMKilled event
- container memory metric increase
- application error log
- latency degradation

### Investigation

Agent should collect:

- Kubernetes events
- Prometheus metrics
- Loki logs
- OpenTelemetry traces

### Reasoning

Produce:

- Evidence Graph
- Hypothesis ranking
- RCA confidence

Example:

```
Root Cause: memory leak
Confidence: 0.91
```

### Remediation

Approved action:

```
restart unhealthy pod
```

Executed through:

```
Approval
 -> Action Runtime
 -> Kubernetes MCP Tool
```

### Verification

Validate:

- pod readiness
- memory recovery
- error rate recovery
- latency recovery

### Learning

Persist:

- incident pattern
- root cause
- successful remediation
- verification result

Used by:

- Incident Memory
- BM25 retrieval
- Future RCA improvement
