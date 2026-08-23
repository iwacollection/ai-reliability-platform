# Phase 5.1 Azure AD Authentication + Azure Resource Graph + AKS Discovery Connector

## Goal

Connect AI Reliability Platform to real Azure environments.

Flow:

Azure AD Authentication

-> Access Token

-> Azure Resource Graph

-> AKS Discovery

-> MCP Tool Registry

-> Evidence Source

## Components

### Azure Authentication

`services/connectors/azure/auth.py`

Responsibilities:

- acquire Azure access token
- support service principal / managed identity
- expose authenticated Azure client context

### Azure Resource Graph Connector

`services/connectors/azure/resource_graph.py`

Responsibilities:

- query Azure resources globally
- discover AKS clusters
- collect subscription/resource metadata

### AKS Discovery Connector

`services/connectors/azure/aks.py`

Responsibilities:

- discover AKS clusters
- collect cluster metadata
- register Kubernetes investigation targets

## Investigation Flow

Alert

-> Incident

-> Azure Resource Discovery

-> AKS Context

-> Kubernetes MCP

-> Evidence Graph

-> RCA

## Security Boundary

Read operations:

- Reader role
- Resource Graph query
- AKS metadata discovery

Write operations:

- require Approval Gate
- execute through Action Runtime
- audit all actions
