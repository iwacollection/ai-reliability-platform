# Phase 5.8：Azure Real Environment Bootstrap + Production Connector Activation

## Goal

Activate real Azure production validation path:

Azure Subscription

→ Azure AD Authentication

→ AKS Cluster

→ Kubernetes MCP

→ Prometheus / Loki / OpenTelemetry

→ Evidence Graph

→ RCA Validation

## Objectives

### 1. Azure Runtime Bootstrap

- Validate Azure subscription access
- Configure Entra ID authentication
- Configure Service Principal / Managed Identity
- Validate RBAC permissions

### 2. Production Connector Activation

Enable:

- Azure Resource Graph connector
- AKS discovery connector
- Kubernetes MCP connector
- Prometheus connector
- Loki connector
- OpenTelemetry connector

### 3. Real Incident Validation

Scenario:

payment-api OOM

Flow:

Alert

↓

Incident Creation

↓

Agent Investigation

↓

MCP Tool Calls

↓

Evidence Graph

↓

RCA Confidence Ranking

↓

Approval

↓

Remediation

↓

Verification

## Security Boundary

Read path:

- Azure Reader
- Kubernetes read-only RBAC
- Observability query permissions

Write path:

Approval required before remediation actions.

## Deliverables

- Azure runtime configuration
- Production connector health checks
- Real AKS discovery
- End-to-end RCA replay
