from pathlib import Path

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

from common.config.settings import (
    AuthenticationConfig,
)

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightPolicy,
    KubernetesPreflightResolver,
)
from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.action.preflight_artifact_store import (
    PreflightArtifactStore,
)
from services.agent_runtime.app.action.production_action_preparation import (
    ProductionActionPreparationService,
)
from services.agent_runtime.app.action.production_action_query import (
    ProductionActionQueryService,
)
from services.agent_runtime.app.action.production_action_guard import (
    ProductionActionExpiryGuard,
)
from services.agent_runtime.app.action.safety_models import (
    KubernetesWorkloadScope,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)


def disabled_authentication_service():
    return create_authentication_service(AuthenticationConfig())


def resolver() -> KubernetesPreflightResolver:
    scope = KubernetesWorkloadScope(
        cluster="production-a",
        namespace="payment",
        name="payment-api",
        container="payment-api",
    )
    return KubernetesPreflightResolver(
        api_url="https://kubernetes.test",
        cluster_name="production-a",
        policy=KubernetesPreflightPolicy(
            enabled=True,
            allowed_targets=(scope,),
        ),
        bearer_token="test-service-account-token-000001",
    )


def test_disabled_preflight_creates_no_artifact_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        lambda: None,
    )

    runtime = runtime_module.AgentRuntime(
        authentication_service=disabled_authentication_service()
    )

    assert runtime.kubernetes_preflight is None
    assert runtime.preflight_artifact_store is None
    assert runtime.preflight_artifact_service is None
    assert runtime.production_action_guard is None
    assert runtime.production_action_preparation is None
    assert runtime.production_action_query is None
    assert (
        runtime.approval.manager.transition_guard
        is None
    )
    assert (
        runtime.action_runtime.production_action_guard
        is None
    )
    assert not (tmp_path / "data" / "preflight_artifacts.db").exists()


def test_enabled_preflight_builds_one_shared_preparation_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    expected = resolver()
    runtime = runtime_module.AgentRuntime(
        authentication_service=disabled_authentication_service(),
        kubernetes_preflight=expected,
    )

    assert runtime.kubernetes_preflight is expected
    assert isinstance(runtime.preflight_artifact_store, PreflightArtifactStore)
    assert isinstance(runtime.preflight_artifact_service, PreflightArtifactService)
    assert isinstance(
        runtime.production_action_preparation,
        ProductionActionPreparationService,
    )
    assert isinstance(
        runtime.production_action_query,
        ProductionActionQueryService,
    )
    assert isinstance(
        runtime.production_action_guard,
        ProductionActionExpiryGuard,
    )
    assert (
        runtime.preflight_artifact_service.store
        is runtime.preflight_artifact_store
    )
    assert (
        runtime.production_action_preparation.resolver
        is runtime.kubernetes_preflight
    )
    assert (
        runtime.production_action_preparation.artifact_service
        is runtime.preflight_artifact_service
    )
    assert (
        runtime.production_action_preparation.approval_service
        is runtime.approval
    )
    assert (
        runtime.production_action_query.artifact_service
        is runtime.preflight_artifact_service
    )
    assert (
        runtime.production_action_query.approval_service
        is runtime.approval
    )
    assert (
        runtime.production_action_query.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.production_action_guard.artifact_service
        is runtime.preflight_artifact_service
    )
    assert (
        runtime.approval.manager.transition_guard
        is runtime.production_action_guard
    )
    assert (
        runtime.action_runtime.production_action_guard
        is runtime.production_action_guard
    )
    assert (tmp_path / "data" / "preflight_artifacts.db").is_file()


def test_preparation_wiring_does_not_enable_action_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    runtime = runtime_module.AgentRuntime(
        authentication_service=disabled_authentication_service(),
        kubernetes_preflight=resolver(),
    )

    assert not hasattr(runtime.action_runtime, "kubernetes_preflight")
    assert not hasattr(runtime.action_runtime, "preflight_artifact_service")
    assert runtime.action_runtime.executor.__class__.__name__ == "MockExecutor"
    assert (
        runtime.action_runtime.approval
        is runtime.approval
    )
    assert (
        runtime.verification_coordinator.verification_runtime
        is runtime.verification_runtime
    )


def test_preparation_constructor_rejects_unshared_invalid_components(
    tmp_path: Path,
):
    valid_resolver = resolver()
    store = PreflightArtifactStore(tmp_path / "preflight_artifacts.db")
    artifact_service = PreflightArtifactService(store)

    with pytest.raises(TypeError, match="Artifact service is invalid"):
        ProductionActionPreparationService(
            resolver=valid_resolver,
            artifact_service=object(),
            approval_service=object(),
        )

    with pytest.raises(TypeError, match="Approval service is invalid"):
        ProductionActionPreparationService(
            resolver=valid_resolver,
            artifact_service=artifact_service,
            approval_service=object(),
        )
