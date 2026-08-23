# Phase 5：Azure Production Validation

## Goal

Validate AI Reliability Platform with real Azure infrastructure.

Target flow:

```text
Azure Subscription
        |
        v
Azure AD Authentication
        |
        v
AKS Cluster
        |
 +------+----------------+
 |                       |
 v                       v
Kubernetes API      Observability
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
     Prometheus        Loki       OpenTelemetry
          |
          v
    Evidence Graph
          |
          v
 Investigation Agent
          |
          v
 Remediation + Verification
```

## Phase 5.1 Azure Connector

Implement:

- Azure AD authentication
- Azure Resource Graph connector
- AKS discovery
- Subscription/resource inventory
- Evidence collector integration

## Phase 5.2 Kubernetes Production Connector

Connect:

- Kubernetes API
- Pod events
- Deployment status
- Container state
- Resource usage

## Phase 5.3 Observability Integration

Connect:

- Prometheus query API
- Loki query API
- OpenTelemetry traces

Generate unified evidence:

```text
Metric
 |
Log
 |
Trace
 |
Kubernetes Event
 |
 v
Evidence Graph
```

## Phase 5.4 Real Incident Validation

Scenario:

```text
payment-api OOM

Alert
 -> Investigation
 -> Evidence Collection
 -> RCA
 -> Approval
 -> Remediation
 -> Verification
```
