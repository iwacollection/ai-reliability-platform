from __future__ import annotations

import ast
import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "change-investigation-capability-002-v2.1"

AFTER_NAME = (
    "change_investigation_capability_002_v2_1_after.txt"
)

ERROR_NAME = (
    "change_investigation_capability_002_v2_1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/tools/kubernetes/change_tool.py': '8fa7d8f0083538f5c05bd9a1df95a4161402202947c2ed248445a21c3bc0342c', 'services/agent_runtime/app/investigation/probes.py': 'd6172f987301eee6f3cf8562291696e524b4b0fddfc85ced8343b1222af53cff', 'services/agent_runtime/app/investigation/reasoner.py': 'df78cccd063764ee8676f24c80a1f3ddfae825c7cb16faba87f32a2093604d8c'}

CHANGE_TOOL_SOURCE = 'from __future__ import annotations\n\nfrom collections.abc import Mapping\nfrom datetime import UTC, datetime\nfrom typing import Any\nfrom urllib.parse import quote, urlencode\n\nimport httpx\n\nfrom services.agent_runtime.app.tools.base import (\n    BaseTool,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesAuthorizationError,\n    KubernetesConfigurationError,\n    KubernetesQueryError,\n    KubernetesResourceNotFoundError,\n    KubernetesTool,\n)\n\n\nclass KubernetesChangeToolError(\n    RuntimeError\n):\n    """\n    Base error for the bounded read-only workload-change evidence tool.\n    """\n\n\nclass KubernetesChangeTopologyError(\n    KubernetesChangeToolError\n):\n    """\n    The Pod owner chain cannot be proven as Pod -> ReplicaSet -> Deployment.\n    """\n\n\nclass KubernetesChangeResponseError(\n    KubernetesChangeToolError\n):\n    """\n    Kubernetes workload/change payload is structurally invalid.\n    """\n\n\nclass KubernetesChangeTool(\n    BaseTool\n):\n    """\n    Bounded read-only Deployment change evidence for one Pod.\n\n    The public contract accepts only a Pod scope and optional incident time.\n    It performs only GET requests to fixed Kubernetes Core/apps API paths.\n    The caller cannot supply a Kubernetes verb, resource kind, API path,\n    label selector or URL.\n    """\n\n    _MAX_HISTORY_ITEMS = 50\n    _DEPLOYMENT_REVISION_ANNOTATION = (\n        "deployment.kubernetes.io/revision"\n    )\n\n    def __init__(\n        self,\n        kubernetes: KubernetesTool | None = None,\n    ) -> None:\n        self.kubernetes = (\n            kubernetes\n            if kubernetes is not None\n            else KubernetesTool()\n        )\n\n        if not isinstance(\n            self.kubernetes,\n            KubernetesTool,\n        ):\n            raise TypeError(\n                "KubernetesChangeTool requires KubernetesTool"\n            )\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "kubernetes_change"\n\n    @property\n    def is_available(\n        self,\n    ) -> bool:\n        return (\n            self.kubernetes.api_url\n            is not None\n        )\n\n    async def execute(\n        self,\n        target: str,\n        namespace: str = "default",\n        cluster: str | None = None,\n        incident_time: str | None = None,\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        pod_name = self._required_text(\n            target,\n            "target",\n        )\n\n        namespace_value = self._required_text(\n            namespace,\n            "namespace",\n        )\n\n        if cluster is not None:\n            cluster_value = self._required_text(\n                cluster,\n                "cluster",\n            )\n\n            configured_cluster = (\n                self.kubernetes.cluster_name\n            )\n\n            if (\n                configured_cluster is not None\n                and cluster_value\n                != configured_cluster\n            ):\n                raise KubernetesChangeTopologyError(\n                    "Requested cluster does not match configured Kubernetes cluster"\n                )\n\n        if self.kubernetes.api_url is None:\n            raise KubernetesConfigurationError(\n                "KUBERNETES_API_URL is not configured"\n            )\n\n        incident_at = self._optional_time(\n            incident_time\n        )\n\n        if self.kubernetes.client is not None:\n            return await self._collect(\n                client=self.kubernetes.client,\n                pod_name=pod_name,\n                namespace=namespace_value,\n                incident_at=incident_at,\n            )\n\n        async with httpx.AsyncClient(\n            timeout=self.kubernetes.timeout_seconds,\n            verify=self.kubernetes._httpx_verify,\n        ) as client:\n            return await self._collect(\n                client=client,\n                pod_name=pod_name,\n                namespace=namespace_value,\n                incident_at=incident_at,\n            )\n\n    async def _collect(\n        self,\n        *,\n        client: httpx.AsyncClient,\n        pod_name: str,\n        namespace: str,\n        incident_at: datetime | None,\n    ) -> dict[str, Any]:\n        pod = await self._get_json(\n            client=client,\n            url=self._pod_url(\n                namespace=namespace,\n                name=pod_name,\n            ),\n        )\n\n        replica_set_owner = self._controller_owner(\n            pod,\n            expected_kind="ReplicaSet",\n        )\n\n        replica_set = await self._get_json(\n            client=client,\n            url=self._replica_set_url(\n                namespace=namespace,\n                name=replica_set_owner[\n                    "name"\n                ],\n            ),\n        )\n\n        self._require_metadata_identity(\n            replica_set,\n            expected_name=(\n                replica_set_owner[\n                    "name"\n                ]\n            ),\n            expected_uid=(\n                replica_set_owner[\n                    "uid"\n                ]\n            ),\n            label="ReplicaSet",\n        )\n\n        deployment_owner = self._controller_owner(\n            replica_set,\n            expected_kind="Deployment",\n        )\n\n        deployment = await self._get_json(\n            client=client,\n            url=self._deployment_url(\n                namespace=namespace,\n                name=deployment_owner[\n                    "name"\n                ],\n            ),\n        )\n\n        self._require_metadata_identity(\n            deployment,\n            expected_name=(\n                deployment_owner[\n                    "name"\n                ]\n            ),\n            expected_uid=(\n                deployment_owner[\n                    "uid"\n                ]\n            ),\n            label="Deployment",\n        )\n\n        selector = self._deployment_selector(\n            deployment\n        )\n\n        history_payload = await self._get_json(\n            client=client,\n            url=self._replica_set_list_url(\n                namespace=namespace,\n                selector=selector,\n            ),\n        )\n\n        history_items = history_payload.get(\n            "items"\n        )\n\n        if not isinstance(\n            history_items,\n            list,\n        ):\n            raise KubernetesChangeResponseError(\n                "ReplicaSet history items are invalid"\n            )\n\n        history_metadata = (\n            history_payload.get(\n                "metadata"\n            )\n        )\n\n        history_complete = True\n\n        if isinstance(\n            history_metadata,\n            Mapping,\n        ):\n            history_complete = not bool(\n                history_metadata.get(\n                    "continue"\n                )\n            )\n\n        current_revision = self._revision(\n            replica_set\n        )\n\n        deployment_revision = self._revision(\n            deployment\n        )\n\n        if (\n            deployment_revision is not None\n            and current_revision is not None\n            and deployment_revision\n            != current_revision\n        ):\n            raise KubernetesChangeTopologyError(\n                "Deployment and current ReplicaSet revisions disagree"\n            )\n\n        revision_after = (\n            current_revision\n            if current_revision is not None\n            else deployment_revision\n        )\n\n        deployment_metadata = self._metadata(\n            deployment,\n            "Deployment",\n        )\n\n        deployment_uid = deployment_metadata.get(\n            "uid"\n        )\n\n        history: list[\n            tuple[\n                int,\n                Mapping[str, Any],\n            ]\n        ] = []\n\n        for item in history_items[\n            :self._MAX_HISTORY_ITEMS\n        ]:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            if not self._owned_by(\n                item,\n                kind="Deployment",\n                name=deployment_owner[\n                    "name"\n                ],\n                uid=deployment_uid,\n            ):\n                continue\n\n            revision = self._revision(\n                item\n            )\n\n            if revision is None:\n                continue\n\n            history.append(\n                (\n                    revision,\n                    item,\n                )\n            )\n\n        previous_item = None\n        previous_revision = None\n\n        if revision_after is not None:\n            candidates = [\n                (\n                    revision,\n                    item,\n                )\n                for revision, item\n                in history\n                if revision < revision_after\n            ]\n\n            if candidates:\n                (\n                    previous_revision,\n                    previous_item,\n                ) = max(\n                    candidates,\n                    key=lambda pair: pair[\n                        0\n                    ],\n                )\n\n        image_after = self._template_images(\n            replica_set\n        )\n\n        if image_after is None:\n            image_after = self._template_images(\n                deployment\n            )\n\n        image_before = (\n            self._template_images(\n                previous_item\n            )\n            if previous_item is not None\n            else None\n        )\n\n        image_changed = (\n            (\n                image_before\n                != image_after\n            )\n            if (\n                image_before is not None\n                and image_after is not None\n            )\n            else None\n        )\n\n        rollout_started_at = self._creation_timestamp(\n            replica_set\n        )\n\n        rollout_offset_seconds = None\n\n        if (\n            incident_at is not None\n            and rollout_started_at\n            is not None\n        ):\n            rollout_offset_seconds = (\n                incident_at\n                - rollout_started_at\n            ).total_seconds()\n\n        spec = deployment.get(\n            "spec"\n        )\n\n        status = deployment.get(\n            "status"\n        )\n\n        if not isinstance(\n            spec,\n            Mapping,\n        ):\n            spec = {}\n\n        if not isinstance(\n            status,\n            Mapping,\n        ):\n            status = {}\n\n        rollout_facts = self._rollout_facts(\n            deployment=deployment,\n        )\n\n        event_facts = await self._collect_event_facts(\n            client=client,\n            namespace=namespace,\n            incident_at=incident_at,\n            objects=[\n                (\n                    "Pod",\n                    self._metadata(\n                        pod,\n                        "Pod",\n                    ),\n                ),\n                (\n                    "ReplicaSet",\n                    self._metadata(\n                        replica_set,\n                        "ReplicaSet",\n                    ),\n                ),\n                (\n                    "Deployment",\n                    deployment_metadata,\n                ),\n            ],\n        )\n\n        observed_at = self._now()\n\n        return {\n            "success": True,\n            "source": "kubernetes_change",\n            "mode": "read_only",\n            "production_signal": True,\n            "observed_at": (\n                observed_at.isoformat()\n            ),\n            "cluster": (\n                self.kubernetes.cluster_name\n            ),\n            "data": {\n                "owner_chain_verified": True,\n                "workload_kind": "Deployment",\n                "deployment_name": (\n                    deployment_owner[\n                        "name"\n                    ]\n                ),\n                "revision_before": (\n                    previous_revision\n                ),\n                "revision_after": (\n                    revision_after\n                ),\n                "revision_changed": (\n                    (\n                        previous_revision\n                        != revision_after\n                    )\n                    if (\n                        previous_revision is not None\n                        and revision_after is not None\n                    )\n                    else None\n                ),\n                "image_before": (\n                    image_before\n                ),\n                "image_after": (\n                    image_after\n                ),\n                "image_changed": (\n                    image_changed\n                ),\n                "rollout_started_at": (\n                    rollout_started_at.isoformat()\n                    if rollout_started_at\n                    is not None\n                    else None\n                ),\n                "rollout_offset_seconds": (\n                    rollout_offset_seconds\n                ),\n                "generation": (\n                    self._non_negative_int(\n                        deployment_metadata.get(\n                            "generation"\n                        )\n                    )\n                ),\n                "observed_generation": (\n                    self._non_negative_int(\n                        status.get(\n                            "observedGeneration"\n                        )\n                    )\n                ),\n                "replicas_desired": (\n                    self._non_negative_int(\n                        spec.get(\n                            "replicas"\n                        )\n                    )\n                ),\n                "replicas_updated": (\n                    self._non_negative_int(\n                        status.get(\n                            "updatedReplicas"\n                        )\n                    )\n                ),\n                "replicas_ready": (\n                    self._non_negative_int(\n                        status.get(\n                            "readyReplicas"\n                        )\n                    )\n                ),\n                "replicas_available": (\n                    self._non_negative_int(\n                        status.get(\n                            "availableReplicas"\n                        )\n                    )\n                ),\n                "replicas_unavailable": (\n                    self._non_negative_int(\n                        status.get(\n                            "unavailableReplicas"\n                        )\n                    )\n                ),\n                "history_complete": (\n                    history_complete\n                ),\n                **rollout_facts,\n                **event_facts,\n            },\n        }\n\n    @classmethod\n    def _rollout_facts(\n        cls,\n        *,\n        deployment: Mapping[str, Any],\n    ) -> dict[str, Any]:\n        metadata = cls._metadata(\n            deployment,\n            "Deployment",\n        )\n\n        spec = deployment.get(\n            "spec"\n        )\n\n        status = deployment.get(\n            "status"\n        )\n\n        if not isinstance(\n            spec,\n            Mapping,\n        ):\n            spec = {}\n\n        if not isinstance(\n            status,\n            Mapping,\n        ):\n            status = {}\n\n        conditions = status.get(\n            "conditions"\n        )\n\n        if not isinstance(\n            conditions,\n            list,\n        ):\n            conditions = []\n\n        condition_map: dict[\n            str,\n            Mapping[str, Any],\n        ] = {}\n\n        for condition in conditions[\n            :32\n        ]:\n            if not isinstance(\n                condition,\n                Mapping,\n            ):\n                continue\n\n            condition_type = condition.get(\n                "type"\n            )\n\n            if not isinstance(\n                condition_type,\n                str,\n            ):\n                continue\n\n            if condition_type in {\n                "Progressing",\n                "Available",\n                "ReplicaFailure",\n            }:\n                condition_map[\n                    condition_type\n                ] = condition\n\n        progressing = condition_map.get(\n            "Progressing",\n            {},\n        )\n\n        available = condition_map.get(\n            "Available",\n            {},\n        )\n\n        replica_failure = condition_map.get(\n            "ReplicaFailure",\n            {},\n        )\n\n        progressing_status = cls._condition_status(\n            progressing.get(\n                "status"\n            )\n        )\n\n        available_status = cls._condition_status(\n            available.get(\n                "status"\n            )\n        )\n\n        replica_failure_status = cls._condition_status(\n            replica_failure.get(\n                "status"\n            )\n        )\n\n        progressing_reason = cls._optional_bounded_text(\n            progressing.get(\n                "reason"\n            ),\n            limit=128,\n        )\n\n        available_reason = cls._optional_bounded_text(\n            available.get(\n                "reason"\n            ),\n            limit=128,\n        )\n\n        replica_failure_reason = cls._optional_bounded_text(\n            replica_failure.get(\n                "reason"\n            ),\n            limit=128,\n        )\n\n        failure_reasons = []\n\n        if (\n            progressing_status\n            == "False"\n            and progressing_reason\n            == "ProgressDeadlineExceeded"\n        ):\n            failure_reasons.append(\n                progressing_reason\n            )\n\n        if (\n            replica_failure_status\n            == "True"\n        ):\n            failure_reasons.append(\n                replica_failure_reason\n                or "ReplicaFailure"\n            )\n\n        desired = cls._non_negative_int(\n            spec.get(\n                "replicas"\n            )\n        )\n\n        updated = cls._non_negative_int(\n            status.get(\n                "updatedReplicas"\n            )\n        )\n\n        ready = cls._non_negative_int(\n            status.get(\n                "readyReplicas"\n            )\n        )\n\n        available_replicas = (\n            cls._non_negative_int(\n                status.get(\n                    "availableReplicas"\n                )\n            )\n        )\n\n        generation = cls._non_negative_int(\n            metadata.get(\n                "generation"\n            )\n        )\n\n        observed_generation = (\n            cls._non_negative_int(\n                status.get(\n                    "observedGeneration"\n                )\n            )\n        )\n\n        generation_observed = None\n\n        if (\n            generation is not None\n            and observed_generation\n            is not None\n        ):\n            generation_observed = (\n                observed_generation\n                >= generation\n            )\n\n        rollout_complete = None\n\n        if desired is not None:\n            rollout_complete = (\n                generation_observed\n                is True\n                and updated\n                == desired\n                and ready\n                == desired\n                and available_replicas\n                == desired\n                and not failure_reasons\n            )\n\n        condition_parts = [\n            (\n                "Progressing="\n                + (\n                    progressing_status\n                    or "Unknown"\n                )\n                + ":"\n                + (\n                    progressing_reason\n                    or "-"\n                )\n            ),\n            (\n                "Available="\n                + (\n                    available_status\n                    or "Unknown"\n                )\n                + ":"\n                + (\n                    available_reason\n                    or "-"\n                )\n            ),\n            (\n                "ReplicaFailure="\n                + (\n                    replica_failure_status\n                    or "Unknown"\n                )\n                + ":"\n                + (\n                    replica_failure_reason\n                    or "-"\n                )\n            ),\n        ]\n\n        return {\n            "rollout_condition_summary": (\n                ";".join(\n                    condition_parts\n                )[\n                    :512\n                ]\n            ),\n            "generation_observed": (\n                generation_observed\n            ),\n            "rollout_complete": (\n                rollout_complete\n            ),\n            "rollout_failure_signal": (\n                bool(\n                    failure_reasons\n                )\n            ),\n            "rollout_failure_reason": (\n                ";".join(\n                    failure_reasons\n                )[\n                    :256\n                ]\n                if failure_reasons\n                else None\n            ),\n        }\n\n    async def _collect_event_facts(\n        self,\n        *,\n        client: httpx.AsyncClient,\n        namespace: str,\n        incident_at: datetime | None,\n        objects: list[\n            tuple[\n                str,\n                Mapping[str, Any],\n            ]\n        ],\n    ) -> dict[str, Any]:\n        records: list[\n            dict[str, Any]\n        ] = []\n\n        query_successes = 0\n        query_failures: list[\n            str\n        ] = []\n\n        for kind, metadata in objects:\n            uid = metadata.get(\n                "uid"\n            )\n\n            name = metadata.get(\n                "name"\n            )\n\n            if (\n                not isinstance(\n                    uid,\n                    str,\n                )\n                or not uid.strip()\n                or not isinstance(\n                    name,\n                    str,\n                )\n                or not name.strip()\n            ):\n                continue\n\n            try:\n                payload = await self._get_json(\n                    client=client,\n                    url=self._event_list_url(\n                        namespace=namespace,\n                        uid=uid.strip(),\n                    ),\n                )\n\n            except KubernetesAuthorizationError:\n                query_failures.append(\n                    "authorization_denied"\n                )\n                continue\n\n            except KubernetesResourceNotFoundError:\n                query_failures.append(\n                    "not_found"\n                )\n                continue\n\n            except KubernetesQueryError:\n                query_failures.append(\n                    "query_failed"\n                )\n                continue\n\n            query_successes += 1\n\n            items = payload.get(\n                "items"\n            )\n\n            if not isinstance(\n                items,\n                list,\n            ):\n                query_failures.append(\n                    "invalid_payload"\n                )\n                continue\n\n            for item in items[\n                :self._MAX_HISTORY_ITEMS\n            ]:\n                record = self._event_record(\n                    item=item,\n                    default_kind=kind,\n                    default_name=name.strip(),\n                )\n\n                if record is None:\n                    continue\n\n                if (\n                    incident_at is not None\n                    and record[\n                        "observed_at"\n                    ]\n                    is not None\n                ):\n                    delta_seconds = (\n                        incident_at\n                        - record[\n                            "observed_at"\n                        ]\n                    ).total_seconds()\n\n                    if not (\n                        -900.0\n                        <= delta_seconds\n                        <= 3600.0\n                    ):\n                        continue\n\n                    record[\n                        "incident_offset_seconds"\n                    ] = (\n                        delta_seconds\n                    )\n\n                records.append(\n                    record\n                )\n\n        records.sort(\n            key=lambda item: (\n                item[\n                    "observed_at"\n                ]\n                or datetime.min.replace(\n                    tzinfo=UTC\n                )\n            ),\n            reverse=True,\n        )\n\n        records = records[\n            :12\n        ]\n\n        warning_count = sum(\n            1\n            for item in records\n            if item[\n                "type"\n            ] == "Warning"\n        )\n\n        reasons = []\n\n        for item in records:\n            reason = item.get(\n                "reason"\n            )\n\n            if (\n                reason\n                and reason not in reasons\n            ):\n                reasons.append(\n                    reason\n                )\n\n        summaries = []\n\n        for item in records:\n            timestamp = (\n                item[\n                    "observed_at"\n                ].isoformat()\n                if item[\n                    "observed_at"\n                ]\n                is not None\n                else "unknown-time"\n            )\n\n            summary = (\n                f"{timestamp} "\n                f"{item[\'kind\']}/{item[\'name\']} "\n                f"{item[\'type\']} "\n                f"{item[\'reason\'] or \'UnknownReason\'}"\n            )\n\n            message = item.get(\n                "message"\n            )\n\n            if message:\n                summary += (\n                    ": "\n                    + message\n                )\n\n            summaries.append(\n                summary\n            )\n\n        if query_successes == 0:\n            events_status = (\n                "unavailable"\n            )\n\n        elif query_failures:\n            events_status = "partial"\n\n        else:\n            events_status = "complete"\n\n        return {\n            "events_status": (\n                events_status\n            ),\n            "events_error_code": (\n                query_failures[\n                    0\n                ]\n                if query_failures\n                else None\n            ),\n            "recent_event_count": (\n                len(\n                    records\n                )\n            ),\n            "recent_warning_count": (\n                warning_count\n            ),\n            "recent_event_reasons": (\n                ";".join(\n                    reasons\n                )[\n                    :512\n                ]\n                if reasons\n                else None\n            ),\n            "recent_event_summary": (\n                " | ".join(\n                    summaries\n                )[\n                    :1536\n                ]\n                if summaries\n                else None\n            ),\n        }\n\n    @classmethod\n    def _event_record(\n        cls,\n        *,\n        item: Any,\n        default_kind: str,\n        default_name: str,\n    ) -> dict[str, Any] | None:\n        if not isinstance(\n            item,\n            Mapping,\n        ):\n            return None\n\n        involved = item.get(\n            "involvedObject"\n        )\n\n        if not isinstance(\n            involved,\n            Mapping,\n        ):\n            involved = {}\n\n        kind = cls._optional_bounded_text(\n            involved.get(\n                "kind"\n            ),\n            limit=64,\n        ) or default_kind\n\n        name = cls._optional_bounded_text(\n            involved.get(\n                "name"\n            ),\n            limit=253,\n        ) or default_name\n\n        event_type = cls._optional_bounded_text(\n            item.get(\n                "type"\n            ),\n            limit=32,\n        ) or "Normal"\n\n        reason = cls._optional_bounded_text(\n            item.get(\n                "reason"\n            ),\n            limit=128,\n        )\n\n        message = cls._optional_bounded_text(\n            item.get(\n                "message"\n            ),\n            limit=512,\n        )\n\n        observed_at = cls._event_time(\n            item\n        )\n\n        return {\n            "kind": kind,\n            "name": name,\n            "type": event_type,\n            "reason": reason,\n            "message": message,\n            "observed_at": (\n                observed_at\n            ),\n        }\n\n    @classmethod\n    def _event_time(\n        cls,\n        item: Mapping[str, Any],\n    ) -> datetime | None:\n        candidates = [\n            item.get(\n                "eventTime"\n            ),\n            (\n                item.get(\n                    "series"\n                )\n                or {}\n            ).get(\n                "lastObservedTime"\n            )\n            if isinstance(\n                item.get(\n                    "series"\n                ),\n                Mapping,\n            )\n            else None,\n            item.get(\n                "lastTimestamp"\n            ),\n            item.get(\n                "firstTimestamp"\n            ),\n        ]\n\n        metadata = item.get(\n            "metadata"\n        )\n\n        if isinstance(\n            metadata,\n            Mapping,\n        ):\n            candidates.append(\n                metadata.get(\n                    "creationTimestamp"\n                )\n            )\n\n        for candidate in candidates:\n            if candidate is None:\n                continue\n\n            try:\n                parsed = cls._optional_time(\n                    candidate\n                )\n            except KubernetesChangeResponseError:\n                continue\n\n            if parsed is not None:\n                return parsed\n\n        return None\n\n    @staticmethod\n    def _condition_status(\n        value: Any,\n    ) -> str | None:\n        if value is None:\n            return None\n\n        if value not in {\n            "True",\n            "False",\n            "Unknown",\n        }:\n            raise KubernetesChangeResponseError(\n                "Deployment condition status is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _optional_bounded_text(\n        value: Any,\n        *,\n        limit: int,\n    ) -> str | None:\n        if value is None:\n            return None\n\n        if not isinstance(\n            value,\n            str,\n        ):\n            raise KubernetesChangeResponseError(\n                "Kubernetes change text value is invalid"\n            )\n\n        normalized = (\n            " ".join(\n                value.split()\n            )\n        )\n\n        if not normalized:\n            return None\n\n        return normalized[\n            :limit\n        ]\n\n    async def _get_json(\n        self,\n        *,\n        client: httpx.AsyncClient,\n        url: str,\n    ) -> dict[str, Any]:\n        try:\n            response = await client.get(\n                url,\n                headers=self.kubernetes._headers,\n            )\n        except httpx.HTTPError as exc:\n            raise KubernetesQueryError(\n                "Kubernetes change evidence query failed"\n            ) from exc\n\n        status = response.status_code\n\n        if status in {\n            401,\n            403,\n        }:\n            raise KubernetesAuthorizationError(\n                "Kubernetes authorization failed"\n            )\n\n        if status == 404:\n            raise KubernetesResourceNotFoundError(\n                "Kubernetes change resource not found"\n            )\n\n        if (\n            status < 200\n            or status >= 300\n        ):\n            raise KubernetesQueryError(\n                f"Kubernetes change query failed with HTTP {status}"\n            )\n\n        try:\n            payload = response.json()\n        except ValueError as exc:\n            raise KubernetesChangeResponseError(\n                "Kubernetes change response is invalid JSON"\n            ) from exc\n\n        if not isinstance(\n            payload,\n            dict,\n        ):\n            raise KubernetesChangeResponseError(\n                "Kubernetes change response payload is invalid"\n            )\n\n        return payload\n\n    @staticmethod\n    def _metadata(\n        payload: Mapping[str, Any],\n        label: str,\n    ) -> Mapping[str, Any]:\n        metadata = payload.get(\n            "metadata"\n        )\n\n        if not isinstance(\n            metadata,\n            Mapping,\n        ):\n            raise KubernetesChangeResponseError(\n                f"{label} metadata is invalid"\n            )\n\n        return metadata\n\n    @classmethod\n    def _controller_owner(\n        cls,\n        payload: Mapping[str, Any],\n        *,\n        expected_kind: str,\n    ) -> dict[str, str]:\n        metadata = cls._metadata(\n            payload,\n            expected_kind,\n        )\n\n        owners = metadata.get(\n            "ownerReferences"\n        )\n\n        if not isinstance(\n            owners,\n            list,\n        ):\n            raise KubernetesChangeTopologyError(\n                f"{expected_kind} controller owner is unavailable"\n            )\n\n        matches = []\n\n        for owner in owners:\n            if not isinstance(\n                owner,\n                Mapping,\n            ):\n                continue\n\n            if (\n                owner.get(\n                    "controller"\n                )\n                is not True\n            ):\n                continue\n\n            if owner.get(\n                "kind"\n            ) != expected_kind:\n                continue\n\n            name = owner.get(\n                "name"\n            )\n\n            uid = owner.get(\n                "uid"\n            )\n\n            if (\n                isinstance(\n                    name,\n                    str,\n                )\n                and name.strip()\n                and isinstance(\n                    uid,\n                    str,\n                )\n                and uid.strip()\n            ):\n                matches.append(\n                    {\n                        "name": (\n                            name.strip()\n                        ),\n                        "uid": (\n                            uid.strip()\n                        ),\n                    }\n                )\n\n        if len(\n            matches\n        ) != 1:\n            raise KubernetesChangeTopologyError(\n                f"Expected one controller {expected_kind} owner"\n            )\n\n        return matches[\n            0\n        ]\n\n    @classmethod\n    def _require_metadata_identity(\n        cls,\n        payload: Mapping[str, Any],\n        *,\n        expected_name: str,\n        expected_uid: str,\n        label: str,\n    ) -> None:\n        metadata = cls._metadata(\n            payload,\n            label,\n        )\n\n        if (\n            metadata.get(\n                "name"\n            )\n            != expected_name\n            or metadata.get(\n                "uid"\n            )\n            != expected_uid\n        ):\n            raise KubernetesChangeTopologyError(\n                f"{label} identity does not match owner reference"\n            )\n\n    @classmethod\n    def _owned_by(\n        cls,\n        payload: Mapping[str, Any],\n        *,\n        kind: str,\n        name: str,\n        uid: Any,\n    ) -> bool:\n        metadata = payload.get(\n            "metadata"\n        )\n\n        if not isinstance(\n            metadata,\n            Mapping,\n        ):\n            return False\n\n        owners = metadata.get(\n            "ownerReferences"\n        )\n\n        if not isinstance(\n            owners,\n            list,\n        ):\n            return False\n\n        for owner in owners:\n            if not isinstance(\n                owner,\n                Mapping,\n            ):\n                continue\n\n            if (\n                owner.get(\n                    "controller"\n                )\n                is True\n                and owner.get(\n                    "kind"\n                )\n                == kind\n                and owner.get(\n                    "name"\n                )\n                == name\n                and (\n                    uid is None\n                    or owner.get(\n                        "uid"\n                    )\n                    == uid\n                )\n            ):\n                return True\n\n        return False\n\n    @classmethod\n    def _deployment_selector(\n        cls,\n        deployment: Mapping[str, Any],\n    ) -> str:\n        spec = deployment.get(\n            "spec"\n        )\n\n        if not isinstance(\n            spec,\n            Mapping,\n        ):\n            raise KubernetesChangeResponseError(\n                "Deployment spec is invalid"\n            )\n\n        selector = spec.get(\n            "selector"\n        )\n\n        if not isinstance(\n            selector,\n            Mapping,\n        ):\n            raise KubernetesChangeResponseError(\n                "Deployment selector is invalid"\n            )\n\n        match_labels = selector.get(\n            "matchLabels"\n        )\n\n        if (\n            not isinstance(\n                match_labels,\n                Mapping,\n            )\n            or not match_labels\n            or len(\n                match_labels\n            )\n            > 16\n        ):\n            raise KubernetesChangeResponseError(\n                "Deployment matchLabels selector is unsupported"\n            )\n\n        pairs = []\n\n        for key in sorted(\n            match_labels\n        ):\n            value = match_labels[\n                key\n            ]\n\n            if (\n                not isinstance(\n                    key,\n                    str,\n                )\n                or not key\n                or len(\n                    key\n                )\n                > 253\n                or not isinstance(\n                    value,\n                    str,\n                )\n                or len(\n                    value\n                )\n                > 63\n                or "," in key\n                or "=" in key\n                or "," in value\n                or "=" in value\n            ):\n                raise KubernetesChangeResponseError(\n                    "Deployment label selector is invalid"\n                )\n\n            pairs.append(\n                f"{key}={value}"\n            )\n\n        return ",".join(\n            pairs\n        )\n\n    @classmethod\n    def _revision(\n        cls,\n        payload: Mapping[str, Any] | None,\n    ) -> int | None:\n        if payload is None:\n            return None\n\n        metadata = payload.get(\n            "metadata"\n        )\n\n        if not isinstance(\n            metadata,\n            Mapping,\n        ):\n            return None\n\n        annotations = metadata.get(\n            "annotations"\n        )\n\n        if not isinstance(\n            annotations,\n            Mapping,\n        ):\n            return None\n\n        raw = annotations.get(\n            cls._DEPLOYMENT_REVISION_ANNOTATION\n        )\n\n        if raw is None:\n            return None\n\n        try:\n            value = int(\n                raw\n            )\n        except (\n            TypeError,\n            ValueError,\n        ):\n            raise KubernetesChangeResponseError(\n                "Deployment revision is invalid"\n            )\n\n        if (\n            value < 0\n            or value > 1_000_000_000\n        ):\n            raise KubernetesChangeResponseError(\n                "Deployment revision is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _template_images(\n        payload: Mapping[str, Any] | None,\n    ) -> str | None:\n        if payload is None:\n            return None\n\n        spec = payload.get(\n            "spec"\n        )\n\n        if not isinstance(\n            spec,\n            Mapping,\n        ):\n            return None\n\n        template = spec.get(\n            "template"\n        )\n\n        if not isinstance(\n            template,\n            Mapping,\n        ):\n            return None\n\n        template_spec = template.get(\n            "spec"\n        )\n\n        if not isinstance(\n            template_spec,\n            Mapping,\n        ):\n            return None\n\n        containers = template_spec.get(\n            "containers"\n        )\n\n        if not isinstance(\n            containers,\n            list,\n        ):\n            return None\n\n        pairs = []\n\n        for container in containers[\n            :8\n        ]:\n            if not isinstance(\n                container,\n                Mapping,\n            ):\n                continue\n\n            name = container.get(\n                "name"\n            )\n\n            image = container.get(\n                "image"\n            )\n\n            if (\n                not isinstance(\n                    name,\n                    str,\n                )\n                or not name.strip()\n                or not isinstance(\n                    image,\n                    str,\n                )\n                or not image.strip()\n            ):\n                continue\n\n            pairs.append(\n                (\n                    name.strip()[\n                        :128\n                    ],\n                    image.strip()[\n                        :256\n                    ],\n                )\n            )\n\n        if not pairs:\n            return None\n\n        return ";".join(\n            f"{name}={image}"\n            for name, image\n            in sorted(\n                pairs\n            )\n        )[:512]\n\n    @classmethod\n    def _creation_timestamp(\n        cls,\n        payload: Mapping[str, Any],\n    ) -> datetime | None:\n        metadata = cls._metadata(\n            payload,\n            "Kubernetes resource",\n        )\n\n        return cls._optional_time(\n            metadata.get(\n                "creationTimestamp"\n            )\n        )\n\n    @staticmethod\n    def _optional_time(\n        value: Any,\n    ) -> datetime | None:\n        if value is None:\n            return None\n\n        if not isinstance(\n            value,\n            str,\n        ):\n            raise KubernetesChangeResponseError(\n                "Kubernetes change timestamp is invalid"\n            )\n\n        text = value.strip()\n\n        if not text:\n            return None\n\n        if text.endswith(\n            "Z"\n        ):\n            text = (\n                text[\n                    :-1\n                ]\n                + "+00:00"\n            )\n\n        try:\n            parsed = datetime.fromisoformat(\n                text\n            )\n        except ValueError as exc:\n            raise KubernetesChangeResponseError(\n                "Kubernetes change timestamp is invalid"\n            ) from exc\n\n        if parsed.tzinfo is None:\n            raise KubernetesChangeResponseError(\n                "Kubernetes change timestamp must be timezone-aware"\n            )\n\n        return parsed.astimezone(\n            UTC\n        )\n\n    @staticmethod\n    def _non_negative_int(\n        value: Any,\n    ) -> int | None:\n        if value is None:\n            return None\n\n        if (\n            isinstance(\n                value,\n                bool,\n            )\n            or not isinstance(\n                value,\n                int,\n            )\n            or value < 0\n            or value > 1_000_000_000\n        ):\n            raise KubernetesChangeResponseError(\n                "Kubernetes change integer is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _required_text(\n        value: Any,\n        label: str,\n    ) -> str:\n        if not isinstance(\n            value,\n            str,\n        ):\n            raise KubernetesChangeToolError(\n                f"Kubernetes change {label} is invalid"\n            )\n\n        normalized = value.strip()\n\n        if (\n            not normalized\n            or len(\n                normalized\n            )\n            > 253\n        ):\n            raise KubernetesChangeToolError(\n                f"Kubernetes change {label} is invalid"\n            )\n\n        return normalized\n\n    def _event_list_url(\n        self,\n        *,\n        namespace: str,\n        uid: str,\n    ) -> str:\n        query = urlencode(\n            {\n                "fieldSelector": (\n                    "involvedObject.uid="\n                    + uid\n                ),\n                "limit": str(\n                    self._MAX_HISTORY_ITEMS\n                ),\n            }\n        )\n\n        return (\n            f"{self.kubernetes.api_url}"\n            "/api/v1/namespaces/"\n            f"{quote(namespace, safe=\'\')}"\n            "/events"\n            f"?{query}"\n        )\n\n    def _pod_url(\n        self,\n        *,\n        namespace: str,\n        name: str,\n    ) -> str:\n        return (\n            f"{self.kubernetes.api_url}"\n            "/api/v1/namespaces/"\n            f"{quote(namespace, safe=\'\')}"\n            "/pods/"\n            f"{quote(name, safe=\'\')}"\n        )\n\n    def _replica_set_url(\n        self,\n        *,\n        namespace: str,\n        name: str,\n    ) -> str:\n        return (\n            f"{self.kubernetes.api_url}"\n            "/apis/apps/v1/namespaces/"\n            f"{quote(namespace, safe=\'\')}"\n            "/replicasets/"\n            f"{quote(name, safe=\'\')}"\n        )\n\n    def _deployment_url(\n        self,\n        *,\n        namespace: str,\n        name: str,\n    ) -> str:\n        return (\n            f"{self.kubernetes.api_url}"\n            "/apis/apps/v1/namespaces/"\n            f"{quote(namespace, safe=\'\')}"\n            "/deployments/"\n            f"{quote(name, safe=\'\')}"\n        )\n\n    def _replica_set_list_url(\n        self,\n        *,\n        namespace: str,\n        selector: str,\n    ) -> str:\n        query = urlencode(\n            {\n                "labelSelector": (\n                    selector\n                ),\n                "limit": str(\n                    self._MAX_HISTORY_ITEMS\n                ),\n            }\n        )\n\n        return (\n            f"{self.kubernetes.api_url}"\n            "/apis/apps/v1/namespaces/"\n            f"{quote(namespace, safe=\'\')}"\n            "/replicasets"\n            f"?{query}"\n        )\n\n    def _now(\n        self,\n    ) -> datetime:\n        value = self.kubernetes._clock()\n\n        if value.tzinfo is None:\n            return value.replace(\n                tzinfo=UTC\n            )\n\n        return value.astimezone(\n            UTC\n        )\n\n\n__all__ = [\n    "KubernetesChangeResponseError",\n    "KubernetesChangeTool",\n    "KubernetesChangeToolError",\n    "KubernetesChangeTopologyError",\n]\n'
PROBES_SOURCE = 'import re\nfrom collections.abc import Mapping\nfrom datetime import UTC, datetime\nfrom math import isfinite\nfrom typing import Any\n\nfrom services.agent_runtime.app.investigation.evidence_time import (\n    InvestigationEvidenceTimeError,\n    InvestigationEvidenceTimePolicy,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationProbe,\n    InvestigationScope,\n    default_investigation_probes,\n)\n\n\nclass InvestigationProbeError(RuntimeError):\n    """\n    Base error for the bounded read-only probe adapter.\n    """\n\n\nclass InvestigationToolUnavailableError(\n    InvestigationProbeError\n):\n    """\n    Runtime ToolManager is unavailable.\n    """\n\n\nclass InvestigationProbeResponseError(\n    InvestigationProbeError\n):\n    """\n    A read-only tool returned evidence that cannot cross the\n    Investigation trust boundary.\n    """\n\n\nclass ReadOnlyInvestigationProbeExecutor:\n    """\n    Translate symbolic Investigation probes into exact read-only tool calls.\n\n    The reasoner selects only an InvestigationProbe enum value.\n\n    This adapter owns:\n\n    - fixed Kubernetes read-only actions;\n    - fixed bounded previous-container log collection;\n    - fixed Prometheus query templates;\n    - provider/source validation;\n    - read-only mode validation;\n    - production-signal validation;\n    - observed-at validation;\n    - bounded evidence normalization.\n\n    The reasoner cannot provide Kubernetes verbs, resource kinds, PromQL,\n    URLs, credentials or raw tool arguments.\n    """\n\n    _TRUSTED_MODE = "read_only"\n    _MAX_LOG_TOOL_CHARS = 4000\n    _MAX_LOG_EVIDENCE_CHARS = 1800\n    _MAX_LOG_LINES = 80\n\n    def __init__(\n        self,\n        time_policy: (\n            InvestigationEvidenceTimePolicy\n            | None\n        ) = None,\n    ) -> None:\n        self.time_policy = (\n            time_policy\n            if time_policy is not None\n            else InvestigationEvidenceTimePolicy()\n        )\n\n    @staticmethod\n    def available_probes(\n        context,\n    ) -> list[InvestigationProbe]:\n        probes = default_investigation_probes()\n\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        registry = getattr(\n            tools,\n            "registry",\n            None,\n        )\n\n        getter = getattr(\n            registry,\n            "get",\n            None,\n        )\n\n        if not callable(\n            getter\n        ):\n            return probes\n\n        try:\n            change_tool = getter(\n                "kubernetes_change"\n            )\n        except KeyError:\n            return probes\n\n        if (\n            getattr(\n                change_tool,\n                "is_available",\n                True,\n            )\n            is not True\n        ):\n            return probes\n\n        probes.append(\n            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        )\n\n        return probes\n\n    async def collect(\n        self,\n        context,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> EvidenceItem:\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        if tools is None:\n            raise InvestigationToolUnavailableError(\n                "Runtime tools are unavailable"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="describe",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n            )\n\n            return self._normalize_kubernetes(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="previous_logs",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n            )\n\n            return self._normalize_kubernetes_logs(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        ):\n            result = await tools.call(\n                "kubernetes_change",\n                context=context,\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n                incident_time=(\n                    scope.event_occurred_at.isoformat()\n                    if scope.event_occurred_at\n                    is not None\n                    else None\n                ),\n            )\n\n            return self._normalize_kubernetes_change(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        query = self._prometheus_query(\n            scope=scope,\n            probe=probe,\n        )\n\n        query_time = self.time_policy.query_time(\n            scope=scope,\n            probe=probe,\n        )\n\n        call_arguments = {\n            "query": query,\n        }\n\n        if query_time is not None:\n            call_arguments["time"] = (\n                query_time\n            )\n\n        result = await tools.call(\n            "prometheus",\n            context=context,\n            **call_arguments,\n        )\n\n        return self._normalize_prometheus(\n            scope=scope,\n            probe=probe,\n            result=result,\n        )\n\n    @classmethod\n    def _prometheus_query(\n        cls,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> str:\n        labels = [\n            (\n                \'pod="\'\n                f\'{cls._escape_label(scope.resource)}\'\n                \'"\'\n            ),\n            (\n                \'namespace="\'\n                f\'{cls._escape_label(scope.namespace)}\'\n                \'"\'\n            ),\n        ]\n\n        if scope.cluster:\n            labels.append(\n                \'cluster="\'\n                f\'{cls._escape_label(scope.cluster)}\'\n                \'"\'\n            )\n\n        selector = ",".join(\n            labels\n        )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n        ):\n            return (\n                "sum(container_memory_working_set_bytes{"\n                f\'{selector},container!="POD",container!="",image!=""\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n        ):\n            return (\n                "sum(kube_pod_container_resource_limits{"\n                f\'{selector},resource="memory",unit="byte"\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_RESTART_COUNT\n        ):\n            return (\n                "sum(kube_pod_container_status_restarts_total{"\n                f"{selector}"\n                "})"\n            )\n\n        raise InvestigationProbeError(\n            "Unsupported investigation probe"\n        )\n\n    def _normalize_kubernetes(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if "phase" not in data:\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence phase is missing"\n            )\n\n        containers = data.get(\n            "containers"\n        )\n\n        if not isinstance(\n            containers,\n            list,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence containers are invalid"\n            )\n\n        restart_counts: list[int] = []\n        state_reasons: set[str] = set()\n        termination_reasons: set[str] = set()\n\n        for container in containers[:32]:\n            if not isinstance(\n                container,\n                Mapping,\n            ):\n                continue\n\n            restart_count = container.get(\n                "restart_count"\n            )\n\n            if isinstance(\n                restart_count,\n                int,\n            ):\n                restart_counts.append(\n                    restart_count\n                )\n\n            state_reason = container.get(\n                "state_reason"\n            )\n\n            if (\n                isinstance(\n                    state_reason,\n                    str,\n                )\n                and state_reason\n            ):\n                state_reasons.add(\n                    state_reason[:128]\n                )\n\n            termination_reason = container.get(\n                "last_termination_reason"\n            )\n\n            if (\n                isinstance(\n                    termination_reason,\n                    str,\n                )\n                and termination_reason\n            ):\n                termination_reasons.add(\n                    termination_reason[:128]\n                )\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "phase": cls_scalar(\n                data.get("phase")\n            ),\n            "ready": cls_scalar(\n                data.get("ready")\n            ),\n            "scheduled": cls_scalar(\n                data.get("scheduled")\n            ),\n            "oom_killed": cls_scalar(\n                data.get("oom_killed")\n            ),\n            "max_restart_count": (\n                max(restart_counts)\n                if restart_counts\n                else None\n            ),\n            "state_reasons": (\n                ",".join(\n                    sorted(\n                        state_reasons\n                    )\n                )\n                if state_reasons\n                else None\n            ),\n            "last_termination_reasons": (\n                ",".join(\n                    sorted(\n                        termination_reasons\n                    )\n                )\n                if termination_reasons\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_logs(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if (\n            data.get(\n                "previous"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence is not previous-container output"\n            )\n\n        container_value = data.get(\n            "container_name"\n        )\n\n        if not isinstance(\n            container_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        container_name = (\n            container_value\n            .strip()\n        )\n\n        if (\n            not container_name\n            or len(\n                container_name\n            )\n            > 128\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        line_count = data.get(\n            "line_count"\n        )\n\n        if (\n            not isinstance(\n                line_count,\n                int,\n            )\n            or isinstance(\n                line_count,\n                bool,\n            )\n            or line_count < 0\n            or line_count > self._MAX_LOG_LINES\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence line count is invalid"\n            )\n\n        truncated = data.get(\n            "truncated"\n        )\n\n        if not isinstance(\n            truncated,\n            bool,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence truncation flag is invalid"\n            )\n\n        redaction_count = data.get(\n            "redaction_count"\n        )\n\n        if (\n            not isinstance(\n                redaction_count,\n                int,\n            )\n            or isinstance(\n                redaction_count,\n                bool,\n            )\n            or redaction_count < 0\n            or redaction_count > 10000\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence redaction count is invalid"\n            )\n\n        excerpt_value = data.get(\n            "excerpt"\n        )\n\n        if not isinstance(\n            excerpt_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is invalid"\n            )\n\n        if len(\n            excerpt_value\n        ) > self._MAX_LOG_TOOL_CHARS:\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is too large"\n            )\n\n        excerpt, local_redactions = (\n            redact_log_excerpt(\n                excerpt_value\n            )\n        )\n\n        redaction_count = (\n            redaction_count\n            + local_redactions\n        )\n\n        evidence_truncated = (\n            len(\n                excerpt\n            )\n            > self._MAX_LOG_EVIDENCE_CHARS\n        )\n\n        if evidence_truncated:\n            excerpt = excerpt[\n                -self._MAX_LOG_EVIDENCE_CHARS:\n            ]\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "container_name": container_name,\n            "previous": True,\n            "log_line_count": line_count,\n            "tool_truncated": truncated,\n            "evidence_truncated": (\n                evidence_truncated\n            ),\n            "redaction_count": (\n                redaction_count\n            ),\n            "log_excerpt": (\n                excerpt\n                if excerpt\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_change(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes_change",\n            )\n        )\n\n        if (\n            data.get(\n                "owner_chain_verified"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change owner chain is untrusted"\n            )\n\n        if (\n            data.get(\n                "workload_kind"\n            )\n            != "Deployment"\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change workload kind is unsupported"\n            )\n\n        deployment_name = bounded_change_text(\n            data.get(\n                "deployment_name"\n            ),\n            required=True,\n        )\n\n        rollout_started_at = bounded_change_text(\n            data.get(\n                "rollout_started_at"\n            ),\n            required=False,\n        )\n\n        rollout_offset_seconds = None\n        recent_rollout_before_incident = None\n\n        if (\n            rollout_started_at is not None\n            and scope.event_occurred_at\n            is not None\n        ):\n            rollout_time = parse_observed_at(\n                rollout_started_at\n            )\n\n            rollout_offset_seconds = (\n                scope.event_occurred_at\n                .astimezone(\n                    UTC\n                )\n                - rollout_time\n            ).total_seconds()\n\n            recent_rollout_before_incident = (\n                0.0\n                <= rollout_offset_seconds\n                <= 1800.0\n            )\n\n        facts = {\n            "temporal_basis": (\n                "workload_change_history"\n            ),\n            "owner_chain_verified": True,\n            "deployment_name": (\n                deployment_name\n            ),\n            "revision_before": bounded_change_int(\n                data.get(\n                    "revision_before"\n                )\n            ),\n            "revision_after": bounded_change_int(\n                data.get(\n                    "revision_after"\n                )\n            ),\n            "revision_changed": bounded_change_bool(\n                data.get(\n                    "revision_changed"\n                )\n            ),\n            "image_before": bounded_change_text(\n                data.get(\n                    "image_before"\n                ),\n                required=False,\n            ),\n            "image_after": bounded_change_text(\n                data.get(\n                    "image_after"\n                ),\n                required=False,\n            ),\n            "image_changed": bounded_change_bool(\n                data.get(\n                    "image_changed"\n                )\n            ),\n            "rollout_started_at": (\n                rollout_started_at\n            ),\n            "rollout_offset_seconds": (\n                rollout_offset_seconds\n            ),\n            "recent_rollout_before_incident": (\n                recent_rollout_before_incident\n            ),\n            "generation": bounded_change_int(\n                data.get(\n                    "generation"\n                )\n            ),\n            "observed_generation": bounded_change_int(\n                data.get(\n                    "observed_generation"\n                )\n            ),\n            "replicas_desired": bounded_change_int(\n                data.get(\n                    "replicas_desired"\n                )\n            ),\n            "replicas_updated": bounded_change_int(\n                data.get(\n                    "replicas_updated"\n                )\n            ),\n            "replicas_ready": bounded_change_int(\n                data.get(\n                    "replicas_ready"\n                )\n            ),\n            "replicas_available": bounded_change_int(\n                data.get(\n                    "replicas_available"\n                )\n            ),\n            "replicas_unavailable": bounded_change_int(\n                data.get(\n                    "replicas_unavailable"\n                )\n            ),\n            "history_complete": bounded_change_bool(\n                data.get(\n                    "history_complete"\n                )\n            ),\n            "rollout_condition_summary": bounded_change_text(\n                data.get(\n                    "rollout_condition_summary"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "generation_observed": bounded_change_bool(\n                data.get(\n                    "generation_observed"\n                )\n            ),\n            "rollout_complete": bounded_change_bool(\n                data.get(\n                    "rollout_complete"\n                )\n            ),\n            "rollout_failure_signal": bounded_change_bool(\n                data.get(\n                    "rollout_failure_signal"\n                )\n            ),\n            "rollout_failure_reason": bounded_change_text(\n                data.get(\n                    "rollout_failure_reason"\n                ),\n                required=False,\n            ),\n            "events_status": bounded_change_events_status(\n                data.get(\n                    "events_status"\n                )\n            ),\n            "events_error_code": bounded_change_text(\n                data.get(\n                    "events_error_code"\n                ),\n                required=False,\n            ),\n            "recent_event_count": bounded_change_int(\n                data.get(\n                    "recent_event_count"\n                )\n            ),\n            "recent_warning_count": bounded_change_int(\n                data.get(\n                    "recent_warning_count"\n                )\n            ),\n            "recent_event_reasons": bounded_change_text(\n                data.get(\n                    "recent_event_reasons"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "recent_event_summary": bounded_change_text(\n                data.get(\n                    "recent_event_summary"\n                ),\n                required=False,\n                max_length=1536,\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes_change",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_prometheus(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="prometheus",\n            )\n        )\n\n        result_type_value = data.get(\n            "resultType"\n        )\n\n        if (\n            not isinstance(\n                result_type_value,\n                str,\n            )\n            or result_type_value\n            not in {\n                "vector",\n                "matrix",\n                "scalar",\n                "string",\n            }\n        ):\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence result type is invalid"\n            )\n\n        result_type = (\n            result_type_value[:64]\n        )\n\n        samples = extract_numeric_samples(\n            result_type=result_type,\n            value=data.get(\n                "result"\n            ),\n        )\n\n        if not samples:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence contains no numeric samples"\n            )\n\n        try:\n            event_offset_seconds = (\n                self.time_policy.validate_observed_at(\n                    scope=scope,\n                    probe=probe,\n                    observed_at=observed_at,\n                )\n            )\n        except InvestigationEvidenceTimeError as exc:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence is not "\n                "temporally relevant"\n            ) from exc\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "event_offset_seconds": (\n                event_offset_seconds\n            ),\n            "result_type": result_type,\n            "sample_count": len(\n                samples\n            ),\n            "value_sum": sum(\n                samples\n            ),\n            "value_min": min(\n                samples\n            ),\n            "value_max": max(\n                samples\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="prometheus",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    @classmethod\n    def _validate_tool_evidence(\n        cls,\n        *,\n        result: Any,\n        expected_source: str,\n    ) -> tuple[\n        Mapping[str, Any],\n        datetime,\n    ]:\n        if not isinstance(\n            result,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result is invalid"\n            )\n\n        if (\n            result.get(\n                "success"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result was unsuccessful"\n            )\n\n        source_value = result.get(\n            "source"\n        )\n\n        if not isinstance(\n            source_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is invalid"\n            )\n\n        source = (\n            source_value\n            .strip()\n            .lower()\n        )\n\n        if source != expected_source:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is untrusted"\n            )\n\n        mode_value = result.get(\n            "mode"\n        )\n\n        if not isinstance(\n            mode_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is invalid"\n            )\n\n        mode = (\n            mode_value\n            .strip()\n            .lower()\n        )\n\n        if mode != cls._TRUSTED_MODE:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is not read-only"\n            )\n\n        if (\n            result.get(\n                "production_signal"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence is not a production signal"\n            )\n\n        observed_at = parse_observed_at(\n            result.get(\n                "observed_at"\n            )\n        )\n\n        data = result.get(\n            "data"\n        )\n\n        if not isinstance(\n            data,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence data is invalid"\n            )\n\n        return (\n            data,\n            observed_at,\n        )\n\n    @staticmethod\n    def _escape_label(\n        value: str,\n    ) -> str:\n        return (\n            value\n            .replace(\n                "\\\\",\n                "\\\\\\\\",\n            )\n            .replace(\n                "\\n",\n                "\\\\n",\n            )\n            .replace(\n                "\\r",\n                "\\\\r",\n            )\n            .replace(\n                \'"\',\n                \'\\\\"\',\n            )\n        )\n\n\ndef redact_log_excerpt(\n    value: str,\n) -> tuple[str, int]:\n    """\n    Defense-in-depth redaction at the Investigation trust boundary.\n\n    KubernetesTool redacts before ToolManager tracing. This second pass keeps\n    injected or forged ToolManager responses from placing obvious credentials\n    into bounded InvestigationState.\n    """\n\n    text = value\n    total = 0\n\n    patterns = [\n        (\n            re.compile(\n                (\n                    r"\\beyJ[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}\\b"\n                )\n            ),\n            "[REDACTED_JWT]",\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"bearer|basic"\n                    r")\\s+"\n                    r"[A-Za-z0-9._~+/=-]{8,}"\n                )\n            ),\n            None,\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"password|passwd|pwd|secret|token|"\n                    r"api[_-]?key|access[_-]?key|"\n                    r"client[_-]?secret"\n                    r")\\b"\n                    r"(\\s*[:=]\\s*)"\n                    r"([\\"\']?)"\n                    r"([^\\s,;\\"\']{4,})"\n                    r"([\\"\']?)"\n                )\n            ),\n            None,\n        ),\n    ]\n\n    text, count = patterns[0][0].subn(\n        patterns[0][1],\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[1][0].subn(\n        lambda match: (\n            match.group(1)\n            + " [REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[2][0].subn(\n        lambda match: (\n            match.group(1)\n            + match.group(2)\n            + "[REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    return (\n        text,\n        total,\n    )\n\n\ndef bounded_change_text(\n    value: Any,\n    *,\n    required: bool,\n    max_length: int = 512,\n) -> str | None:\n    if value is None:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if not isinstance(\n        value,\n        str,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is invalid"\n        )\n\n    normalized = value.strip()\n\n    if not normalized:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if len(\n        normalized\n    ) > max_length:\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is too large"\n        )\n\n    return normalized\n\n\ndef bounded_change_int(\n    value: Any,\n) -> int | None:\n    if value is None:\n        return None\n\n    if (\n        isinstance(\n            value,\n            bool,\n        )\n        or not isinstance(\n            value,\n            int,\n        )\n        or value < 0\n        or value > 1_000_000_000\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change integer fact is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_events_status(\n    value: Any,\n) -> str | None:\n    if value is None:\n        return None\n\n    if value not in {\n        "complete",\n        "partial",\n        "unavailable",\n    }:\n        raise InvestigationProbeResponseError(\n            "Kubernetes event evidence status is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_bool(\n    value: Any,\n) -> bool | None:\n    if value is None:\n        return None\n\n    if not isinstance(\n        value,\n        bool,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change boolean fact is invalid"\n        )\n\n    return value\n\n\ndef cls_scalar(\n    value: Any,\n):\n    if (\n        value is None\n        or isinstance(\n            value,\n            (\n                bool,\n                int,\n                float,\n                str,\n            ),\n        )\n    ):\n        return value\n\n    return str(\n        value\n    )[:256]\n\n\ndef parse_observed_at(\n    value: Any,\n) -> datetime:\n    if isinstance(\n        value,\n        datetime,\n    ):\n        parsed = value\n\n    elif isinstance(\n        value,\n        str,\n    ):\n        text = value.strip()\n\n        if not text:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            )\n\n        if text.endswith(\n            "Z"\n        ):\n            text = (\n                f"{text[:-1]}+00:00"\n            )\n\n        try:\n            parsed = datetime.fromisoformat(\n                text\n            )\n        except ValueError as exc:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            ) from exc\n\n    else:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at is invalid"\n        )\n\n    if parsed.tzinfo is None:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at must be timezone-aware"\n        )\n\n    return parsed.astimezone(\n        UTC\n    )\n\n\ndef extract_numeric_samples(\n    result_type: str | None,\n    value: Any,\n) -> list[float]:\n    samples: list[float] = []\n\n    def add_sample(\n        sample: Any,\n    ) -> None:\n        if (\n            not isinstance(\n                sample,\n                list,\n            )\n            or len(sample) < 2\n            or len(samples) >= 32\n        ):\n            return\n\n        try:\n            numeric_value = float(\n                sample[1]\n            )\n        except (\n            TypeError,\n            ValueError,\n        ):\n            return\n\n        if not isfinite(\n            numeric_value\n        ):\n            return\n\n        samples.append(\n            numeric_value\n        )\n\n    if result_type in {\n        "scalar",\n        "string",\n    }:\n        add_sample(\n            value\n        )\n\n    elif (\n        result_type == "vector"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if isinstance(\n                item,\n                Mapping,\n            ):\n                add_sample(\n                    item.get(\n                        "value"\n                    )\n                )\n\n    elif (\n        result_type == "matrix"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            values = item.get(\n                "values"\n            )\n\n            if (\n                isinstance(\n                    values,\n                    list,\n                )\n                and values\n            ):\n                add_sample(\n                    values[-1]\n                )\n\n    return samples\n\n\n__all__ = [\n    "InvestigationProbeError",\n    "InvestigationProbeResponseError",\n    "InvestigationToolUnavailableError",\n    "ReadOnlyInvestigationProbeExecutor",\n    "extract_numeric_samples",\n    "parse_observed_at",\n]\n'
REASONER_SOURCE = 'import json\nfrom abc import ABC, abstractmethod\n\nfrom pydantic import ValidationError\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\n\n\nclass InvestigationReasonerError(RuntimeError):\n    """\n    Sanitized reasoner failure.\n    """\n\n\nclass InvestigationReasonerJSONError(\n    InvestigationReasonerError\n):\n    """\n    Primary decision was not valid JSON.\n    """\n\n\nclass InvestigationReasonerValidationError(\n    InvestigationReasonerError\n):\n    """\n    Primary JSON did not satisfy InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerRepairJSONError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still did not return valid JSON.\n    """\n\n\nclass InvestigationReasonerRepairValidationError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still violated InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerExecutionRetryError(\n    InvestigationReasonerError\n):\n    """\n    The sanitized LLM execution failed twice for the same reasoning request.\n    """\n\n\nclass BaseInvestigationReasoner(ABC):\n    """\n    Select the next symbolic read-only probe or stop with a conclusion.\n    """\n\n    @abstractmethod\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        ...\n\n\nclass LLMInvestigationReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Structured LLM reasoner for the bounded InvestigationCoordinator.\n\n    The reasoner depends only on the Investigation-owned LLM abstraction.\n    Gateway routing, provider selection, fallback, rate limiting and circuit\n    breaking remain outside this class.\n\n    Transport execution retry ownership remains entirely in the shared\n    LLM Gateway. The Reasoner does not repeat a failed Gateway request.\n    Its only bounded second model call is structured Decision-contract repair\n    after a model response was successfully received but failed validation.\n\n    It can select only an InvestigationProbe enum value. It cannot construct\n    tool calls, resource scope, PromQL, URLs or credentials.\n    """\n\n    _SYSTEM_PROMPT = (\n        "You are a bounded SRE investigation reasoner. "\n        "Maintain competing hypotheses, use only supplied "\n        "evidence, and select only one allowed symbolic "\n        "read-only probe. Never propose or execute a write."\n    )\n\n    def __init__(\n        self,\n        investigation_llm: BaseInvestigationLLM,\n    ) -> None:\n        if not isinstance(\n            investigation_llm,\n            BaseInvestigationLLM,\n        ):\n            raise TypeError(\n                "Investigation LLM adapter is invalid"\n            )\n\n        self.investigation_llm = (\n            investigation_llm\n        )\n\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        prompt = self._build_prompt(\n            scope=scope,\n            state=state,\n        )\n\n        content = await self.investigation_llm.complete(\n            system_prompt=self._SYSTEM_PROMPT,\n            prompt=prompt,\n        )\n\n        if not isinstance(\n            content,\n            str,\n        ):\n            raise InvestigationReasonerError(\n                "Investigation reasoner returned no JSON"\n            )\n\n        try:\n            decision = self._parse_decision(\n                content,\n                repair=False,\n            )\n\n            self._validate_decision_against_state(\n                decision=decision,\n                state=state,\n                repair=False,\n            )\n\n            return decision\n\n        except (\n            InvestigationReasonerJSONError,\n            InvestigationReasonerValidationError,\n        ) as primary_error:\n            repair_content = await self.investigation_llm.complete(\n                system_prompt=(\n                    self._SYSTEM_PROMPT\n                    + " Repair the decision contract only; "\n                    "do not invent new evidence."\n                ),\n                prompt=self._build_repair_prompt(\n                    scope=scope,\n                    state=state,\n                    primary_error=primary_error,\n                ),\n            )\n\n            if not isinstance(\n                repair_content,\n                str,\n            ):\n                raise InvestigationReasonerError(\n                    "Investigation reasoner repair returned no JSON"\n                ) from primary_error\n\n            try:\n                decision = self._parse_decision(\n                    repair_content,\n                    repair=True,\n                )\n\n                self._validate_decision_against_state(\n                    decision=decision,\n                    state=state,\n                    repair=True,\n                )\n\n                return decision\n\n            except InvestigationReasonerError as repair_error:\n                raise repair_error from primary_error\n\n    @staticmethod\n    def _validate_decision_against_state(\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n        repair: bool,\n    ) -> None:\n        probe = decision.next_probe\n\n        remaining_tool_calls = max(\n            0,\n            (\n                state.limits.max_tool_calls\n                - state.tool_call_count\n            ),\n        )\n\n        remaining_reasoning_iterations = max(\n            0,\n            (\n                state.limits.max_iterations\n                - state.iteration_count\n            ),\n        )\n\n        if (\n            not decision.stop\n            and (\n                remaining_tool_calls <= 0\n                or remaining_reasoning_iterations <= 1\n            )\n        ):\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                "Investigation reasoner must return a terminal decision "\n                "because no safe probe-plus-final-synthesis budget remains"\n            )\n\n        if (\n            probe is not None\n            and probe in state.attempted_probes\n        ):\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                "Investigation reasoner selected an already-attempted probe"\n            )\n\n    @staticmethod\n    def _parse_decision(\n        content: str,\n        *,\n        repair: bool,\n    ) -> InvestigationDecision:\n        try:\n            payload = json.loads(\n                content\n            )\n\n        except json.JSONDecodeError as exc:\n            error_type = (\n                InvestigationReasonerRepairJSONError\n                if repair\n                else InvestigationReasonerJSONError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned invalid JSON"\n                    if repair\n                    else "Investigation reasoner returned invalid JSON"\n                )\n            ) from exc\n\n        try:\n            return InvestigationDecision.model_validate(\n                payload\n            )\n\n        except (\n            ValidationError,\n            TypeError,\n            ValueError,\n        ) as exc:\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned an invalid decision"\n                    if repair\n                    else "Investigation reasoner returned an invalid decision"\n                )\n            ) from exc\n\n    @classmethod\n    def _build_repair_prompt(\n        cls,\n        *,\n        scope: InvestigationScope,\n        state: InvestigationState,\n        primary_error: InvestigationReasonerError,\n    ) -> str:\n        failure_kind = type(\n            primary_error\n        ).__name__\n\n        return (\n            "Your previous decision failed the bounded structured-output "\n            f"contract with failure type {failure_kind}.\\n"\n            "Do not repeat or explain the invalid response.\\n"\n            "Re-evaluate the SAME supplied state. Do not invent evidence, "\n            "do not add a tool call outside allowed_probes, and do not "\n            "change resource scope.\\n"\n            "Return exactly one corrected JSON decision that satisfies every "\n            "shape and evidence rule below.\\n\\n"\n            + cls._build_prompt(\n                scope=scope,\n                state=state,\n            )\n        )\n\n    @staticmethod\n    def _build_prompt(\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> str:\n        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        attempted_probe_set = set(\n            state.attempted_probes\n        )\n\n        remaining_tool_calls = max(\n            0,\n            (\n                state.limits.max_tool_calls\n                - state.tool_call_count\n            ),\n        )\n\n        remaining_reasoning_iterations = max(\n            0,\n            (\n                state.limits.max_iterations\n                - state.iteration_count\n            ),\n        )\n\n        continuation_allowed = (\n            remaining_tool_calls > 0\n            and remaining_reasoning_iterations > 1\n        )\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "max_iterations": state.limits.max_iterations,\n            "remaining_reasoning_iterations": (\n                remaining_reasoning_iterations\n            ),\n            "tool_call_count": state.tool_call_count,\n            "max_tool_calls": state.limits.max_tool_calls,\n            "remaining_tool_calls": remaining_tool_calls,\n            "continuation_allowed": continuation_allowed,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "failed_probes": [\n                item.probe.value\n                for item in state.evidence\n                if not item.success\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in state.available_probes\n                if probe not in attempted_probe_set\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Probe affordances:\\n"\n            "- kubernetes_pod_state: current pod/container state, restart "\n            "indicators, and last termination reasons.\\n"\n            "- kubernetes_previous_container_logs: bounded previous-container "\n            "output; high-information evidence for unexplained restart, startup, "\n            "panic, configuration, dependency, or crash symptoms.\\n"\n            "- kubernetes_workload_change: bounded trusted Deployment-owner "\n            "change context for a Pod, including current/previous rollout "\n            "revision, image-before/image-after, rollout time, "\n            "generation/observedGeneration, replica status, Deployment "\n            "Progressing/Available/ReplicaFailure conditions, and bounded "\n            "incident-window Kubernetes Event summaries when event RBAC is "\n            "available. Treat rollout_failure_signal, ProgressDeadlineExceeded, "\n            "ReplicaFailure, FailedCreate, BackOff, FailedScheduling, or image "\n            "pull events as discriminative operational evidence. This remains "\n            "temporal change evidence and is not by itself proof that the change "\n            "caused the incident; temporal change evidence alone is not proof of "\n            "causation.\\n"\n            "- prometheus_memory_working_set: sampled container memory usage.\\n"\n            "- prometheus_memory_limit: configured container memory limit.\\n"\n            "- prometheus_restart_count: sampled restart frequency/corroboration.\\n"\n            "If trusted evidence falsifies the current leading hypothesis but "\n            "the observed incident symptom remains unexplained, do not stop "\n            "solely because that hypothesis was rejected. Replan with at least "\n            "one evidence-plausible alternative hypothesis when an unattempted "\n            "allowed probe can materially discriminate plausible causes.\\n"\n            "Use insufficient_evidence only when no unattempted safe probe can "\n            "materially discriminate the remaining plausible causes, or when "\n            "required evidence is unavailable.\\n"\n            "State.allowed_probes already excludes every attempted probe. "\n            "Select next_probe only from State.allowed_probes.\\n"\n            "Budget discipline is mandatory. State.remaining_tool_calls is the "\n            "number of additional read-only probes that may still execute. "\n            "State.remaining_reasoning_iterations counts this decision and any "\n            "future synthesis decisions. A continuing decision consumes the "\n            "current reasoning iteration and requires at least one later "\n            "reasoning iteration to interpret the new evidence. Therefore, if "\n            "State.continuation_allowed is false, you MUST return a terminal "\n            "decision now with next_probe=null. Do not request one more probe "\n            "when there is no probe-plus-final-synthesis budget remaining.\\n"\n            "Do not spend the final useful budget on evidence that only "\n            "corroborates frequency, severity, or a symptom already established "\n            "when it cannot resolve required root-cause mechanism evidence or "\n            "materially falsify a competing hypothesis. For example, restart "\n            "count is corroborative and does not establish why a CrashLoop or "\n            "OOM occurred.\\n"\n            "A failed probe is still an attempted probe. Do not retry it inside "\n            "the same investigation; keep its required evidence missing and "\n            "use another unattempted discriminative probe or safely abstain.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "A recent rollout, revision change, image change, or replica state "\n            "is temporal/correlation evidence. Do not claim that a workload "\n            "change CAUSED the incident from change evidence alone. Pair change "\n            "evidence with independent symptom or mechanism evidence such as "\n            "logs, termination state, or relevant metrics before accepting a "\n            "change-caused root cause.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "A symptom or failure-mode observation such as CrashLoopBackOff, "\n            "restart count, unready state, high latency, or high error rate can "\n            "confirm that a failure exists, but does not by itself establish the "\n            "specific underlying cause that produced it.\\n"\n            "If several underlying causes remain plausible and current allowed "\n            "probes cannot discriminate among them, keep the required "\n            "root-cause evidence in hypothesis.missing_evidence and stop with "\n            "insufficient_evidence or no_safe_probe.\\n"\n            "Use hypothesis.missing_evidence only for evidence that is REQUIRED "\n            "before the specific root cause can be accepted. Use "\n            "hypothesis.optional_evidence for corroboration that may increase "\n            "confidence or describe frequency/severity but is not required to "\n            "establish the root cause.\\n"\n            "Do not put the same evidence need in both missing_evidence and "\n            "optional_evidence.\\n"\n            "Do not clear missing_evidence merely because all allowed probes "\n            "have been attempted. For sufficient_evidence, the positively "\n            "supported hypothesis used by the conclusion must have an empty "\n            "missing_evidence list. optional_evidence may remain non-empty.\\n"\n            "Treat event evidence separately from mechanism evidence. For example, "\n            "OOMKilled proves that an OOM termination occurred, but does not by "\n            "itself prove that a configured container memory limit was exceeded.\\n"\n            "A point-in-time or sampled metric cannot establish an unobserved "\n            "transient peak, historical trend, or threshold crossing. Never invent "\n            "an unseen spike to make a hypothesis fit.\\n"\n            "For quantitative threshold causes, supporting evidence must be "\n            "directionally consistent with the claimed mechanism. If a sampled "\n            "working value is below the sampled limit, that sample is not positive "\n            "support for the claim that the limit was exceeded.\\n"\n            "If an event is confirmed but the available sampled metrics do not "\n            "explain its mechanism, keep the required historical/range/peak "\n            "evidence in missing_evidence and stop with insufficient_evidence "\n            "unless another direct causal observation establishes the cause.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "If the available evidence only rejects hypotheses or confirms a "\n            "symptom/failure mode without establishing its cause, stop with "\n            "insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required evidence"], "optional_evidence": ["non-blocking corroboration"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": [], "optional_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required missing evidence"], "optional_evidence": ["non-blocking evidence if useful"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n\n\n__all__ = [\n    "BaseInvestigationReasoner",\n    "InvestigationReasonerError",\n    "LLMInvestigationReasoner",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\n\nimport httpx\nimport pytest\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    ReadOnlyInvestigationProbeExecutor,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.tools.kubernetes.change_tool import (\n    KubernetesChangeTool,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesTool,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    11,\n    10,\n    30,\n    tzinfo=UTC,\n)\n\nINCIDENT = datetime(\n    2026,\n    8,\n    11,\n    10,\n    20,\n    tzinfo=UTC,\n)\n\n\ndef owner(\n    kind: str,\n    name: str,\n    uid: str,\n):\n    return {\n        "apiVersion": (\n            "apps/v1"\n            if kind\n            in {\n                "ReplicaSet",\n                "Deployment",\n            }\n            else "v1"\n        ),\n        "kind": kind,\n        "name": name,\n        "uid": uid,\n        "controller": True,\n        "blockOwnerDeletion": True,\n    }\n\n\ndef replica_set(\n    *,\n    name: str,\n    uid: str,\n    revision: int,\n    image: str,\n):\n    return {\n        "apiVersion": "apps/v1",\n        "kind": "ReplicaSet",\n        "metadata": {\n            "name": name,\n            "namespace": "payment",\n            "uid": uid,\n            "creationTimestamp": (\n                "2026-08-11T10:15:00Z"\n                if revision == 7\n                else "2026-08-10T22:00:00Z"\n            ),\n            "annotations": {\n                "deployment.kubernetes.io/revision": (\n                    str(\n                        revision\n                    )\n                )\n            },\n            "ownerReferences": [\n                owner(\n                    "Deployment",\n                    "payment-api",\n                    "deployment-uid",\n                )\n            ],\n        },\n        "spec": {\n            "template": {\n                "spec": {\n                    "containers": [\n                        {\n                            "name": "app",\n                            "image": image,\n                        }\n                    ]\n                }\n            }\n        },\n    }\n\n\ndef deployment():\n    return {\n        "apiVersion": "apps/v1",\n        "kind": "Deployment",\n        "metadata": {\n            "name": "payment-api",\n            "namespace": "payment",\n            "uid": "deployment-uid",\n            "generation": 9,\n            "annotations": {\n                "deployment.kubernetes.io/revision": "7"\n            },\n        },\n        "spec": {\n            "replicas": 4,\n            "selector": {\n                "matchLabels": {\n                    "app": "payment-api"\n                }\n            },\n            "template": {\n                "spec": {\n                    "containers": [\n                        {\n                            "name": "app",\n                            "image": "payment-api:v7",\n                        }\n                    ]\n                }\n            },\n        },\n        "status": {\n            "observedGeneration": 9,\n            "updatedReplicas": 4,\n            "readyReplicas": 2,\n            "availableReplicas": 2,\n            "unavailableReplicas": 2,\n            "conditions": [\n                {\n                    "type": "Progressing",\n                    "status": "False",\n                    "reason": "ProgressDeadlineExceeded",\n                },\n                {\n                    "type": "Available",\n                    "status": "False",\n                    "reason": "MinimumReplicasUnavailable",\n                },\n                {\n                    "type": "ReplicaFailure",\n                    "status": "True",\n                    "reason": "FailedCreate",\n                },\n            ],\n        },\n    }\n\n\ndef event(\n    *,\n    uid: str,\n    kind: str,\n    name: str,\n    event_type: str,\n    reason: str,\n    message: str,\n    timestamp: str,\n):\n    return {\n        "apiVersion": "v1",\n        "kind": "Event",\n        "metadata": {\n            "creationTimestamp": timestamp,\n        },\n        "involvedObject": {\n            "uid": uid,\n            "kind": kind,\n            "name": name,\n        },\n        "type": event_type,\n        "reason": reason,\n        "message": message,\n        "lastTimestamp": timestamp,\n    }\n\n\ndef object_handler(\n    request: httpx.Request,\n) -> httpx.Response:\n    path = request.url.path\n\n    if path.endswith(\n        "/pods/payment-api"\n    ):\n        return httpx.Response(\n            200,\n            json={\n                "apiVersion": "v1",\n                "kind": "Pod",\n                "metadata": {\n                    "name": "payment-api",\n                    "namespace": "payment",\n                    "uid": "pod-uid",\n                    "ownerReferences": [\n                        owner(\n                            "ReplicaSet",\n                            "payment-api-7b9f",\n                            "rs-current-uid",\n                        )\n                    ],\n                },\n            },\n            request=request,\n        )\n\n    if path.endswith(\n        "/replicasets/payment-api-7b9f"\n    ):\n        return httpx.Response(\n            200,\n            json=replica_set(\n                name="payment-api-7b9f",\n                uid="rs-current-uid",\n                revision=7,\n                image="payment-api:v7",\n            ),\n            request=request,\n        )\n\n    if path.endswith(\n        "/deployments/payment-api"\n    ):\n        return httpx.Response(\n            200,\n            json=deployment(),\n            request=request,\n        )\n\n    if (\n        path.endswith(\n            "/replicasets"\n        )\n        and "labelSelector"\n        in request.url.params\n    ):\n        return httpx.Response(\n            200,\n            json={\n                "apiVersion": "apps/v1",\n                "kind": "ReplicaSetList",\n                "metadata": {},\n                "items": [\n                    replica_set(\n                        name="payment-api-6aaa",\n                        uid="rs-old-uid",\n                        revision=6,\n                        image="payment-api:v6",\n                    ),\n                    replica_set(\n                        name="payment-api-7b9f",\n                        uid="rs-current-uid",\n                        revision=7,\n                        image="payment-api:v7",\n                    ),\n                ],\n            },\n            request=request,\n        )\n\n    raise AssertionError(\n        f"unexpected object path: {request.url}"\n    )\n\n\ndef handler_with_events(\n    request: httpx.Request,\n) -> httpx.Response:\n    if request.url.path.endswith(\n        "/events"\n    ):\n        selector = request.url.params.get(\n            "fieldSelector"\n        )\n\n        if selector == (\n            "involvedObject.uid=pod-uid"\n        ):\n            items = [\n                event(\n                    uid="pod-uid",\n                    kind="Pod",\n                    name="payment-api",\n                    event_type="Warning",\n                    reason="BackOff",\n                    message=(\n                        "Back-off restarting failed container app"\n                    ),\n                    timestamp=(\n                        "2026-08-11T10:19:00Z"\n                    ),\n                )\n            ]\n\n        elif selector == (\n            "involvedObject.uid=rs-current-uid"\n        ):\n            items = [\n                event(\n                    uid="rs-current-uid",\n                    kind="ReplicaSet",\n                    name="payment-api-7b9f",\n                    event_type="Warning",\n                    reason="FailedCreate",\n                    message=(\n                        "Error creating pod during rollout"\n                    ),\n                    timestamp=(\n                        "2026-08-11T10:18:00Z"\n                    ),\n                )\n            ]\n\n        elif selector == (\n            "involvedObject.uid=deployment-uid"\n        ):\n            items = [\n                event(\n                    uid="deployment-uid",\n                    kind="Deployment",\n                    name="payment-api",\n                    event_type="Normal",\n                    reason="ScalingReplicaSet",\n                    message=(\n                        "Scaled up replica set payment-api-7b9f"\n                    ),\n                    timestamp=(\n                        "2026-08-11T10:15:30Z"\n                    ),\n                )\n            ]\n\n        else:\n            items = []\n\n        return httpx.Response(\n            200,\n            json={\n                "apiVersion": "v1",\n                "kind": "EventList",\n                "metadata": {},\n                "items": items,\n            },\n            request=request,\n        )\n\n    return object_handler(\n        request\n    )\n\n\ndef handler_events_forbidden(\n    request: httpx.Request,\n) -> httpx.Response:\n    if request.url.path.endswith(\n        "/events"\n    ):\n        return httpx.Response(\n            403,\n            json={},\n            request=request,\n        )\n\n    return object_handler(\n        request\n    )\n\n\n@pytest.mark.asyncio\nasync def test_change_tool_adds_rollout_conditions_and_incident_window_events():\n    transport = httpx.MockTransport(\n        handler_with_events\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport,\n    ) as client:\n        kubernetes = KubernetesTool(\n            api_url="https://kubernetes.test",\n            bearer_token="unit-token",\n            cluster_name="production-a",\n            allow_dry_run_fallback=False,\n            client=client,\n            clock=lambda: NOW,\n        )\n\n        result = await KubernetesChangeTool(\n            kubernetes\n        ).execute(\n            target="payment-api",\n            namespace="payment",\n            cluster="production-a",\n            incident_time=(\n                INCIDENT.isoformat()\n            ),\n        )\n\n    data = result[\n        "data"\n    ]\n\n    assert (\n        "Progressing=False:ProgressDeadlineExceeded"\n        in data[\n            "rollout_condition_summary"\n        ]\n    )\n\n    assert (\n        "ReplicaFailure=True:FailedCreate"\n        in data[\n            "rollout_condition_summary"\n        ]\n    )\n\n    assert data[\n        "rollout_failure_signal"\n    ] is True\n\n    assert (\n        "ProgressDeadlineExceeded"\n        in data[\n            "rollout_failure_reason"\n        ]\n    )\n\n    assert data[\n        "generation_observed"\n    ] is True\n\n    assert data[\n        "rollout_complete"\n    ] is False\n\n    assert data[\n        "events_status"\n    ] == "complete"\n\n    assert data[\n        "recent_event_count"\n    ] == 3\n\n    assert data[\n        "recent_warning_count"\n    ] == 2\n\n    assert (\n        "BackOff"\n        in data[\n            "recent_event_reasons"\n        ]\n    )\n\n    assert (\n        "FailedCreate"\n        in data[\n            "recent_event_reasons"\n        ]\n    )\n\n    assert (\n        "ScalingReplicaSet"\n        in data[\n            "recent_event_reasons"\n        ]\n    )\n\n    assert (\n        "Back-off restarting failed container"\n        in data[\n            "recent_event_summary"\n        ]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_event_rbac_denial_degrades_only_event_enrichment():\n    transport = httpx.MockTransport(\n        handler_events_forbidden\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport,\n    ) as client:\n        kubernetes = KubernetesTool(\n            api_url="https://kubernetes.test",\n            bearer_token="unit-token",\n            cluster_name="production-a",\n            allow_dry_run_fallback=False,\n            client=client,\n            clock=lambda: NOW,\n        )\n\n        result = await KubernetesChangeTool(\n            kubernetes\n        ).execute(\n            target="payment-api",\n            namespace="payment",\n            cluster="production-a",\n            incident_time=(\n                INCIDENT.isoformat()\n            ),\n        )\n\n    assert result[\n        "success"\n    ] is True\n\n    data = result[\n        "data"\n    ]\n\n    assert data[\n        "revision_after"\n    ] == 7\n\n    assert data[\n        "rollout_failure_signal"\n    ] is True\n\n    assert data[\n        "events_status"\n    ] == "unavailable"\n\n    assert data[\n        "events_error_code"\n    ] == "authorization_denied"\n\n    assert data[\n        "recent_event_count"\n    ] == 0\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api is restarting after rollout"\n        ),\n        event_occurred_at=INCIDENT,\n        resource="payment-api",\n        namespace="payment",\n        cluster="production-a",\n    )\n\n\ndef test_change_probe_normalizes_rollout_and_event_facts():\n    executor = (\n        ReadOnlyInvestigationProbeExecutor()\n    )\n\n    result = {\n        "success": True,\n        "source": "kubernetes_change",\n        "mode": "read_only",\n        "production_signal": True,\n        "observed_at": NOW.isoformat(),\n        "data": {\n            "owner_chain_verified": True,\n            "workload_kind": "Deployment",\n            "deployment_name": "payment-api",\n            "revision_before": 6,\n            "revision_after": 7,\n            "revision_changed": True,\n            "image_before": "app=payment-api:v6",\n            "image_after": "app=payment-api:v7",\n            "image_changed": True,\n            "rollout_started_at": (\n                "2026-08-11T10:15:00+00:00"\n            ),\n            "generation": 9,\n            "observed_generation": 9,\n            "replicas_desired": 4,\n            "replicas_updated": 4,\n            "replicas_ready": 2,\n            "replicas_available": 2,\n            "replicas_unavailable": 2,\n            "history_complete": True,\n            "rollout_condition_summary": (\n                "Progressing=False:ProgressDeadlineExceeded;"\n                "Available=False:MinimumReplicasUnavailable;"\n                "ReplicaFailure=True:FailedCreate"\n            ),\n            "generation_observed": True,\n            "rollout_complete": False,\n            "rollout_failure_signal": True,\n            "rollout_failure_reason": (\n                "ProgressDeadlineExceeded;FailedCreate"\n            ),\n            "events_status": "complete",\n            "events_error_code": None,\n            "recent_event_count": 3,\n            "recent_warning_count": 2,\n            "recent_event_reasons": (\n                "BackOff;FailedCreate;ScalingReplicaSet"\n            ),\n            "recent_event_summary": (\n                "Pod/payment-api Warning BackOff"\n            ),\n        },\n    }\n\n    evidence = (\n        executor\n        ._normalize_kubernetes_change(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            result=result,\n        )\n    )\n\n    assert evidence.trusted is True\n\n    assert evidence.facts[\n        "rollout_failure_signal"\n    ] is True\n\n    assert (\n        "ProgressDeadlineExceeded"\n        in evidence.facts[\n            "rollout_condition_summary"\n        ]\n    )\n\n    assert evidence.facts[\n        "events_status"\n    ] == "complete"\n\n    assert len(\n        evidence.facts\n    ) <= 32\n\n    assert evidence.facts[\n        "recent_warning_count"\n    ] == 2\n\n    assert (\n        "FailedCreate"\n        in evidence.facts[\n            "recent_event_reasons"\n        ]\n    )\n\n\ndef test_reasoner_prompt_documents_rollout_failure_and_event_semantics():\n    state = InvestigationState(\n        scope=scope(),\n        available_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n        ],\n    )\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=state.scope,\n            state=state,\n        )\n    )\n\n    assert (\n        "ProgressDeadlineExceeded"\n        in prompt\n    )\n\n    assert (\n        "ReplicaFailure"\n        in prompt\n    )\n\n    assert (\n        "Kubernetes Event summaries"\n        in prompt\n    )\n\n    assert (\n        "not by itself proof"\n        in prompt\n    )\n\n    assert (\n        "temporal change evidence alone is not proof"\n        in prompt\n    )\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found."
    )


def normalize_text(
    value: str,
) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def read_text(
    path: Path,
) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        normalize_text(
            value
        ),
        encoding="utf-8",
        newline="\n",
    )


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        normalize_text(
            value
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(
    lines: list[str],
    title: str,
) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def verify_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = sha256_text(
        read_text(
            path
        )
    )

    expected = EXPECTED_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the rolled-back v2 baseline. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing stale Change #002 v2.1 installation."
            )
        )


def retained_change_fact_count(
    source: str,
) -> int:
    tree = ast.parse(
        source
    )

    for node in ast.walk(
        tree
    ):
        if not (
            isinstance(
                node,
                ast.FunctionDef,
            )
            and node.name
            == "_normalize_kubernetes_change"
        ):
            continue

        for child in ast.walk(
            node
        ):
            if not isinstance(
                child,
                ast.Assign,
            ):
                continue

            if (
                len(
                    child.targets
                )
                != 1
                or not isinstance(
                    child.targets[
                        0
                    ],
                    ast.Name,
                )
                or child.targets[
                    0
                ].id
                != "facts"
                or not isinstance(
                    child.value,
                    ast.Dict,
                )
            ):
                continue

            return len(
                child.value.keys
            )

    raise RuntimeError(
        "Could not find retained Change facts dictionary"
    )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    change_tool_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "tools"
        / "kubernetes"
        / "change_tool.py"
    )

    probes_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "probes.py"
    )

    reasoner_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "reasoner.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_change_rollout_evidence.py"
    )

    sources = {
        change_tool_file: CHANGE_TOOL_SOURCE,
        probes_file: PROBES_SOURCE,
        reasoner_file: REASONER_SOURCE,
        test_file: TEST_SOURCE,
    }

    targets = list(
        sources.keys()
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Change Investigation Capability #002 v2.1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "v2 failure diagnosis:",
        "- v2 retained 36 top-level EvidenceItem facts but the domain contract allows at most 32",
        "- the new Reasoner wording removed an existing compatibility phrase used by the v1 regression test",
        "- the failed v2 installer rolled all modified files back before this installer is applied",
        "",
        "v2.1 correction:",
        "- EvidenceItem domain limits are NOT relaxed",
        "- rollout conditions are compacted into one bounded scalar rollout_condition_summary",
        "- high-signal rollout and event fields remain scalar and bounded",
        "- retained Change evidence is statically checked to stay <= 32 facts",
        "- existing causal-safety phrase 'not by itself proof' is preserved",
        "",
        "Capability retained:",
        "- Deployment rollout failure signals",
        "- ProgressDeadlineExceeded / FailedCreate summarization",
        "- generation-observed and rollout-complete state",
        "- bounded Pod / ReplicaSet / Deployment event enrichment",
        "- Event RBAC denial degrades event enrichment without discarding core Change evidence",
        "",
        "Safety unchanged:",
        "- GET-only Kubernetes access",
        "- no LLM-supplied API path / selector / verb",
        "- Change evidence alone remains insufficient causal proof",
        "- no Action / Approval / Verification authority",
        "",
        "Installer sends no external LLM/Kubernetes/Prometheus request.",
    ]

    try:
        section(
            report,
            "CURRENT HASH PREFLIGHT",
        )

        for relative in EXPECTED_HASHES:
            verify_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_HASHES[
                    relative
                ]
            )

        fact_count = retained_change_fact_count(
            PROBES_SOURCE
        )

        report.append(
            "retained_change_fact_count="
            + str(
                fact_count
            )
        )

        if fact_count > 32:
            raise RuntimeError(
                "Retained Change evidence exceeds EvidenceItem facts contract"
            )

        section(
            report,
            "BACKUP",
        )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
                        backup,
                    )
                )

                report.append(
                    "backup="
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )

        for path, source in sources.items():
            write_text(
                path,
                source,
            )

        syntax = run_command(
            root=root,
            name="Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Change #002 v2.1 syntax verification failed"
            )

        focused = run_command(
            root=root,
            name="Change rollout/events v2.1 focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_rollout_evidence.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_capability.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_probes.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Change rollout/events v2.1 focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Investigation / Change compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_final_synthesis_budget_discipline.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_consistency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_execution_resilience.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            compatibility,
        )

        if compatibility.returncode != 0:
            raise RuntimeError(
                "Change #002 v2.1 compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Evidence contract / rollout semantics preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "import ast; "
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/investigation/probes.py')"
                    ".read_text(encoding='utf-8'); "
                    "t=Path(r'services/agent_runtime/app/tools/kubernetes/change_tool.py')"
                    ".read_text(encoding='utf-8'); "
                    "r=Path(r'services/agent_runtime/app/investigation/reasoner.py')"
                    ".read_text(encoding='utf-8'); "
                    "tree=ast.parse(p); "
                    "counts=[]; "
                    "[counts.append(len(c.value.keys)) "
                    "for n in ast.walk(tree) "
                    "if isinstance(n,ast.FunctionDef) and n.name=='_normalize_kubernetes_change' "
                    "for c in ast.walk(n) "
                    "if isinstance(c,ast.Assign) and len(c.targets)==1 "
                    "and isinstance(c.targets[0],ast.Name) and c.targets[0].id=='facts' "
                    "and isinstance(c.value,ast.Dict)]; "
                    "print('retained_change_fact_count='+str(counts)); "
                    "print('rollout_condition_summary='+str('rollout_condition_summary' in p and 'rollout_condition_summary' in t)); "
                    "print('not_by_itself_proof='+str('not by itself proof' in r)); "
                    "assert counts and counts[0] <= 32; "
                    "assert 'rollout_condition_summary' in p and 'rollout_condition_summary' in t; "
                    "assert 'not by itself proof' in r"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Change #002 v2.1 evidence-contract preflight failed"
            )

        authority = run_command(
            root=root,
            name="Read-only authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "files=["
                    "Path(r'services/agent_runtime/app/tools/kubernetes/change_tool.py'),"
                    "Path(r'services/agent_runtime/app/investigation/probes.py'),"
                    "Path(r'services/agent_runtime/app/investigation/reasoner.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','VerificationRuntime',"
                    "'.post(','.patch(','.put(','.delete('] if x in s]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )

        add_command(
            report,
            authority,
        )

        if authority.returncode != 0:
            raise RuntimeError(
                "Change #002 v2.1 authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Change Investigation Capability #002 v2.1 is installed.",
                "",
                "Retained Change Evidence remains within the original EvidenceItem scalar/32-fact contract.",
                "Rollout failure and Kubernetes Event enrichment are enabled without expanding write authority.",
                "",
                "Next:",
                "add deterministic Change benchmark scenarios for failed rollout signals versus unrelated events before spending real model tokens.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "CHANGE INVESTIGATION CAPABILITY #002 V2.1 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
        )
        print("")
        print("Upload only:")
        print(after)

        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                )

            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )

                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK REMOVE FAILED "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Change Investigation Capability #002 v2.1 FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                    "",
                    "ROLLBACK",
                    "=" * 120,
                    *rollback,
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "CHANGE INVESTIGATION CAPABILITY #002 V2.1 FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Modified files were rolled back where possible."
        )
        print("")
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
