# Kubernetes Pod OOM Expert Skill

## Trigger

- OOMKilled
- ContainerMemoryHigh
- MemoryPressure

## Investigation Plan

1. Check container termination status.
2. Check memory limit and usage.
3. Compare RSS and heap growth.
4. Check node memory pressure.

## Decision Rules

- Heap growth -> possible JVM heap issue.
- RSS growth with stable heap -> possible native memory leak.
- Node pressure -> possible node memory issue.

## Safety

Never automatically:
- restart pod
- increase memory limit
- modify workload resources

Require approval for remediation.
