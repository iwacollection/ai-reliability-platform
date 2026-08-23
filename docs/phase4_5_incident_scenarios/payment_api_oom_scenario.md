# Payment API OOM Incident Scenario

## Trigger

```
payment-api
container memory usage continuously increases
```

## Expected Alert

```
ContainerMemoryUsageHigh
or
OOMKilled
```

## Investigation Evidence

Kubernetes:

```
pod restart count increased
container terminated reason: OOMKilled
```

Prometheus:

```
container_memory_usage_bytes rising
working_set memory abnormal
```

Loki:

```
out of memory
allocation failed
```

OpenTelemetry:

```
request latency increase
trace failures increase
```

## Expected RCA

```
Cause:
application memory leak

Confidence:
> 0.85
```

## Remediation

Approved action:

```
restart pod
```

## Verification

Success criteria:

- pod ready
- memory stable
- error rate recovered
- latency within SLO

Failure:

```
trigger rollback
```

## Learning Output

Store:

```
incident pattern
root cause
fix action
verification result
```
