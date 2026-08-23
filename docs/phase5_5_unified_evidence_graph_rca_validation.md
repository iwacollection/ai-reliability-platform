# Phase 5.5: Unified Evidence Graph + Production RCA Validation

## Goal

Merge Kubernetes, Prometheus, Loki and OpenTelemetry signals into a unified evidence graph and validate production RCA reasoning.

## Architecture

```
Kubernetes MCP
        \
         \
Prometheus ----> Evidence Collector ----> Unified Evidence Graph ----> Hypothesis Engine ----> RCA Ranking
         /
        /
Loki MCP

OpenTelemetry Trace
```

## Evidence Model

All production signals are normalized into:

- source
- timestamp
- resource
- observation
- confidence
- relationships

## Evidence Relationship

Example:

```
Alert
 |
 v
Pod Restart
 |
 +---- Kubernetes Event: OOMKilled
 |
 +---- Prometheus: memory usage increasing
 |
 +---- Loki: OutOfMemoryError
 |
 +---- OTel: latency degradation
 |
 v
RCA: Memory Leak
Confidence: 0.96
```

## RCA Validation

Evaluation dimensions:

- Evidence coverage
- Root cause accuracy
- Confidence calibration
- Historical incident similarity
- Remediation correctness

## Production Scenario

payment-api OOM:

```
Alert
 -> Incident
 -> AKS Discovery
 -> Kubernetes Evidence
 -> Prometheus Metrics
 -> Loki Logs
 -> OTel Trace
 -> RCA
 -> Remediation Decision
 -> Verification
```

## Next Implementation

- Evidence Relationship Engine
- RCA confidence ranking
- Production AKS validation workflow
- Incident replay comparison
