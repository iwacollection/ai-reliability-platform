from types import SimpleNamespace
from typing import Any

import pytest

from services.agent_runtime.app.skills.kubernetes import (
    KubernetesDiagnosisSkill,
)


class FakeToolManager:
    def __init__(
        self,
        fail_on: str | None = None,
    ) -> None:
        self.fail_on = fail_on
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        name: str,
        context=None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "name": name,
                "context": context,
                "kwargs": kwargs,
            }
        )

        if name == self.fail_on:
            raise RuntimeError(
                f"{name} failed"
            )

        return {
            "tool": name,
            "input": kwargs,
        }


class FakeMCPServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        operation: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "operation": operation,
                "kwargs": kwargs,
            }
        )

        return {
            "operation": operation,
            "resource": kwargs.get(
                "resource"
            ),
        }


class FakeMCPRegistry:
    def __init__(
        self,
        server: FakeMCPServer,
    ) -> None:
        self.server = server
        self.requested_names: list[str] = []

    def get(
        self,
        name: str,
    ) -> FakeMCPServer:
        self.requested_names.append(
            name
        )
        return self.server


def create_context(
    *,
    tools=None,
    mcp=None,
):
    return SimpleNamespace(
        tools=tools,
        mcp=mcp,
    )


@pytest.mark.asyncio
async def test_scope_is_passed_to_tools():
    tools = FakeToolManager()
    context = create_context(
        tools=tools
    )
    skill = KubernetesDiagnosisSkill()

    result = await skill.execute(
        context,
        {
            "resource": "payment-api",
            "namespace": "payment",
            "cluster": "production-a",
        },
    )

    assert result["resource"] == "payment-api"
    assert result["namespace"] == "payment"
    assert result["cluster"] == "production-a"

    assert [
        call["name"]
        for call in tools.calls
    ] == [
        "kubernetes",
        "prometheus",
    ]

    kubernetes_call = tools.calls[0]

    assert kubernetes_call["context"] is context
    assert kubernetes_call["kwargs"] == {
        "action": "describe",
        "resource": "pod",
        "target": "payment-api",
        "namespace": "payment",
    }

    prometheus_call = tools.calls[1]
    query = prometheus_call[
        "kwargs"
    ]["query"]

    assert prometheus_call["context"] is context
    assert (
        "container_cpu_usage_seconds_total"
        in query
    )
    assert 'pod="payment-api"' in query
    assert 'namespace="payment"' in query
    assert 'cluster="production-a"' in query
    assert 'container!="POD"' in query
    assert 'container!=""' in query
    assert 'image!=""' in query
    assert query.startswith(
        "sum(rate("
    )
    assert query.endswith(
        "[5m]))"
    )


@pytest.mark.asyncio
async def test_default_namespace_and_optional_cluster():
    tools = FakeToolManager()
    context = create_context(
        tools=tools
    )
    skill = KubernetesDiagnosisSkill()

    result = await skill.execute(
        context,
        {
            "resource": "payment-api"
        },
    )

    assert result["namespace"] == "default"
    assert result["cluster"] is None
    assert tools.calls[0]["kwargs"][
        "namespace"
    ] == "default"

    query = tools.calls[1]["kwargs"][
        "query"
    ]

    assert 'namespace="default"' in query
    assert "cluster=" not in query


@pytest.mark.asyncio
async def test_blank_scope_values_are_normalized():
    tools = FakeToolManager()
    context = create_context(
        tools=tools
    )
    skill = KubernetesDiagnosisSkill()

    result = await skill.execute(
        context,
        {
            "resource": "   ",
            "namespace": "   ",
            "cluster": "   ",
        },
    )

    assert result["resource"] == "unknown"
    assert result["namespace"] == "default"
    assert result["cluster"] == ""
    assert tools.calls[0]["kwargs"][
        "target"
    ] == "unknown"

    query = tools.calls[1]["kwargs"][
        "query"
    ]

    assert 'pod="unknown"' in query
    assert 'namespace="default"' in query
    assert "cluster=" not in query


def test_prometheus_label_values_are_escaped():
    skill = KubernetesDiagnosisSkill()

    escaped = skill._escape_label_value(
        'a"b\\c\nd\re'
    )

    assert escaped == (
        'a\\"b\\\\c\\nd\\re'
    )
    assert "\n" not in escaped
    assert "\r" not in escaped


def test_query_uses_escaped_scope_values():
    skill = KubernetesDiagnosisSkill()

    query = skill._build_cpu_query(
        resource='payment"api',
        namespace="team\\blue",
        cluster="prod\nwest",
    )

    assert 'pod="payment\\"api"' in query
    assert 'namespace="team\\\\blue"' in query
    assert 'cluster="prod\\nwest"' in query
    assert "\n" not in query


@pytest.mark.asyncio
async def test_mcp_parameter_contract_remains_compatible():
    server = FakeMCPServer()
    registry = FakeMCPRegistry(
        server
    )
    context = create_context(
        mcp=registry
    )
    skill = KubernetesDiagnosisSkill()

    result = await skill.execute(
        context,
        {
            "resource": "payment-api",
            "namespace": "payment",
            "cluster": "production-a",
        },
    )

    assert registry.requested_names == [
        "mock_mcp"
    ]
    assert len(server.calls) == 1

    call = server.calls[0]

    assert call["operation"] == (
        "kubernetes_diagnosis"
    )
    assert call["kwargs"]["context"] is context
    assert call["kwargs"]["resource"] == (
        "payment-api"
    )
    assert "namespace" not in call["kwargs"]
    assert "cluster" not in call["kwargs"]
    assert result["mcp"]["resource"] == (
        "payment-api"
    )


@pytest.mark.asyncio
async def test_scope_is_returned_without_integrations():
    context = create_context()
    skill = KubernetesDiagnosisSkill()

    result = await skill.execute(
        context,
        {
            "resource": "payment-api",
            "namespace": "payment",
            "cluster": "production-a",
        },
    )

    assert result == {
        "resource": "payment-api",
        "namespace": "payment",
        "cluster": "production-a",
    }


@pytest.mark.asyncio
async def test_tool_failure_stops_following_calls():
    tools = FakeToolManager(
        fail_on="kubernetes"
    )
    context = create_context(
        tools=tools
    )
    skill = KubernetesDiagnosisSkill()

    with pytest.raises(
        RuntimeError,
        match="kubernetes failed",
    ):
        await skill.execute(
            context,
            {
                "resource": "payment-api",
                "namespace": "payment",
            },
        )

    assert [
        call["name"]
        for call in tools.calls
    ] == [
        "kubernetes"
    ]