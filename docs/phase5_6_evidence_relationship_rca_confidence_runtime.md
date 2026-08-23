# Phase 5.6：Evidence Relationship Engine + RCA Confidence Ranking Runtime

## Goal

Upgrade the platform from evidence collection to evidence-driven reliability reasoning.

Pipeline:

Incident

↓

Evidence Collector

↓

Evidence Relationship Engine

↓

Hypothesis Engine

↓

RCA Confidence Ranking

↓

Remediation Decision

## Architecture

```
Agent Runtime
    |
    v
Evidence Graph Runtime
    |
    +-- Kubernetes Evidence
    +-- Prometheus Metrics
    +-- Loki Logs
    +-- OpenTelemetry Trace
    |
    v
Relationship Engine
    |
    v
Hypothesis Ranking
    |
    v
RCA Result
```

## Components

services/evidence/

- graph.py
- relationship.py
- correlation.py
- scorer.py

services/rca/

- hypothesis_engine.py
- confidence.py
- ranking.py

## Evidence Relationship

Example:

```
High Latency
    |
    +-- Memory Growth
    |
    +-- OOMKilled Event
    |
    +-- OutOfMemoryError Log
    |
    +-- Slow Trace Span
    |
    v
Memory Leak Hypothesis
```

## Confidence Scoring

Evidence contributes weighted confidence:

- Kubernetes OOMKilled: +30
- Memory increasing trend: +25
- Application OOM log: +25
- Trace degradation: +10
- Historical RCA match: +10

Output:

```json
{
  "root_cause": "memory leak",
  "confidence": 0.96,
  "evidence_count": 5
}
```

## Production Validation Scenario

payment-api OOM:

1. Detect incident
2. Query AKS Kubernetes MCP
3. Collect Prometheus memory metrics
4. Collect Loki application errors
5. Correlate OpenTelemetry traces
6. Build Evidence Graph
7. Rank RCA hypotheses
8. Generate remediation recommendation

## Security

Investigation path:

- read-only evidence access
- query-only tools

Remediation path:

RCA

↓

Risk Policy

↓

Human Approval

↓

Action Runtime

## Success Criteria

The agent should provide:

- explainable RCA
- evidence chain
- confidence score
- supporting observations
- regression-testable result
