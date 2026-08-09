import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

import httpx

from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    InvestigationLLMGatewayAdapter,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationLimits,
)
from services.agent_runtime.app.investigation.reasoner import (
    LLMInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
)
from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)
from services.agent_runtime.app.llm.client import (
    LLMClient,
)
from services.agent_runtime.app.llm.gateway.gateway import (
    LLMGateway,
)
from services.agent_runtime.app.llm.gateway.rate_limiter import (
    RateLimiter,
)
from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesTool,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.mock.echo import (
    EchoTool,
)
from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusTool,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


_RESOURCE_NAMES = (
    "deployment-regression-api",
    "memory-saturation-api",
    "restart-storm-api",
    "memory-pressure-api",
    "payment-api",
)


_RCA_FIXTURES = {
    "payment-api": {
        "root_cause": (
            "Container memory limit exceeded causing OOMKilled."
        ),
        "confidence": 0.90,
    },
    "memory-pressure-api": {
        "root_cause": (
            "Container memory pressure exhausted available headroom."
        ),
        "confidence": 0.82,
    },
    "restart-storm-api": {
        "root_cause": (
            "Repeated OOMKilled events caused a pod restart storm."
        ),
        "confidence": 0.88,
    },
    "memory-saturation-api": {
        "root_cause": (
            "Container memory usage remained near its configured limit."
        ),
        "confidence": 0.70,
    },
    "deployment-regression-api": {
        "root_cause": (
            "Deployment regression increased memory usage "
            "and caused OOMKilled."
        ),
        "confidence": 0.93,
    },
}


_INVESTIGATION_CONCLUSIONS = {
    "payment-api": {
        "root_cause": (
            "Container memory limit exceeded causing OOMKilled."
        ),
        "confidence": 0.95,
    },
    "memory-pressure-api": {
        "root_cause": (
            "Container working set exhausted available memory headroom."
        ),
        "confidence": 0.92,
    },
    "restart-storm-api": {
        "root_cause": (
            "Repeated OOMKilled events caused a pod restart storm."
        ),
        "confidence": 0.94,
    },
    "memory-saturation-api": {
        "root_cause": (
            "Sustained memory saturation left minimal headroom "
            "below the configured limit."
        ),
        "confidence": 0.85,
    },
}


_KUBERNETES_FIXTURES = {
    "payment-api": {
        "oom_killed": True,
        "restart_count": 7,
        "ready": False,
        "state_reason": "CrashLoopBackOff",
    },
    "memory-pressure-api": {
        "oom_killed": True,
        "restart_count": 3,
        "ready": False,
        "state_reason": "CrashLoopBackOff",
    },
    "restart-storm-api": {
        "oom_killed": True,
        "restart_count": 25,
        "ready": False,
        "state_reason": "CrashLoopBackOff",
    },
    "memory-saturation-api": {
        "oom_killed": False,
        "restart_count": 0,
        "ready": True,
        "state_reason": None,
    },
    "deployment-regression-api": {
        "oom_killed": True,
        "restart_count": 6,
        "ready": False,
        "state_reason": "CrashLoopBackOff",
    },
}


_PROMETHEUS_FIXTURES = {
    "payment-api": {
        "working_set": 520_000_000.0,
        "memory_limit": 536_870_912.0,
        "restart_count": 7.0,
    },
    "memory-pressure-api": {
        "working_set": 1_020_000_000.0,
        "memory_limit": 1_073_741_824.0,
        "restart_count": 3.0,
    },
    "restart-storm-api": {
        "working_set": 500_000_000.0,
        "memory_limit": 536_870_912.0,
        "restart_count": 25.0,
    },
    "memory-saturation-api": {
        "working_set": 1_050_000_000.0,
        "memory_limit": 1_073_741_824.0,
        "restart_count": 0.0,
    },
    "deployment-regression-api": {
        "working_set": 900_000_000.0,
        "memory_limit": 1_073_741_824.0,
        "restart_count": 6.0,
    },
}


class InvestigationEvaluationProvider(
    BaseLLMProvider
):
    """
    Deterministic offline provider for Investigation evaluation only.

    It serves the same shared LLMGateway used by:

    - NoiseAgent
    - RCAAgent
    - HealingAgent
    - LLMInvestigationReasoner

    No production provider, credential or endpoint is used.
    """

    def __init__(
        self,
    ) -> None:
        self.requests: list[
            ChatRequest
        ] = []

    @property
    def name(
        self,
    ) -> str:
        return "investigation-evaluation"

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        self.requests.append(
            request
        )

        system_prompt = (
            request.system_prompt
            .strip()
            .lower()
        )

        if (
            "bounded sre investigation reasoner"
            in system_prompt
        ):
            payload = (
                self._investigation_response(
                    request.user_prompt
                )
            )

        elif (
            "sre rca assistant"
            in system_prompt
        ):
            payload = (
                self._rca_response(
                    request.user_prompt
                )
            )

        elif (
            "sre healing assistant"
            in system_prompt
        ):
            payload = (
                self._healing_response(
                    request.user_prompt
                )
            )

        else:
            payload = {
                "noise": False,
                "confidence": 0.95,
                "reason": (
                    "Deterministic evaluation incident"
                ),
            }

        return ChatResponse(
            content=json.dumps(
                payload
            ),
            model=(
                "investigation-evaluation-v1"
            ),
            prompt_tokens=20,
            completion_tokens=20,
            total_tokens=40,
        )

    @staticmethod
    def _rca_response(
        prompt: str,
    ) -> dict[str, Any]:
        resource = _resource_from_text(
            prompt
        )

        fixture = _RCA_FIXTURES[
            resource
        ]

        return {
            "root_cause": fixture[
                "root_cause"
            ],
            "confidence": fixture[
                "confidence"
            ],
            "evidence": [
                (
                    "deterministic pipeline "
                    "evaluation evidence"
                ),
            ],
        }

    @staticmethod
    def _healing_response(
        prompt: str,
    ) -> dict[str, Any]:
        resource = _resource_from_text(
            prompt
        )

        return {
            "action": {
                "type": (
                    "increase_memory_limit"
                ),
                "target": resource,
            },
            "risk": "medium",
            "reason": (
                "Evaluation-only remediation "
                "recommendation"
            ),
            "rollback": (
                "Restore the previous memory limit"
            ),
            "verification": (
                "Verify memory and restart metrics"
            ),
            "approval_required": True,
        }

    @staticmethod
    def _investigation_response(
        prompt: str,
    ) -> dict[str, Any]:
        marker = "State:\n"

        if marker not in prompt:
            raise ValueError(
                "Evaluation Investigation state is missing"
            )

        state = json.loads(
            prompt.split(
                marker,
                1,
            )[1]
        )

        scope = state.get(
            "scope",
            {},
        )

        resource = _resource_from_text(
            json.dumps(
                scope,
                sort_keys=True,
            )
        )

        evidence = state.get(
            "evidence",
            [],
        )

        evidence_ids = [
            str(
                item.get(
                    "evidence_id"
                )
            )
            for item in evidence
            if isinstance(
                item,
                dict,
            )
            and item.get(
                "evidence_id"
            )
        ]

        hypothesis = {
            "hypothesis_id": (
                "evaluation-hypothesis"
            ),
            "cause": (
                "Evaluate the resource using "
                "bounded read-only evidence"
            ),
            "confidence": (
                min(
                    0.40
                    + (
                        0.15
                        * len(
                            evidence_ids
                        )
                    ),
                    0.90,
                )
            ),
            "supporting_evidence_ids": (
                evidence_ids
            ),
            "conflicting_evidence_ids": [],
            "missing_evidence": [],
        }

        evidence_count = len(
            evidence
        )

        if evidence_count == 0:
            return {
                "hypotheses": [
                    hypothesis
                ],
                "rationale_summary": (
                    "Collect Kubernetes Pod state"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": (
                    "kubernetes_pod_state"
                ),
                "conclusion": None,
            }

        if evidence_count == 1:

            if (
                resource
                == "restart-storm-api"
            ):
                next_probe = (
                    "prometheus_restart_count"
                )
            else:
                next_probe = (
                    "prometheus_memory_working_set"
                )

            return {
                "hypotheses": [
                    hypothesis
                ],
                "rationale_summary": (
                    "Collect the next bounded metric"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": next_probe,
                "conclusion": None,
            }

        if (
            resource
            == "restart-storm-api"
            and evidence_count >= 2
        ):
            return _conclusion_decision(
                resource=resource,
                evidence_ids=evidence_ids,
                hypothesis=hypothesis,
            )

        if evidence_count == 2:
            return {
                "hypotheses": [
                    hypothesis
                ],
                "rationale_summary": (
                    "Compare memory usage with "
                    "the configured memory limit"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": (
                    "prometheus_memory_limit"
                ),
                "conclusion": None,
            }

        if (
            resource
            == "deployment-regression-api"
        ):
            return {
                "hypotheses": [
                    {
                        **hypothesis,
                        "cause": (
                            "A deployment regression is "
                            "possible but the current probe "
                            "set cannot verify revision history"
                        ),
                        "confidence": 0.60,
                        "missing_evidence": [
                            (
                                "deployment revision "
                                "history"
                            )
                        ],
                    }
                ],
                "rationale_summary": (
                    "Available Pod and memory evidence "
                    "cannot prove a deployment regression"
                ),
                "stop": True,
                "stop_reason": (
                    "insufficient_evidence"
                ),
                "next_probe": None,
                "conclusion": None,
            }

        return _conclusion_decision(
            resource=resource,
            evidence_ids=evidence_ids,
            hypothesis=hypothesis,
        )


def _conclusion_decision(
    *,
    resource: str,
    evidence_ids: list[str],
    hypothesis: dict[str, Any],
) -> dict[str, Any]:
    fixture = (
        _INVESTIGATION_CONCLUSIONS[
            resource
        ]
    )

    return {
        "hypotheses": [
            {
                **hypothesis,
                "cause": fixture[
                    "root_cause"
                ],
                "confidence": fixture[
                    "confidence"
                ],
                "supporting_evidence_ids": (
                    evidence_ids
                ),
                "missing_evidence": [],
            }
        ],
        "rationale_summary": (
            "Trusted bounded evidence is sufficient"
        ),
        "stop": True,
        "stop_reason": (
            "sufficient_evidence"
        ),
        "next_probe": None,
        "conclusion": {
            "root_cause": fixture[
                "root_cause"
            ],
            "confidence": fixture[
                "confidence"
            ],
            "evidence_ids": (
                evidence_ids
            ),
            "remaining_uncertainties": [],
        },
    }


class _FixtureHTTPClient:
    """
    Minimal async HTTP client used only by evaluation fixtures.

    No socket or DNS operation exists in this client.
    """

    def __init__(
        self,
        handler: Callable[
            [httpx.Request],
            httpx.Response,
        ],
    ) -> None:
        self.handler = handler

    async def get(
        self,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        request = httpx.Request(
            "GET",
            url,
            headers=kwargs.get(
                "headers"
            ),
            params=kwargs.get(
                "params"
            ),
        )

        return self.handler(
            request
        )


def create_investigation_evaluation_gateway(
) -> tuple[
    LLMGateway,
    InvestigationEvaluationProvider,
]:
    provider = (
        InvestigationEvaluationProvider()
    )

    client = LLMClient(
        provider
    )

    gateway = LLMGateway(
        clients={
            "openai": client,
        },
        rate_limiter=RateLimiter(
            enabled=False,
        ),
    )

    return (
        gateway,
        provider,
    )


def create_investigation_evaluation_tool_manager(
) -> ToolManager:

    registry = ToolRegistry()

    registry.register(
        EchoTool()
    )

    registry.register(
        KubernetesTool(
            api_url=(
                "https://evaluation."
                "kubernetes.invalid"
            ),
            cluster_name=(
                "production-a"
            ),
            allow_dry_run_fallback=False,
            client=_FixtureHTTPClient(
                _kubernetes_handler
            ),
        )
    )

    registry.register(
        PrometheusTool(
            base_url=(
                "https://evaluation."
                "prometheus.invalid"
            ),
            allow_mock_fallback=False,
            client=_FixtureHTTPClient(
                _prometheus_handler
            ),
        )
    )

    return ToolManager(
        registry
    )


def create_investigation_evaluation_runtime(
) -> AgentRuntime:
    """
    Create one explicitly enabled, offline-only evaluation Runtime.

    This Runtime:

    - uses the real AgentRuntime;
    - uses one shared LLMGateway;
    - uses LLMInvestigationReasoner;
    - enables Investigation with the exact acknowledgement;
    - swaps Runtime ToolManager to local read-only HTTP fixtures;
    - performs no Matrix ActionRuntime or Verification execution.
    """

    (
        gateway,
        provider,
    ) = (
        create_investigation_evaluation_gateway()
    )

    adapter = (
        InvestigationLLMGatewayAdapter(
            gateway
        )
    )

    reasoner = (
        LLMInvestigationReasoner(
            adapter
        )
    )

    settings = InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
        limits=InvestigationLimits(
            max_iterations=5,
            max_tool_calls=4,
            timeout_seconds=10,
        ),
    )

    runtime = AgentRuntime(
        llm_gateway=gateway,
        investigation_reasoner=reasoner,
        investigation_settings=settings,
    )

    runtime.tools = (
        create_investigation_evaluation_tool_manager()
    )

    runtime.investigation_evaluation_provider = (
        provider
    )

    return runtime


def _kubernetes_handler(
    request: httpx.Request,
) -> httpx.Response:
    target = unquote(
        request.url.path
        .rstrip("/")
        .split("/")[-1]
    )

    fixture = (
        _KUBERNETES_FIXTURES.get(
            target
        )
    )

    if fixture is None:
        return httpx.Response(
            404,
            json={
                "kind": "Status",
                "status": "Failure",
                "reason": "NotFound",
            },
            request=request,
        )

    ready = fixture[
        "ready"
    ]

    oom_killed = fixture[
        "oom_killed"
    ]

    state_reason = fixture[
        "state_reason"
    ]

    state = (
        {
            "waiting": {
                "reason": state_reason,
            }
        }
        if state_reason
        else {
            "running": {}
        }
    )

    last_state = (
        {
            "terminated": {
                "reason": "OOMKilled",
                "finishedAt": (
                    "2026-08-10T05:00:00Z"
                ),
            }
        }
        if oom_killed
        else {}
    )

    return httpx.Response(
        200,
        json={
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": target,
                "namespace": "payment",
                "uid": (
                    f"fixture-{target}"
                ),
                "resourceVersion": "1",
                "labels": {
                    "evaluation": "true",
                },
            },
            "spec": {
                "nodeName": (
                    "evaluation-worker"
                ),
            },
            "status": {
                "phase": "Running",
                "conditions": [
                    {
                        "type": (
                            "PodScheduled"
                        ),
                        "status": "True",
                    },
                    {
                        "type": "Ready",
                        "status": (
                            "True"
                            if ready
                            else "False"
                        ),
                    },
                ],
                "containerStatuses": [
                    {
                        "name": target,
                        "ready": ready,
                        "restartCount": fixture[
                            "restart_count"
                        ],
                        "state": state,
                        "lastState": last_state,
                        "image": (
                            "evaluation:v1"
                        ),
                        "imageID": (
                            "evaluation-image"
                        ),
                    }
                ],
            },
        },
        request=request,
    )


def _prometheus_handler(
    request: httpx.Request,
) -> httpx.Response:

    query = request.url.params.get(
        "query",
        "",
    )

    resource = _resource_from_text(
        query
    )

    fixture = (
        _PROMETHEUS_FIXTURES[
            resource
        ]
    )

    if (
        "container_memory_working_set_bytes"
        in query
    ):
        value = fixture[
            "working_set"
        ]

    elif (
        "kube_pod_container_resource_limits"
        in query
    ):
        value = fixture[
            "memory_limit"
        ]

    elif (
        "kube_pod_container_status_restarts_total"
        in query
    ):
        value = fixture[
            "restart_count"
        ]

    else:
        # Diagnosis may issue other read-only metrics.
        # Keep them deterministic and finite.
        value = 1.0

    raw_time = request.url.params.get(
        "time"
    )

    observed_at = datetime.now(
        UTC
    )

    if raw_time:
        try:
            observed_at = (
                datetime.fromisoformat(
                    raw_time.replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

            if (
                observed_at.tzinfo
                is None
            ):
                observed_at = (
                    observed_at.replace(
                        tzinfo=UTC
                    )
                )

            observed_at = (
                observed_at.astimezone(
                    UTC
                )
            )

        except ValueError:
            observed_at = datetime.now(
                UTC
            )

    return httpx.Response(
        200,
        json={
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {
                            "pod": resource,
                            "namespace": (
                                "payment"
                            ),
                        },
                        "value": [
                            observed_at.timestamp(),
                            str(
                                value
                            ),
                        ],
                    }
                ],
            },
        },
        request=request,
    )


def _resource_from_text(
    value: str,
) -> str:
    normalized = (
        value.lower()
    )

    for resource in _RESOURCE_NAMES:
        if resource in normalized:
            return resource

    raise ValueError(
        "Unknown Investigation evaluation resource"
    )


__all__ = [
    "InvestigationEvaluationProvider",
    "create_investigation_evaluation_gateway",
    "create_investigation_evaluation_runtime",
    "create_investigation_evaluation_tool_manager",
]
