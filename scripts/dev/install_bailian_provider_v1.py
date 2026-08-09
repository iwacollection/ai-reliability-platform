from __future__ import annotations

import ast
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "bailian-provider-v1"
AFTER_NAME = "bailian_provider_v1_after.txt"
ERROR_NAME = "bailian_provider_v1_error.txt"

PROVIDER_SOURCE = 'import os\nfrom typing import Any\nfrom urllib.parse import urlparse\n\nimport httpx\n\nfrom services.agent_runtime.app.llm.base import (\n    BaseLLMProvider,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\n\n\nclass BailianCompatibleProvider(\n    BaseLLMProvider\n):\n    """\n    Alibaba Cloud Model Studio (Bailian) OpenAI-compatible provider.\n\n    Configuration:\n    - BAILIAN_BASE_URL\n    - DASHSCOPE_API_KEY\n    - BAILIAN_MODEL\n\n    BAILIAN_BASE_URL must already include /compatible-mode/v1.\n    Configuration is validated only when chat() is invoked so registry\n    construction never breaks the safe default mock development path.\n    """\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "bailian"\n\n    def __init__(\n        self,\n    ) -> None:\n        self.base_url = os.getenv(\n            "BAILIAN_BASE_URL",\n            "",\n        ).strip().rstrip("/")\n\n        self.api_key = os.getenv(\n            "DASHSCOPE_API_KEY",\n            "",\n        ).strip()\n\n        self.model = os.getenv(\n            "BAILIAN_MODEL",\n            "",\n        ).strip()\n\n    def validate_configuration(\n        self,\n    ) -> None:\n        if not self.base_url:\n            raise RuntimeError(\n                "BAILIAN_BASE_URL is not configured"\n            )\n\n        parsed = urlparse(\n            self.base_url\n        )\n\n        if (\n            parsed.scheme != "https"\n            or not parsed.netloc\n            or parsed.username is not None\n            or parsed.password is not None\n            or parsed.query\n            or parsed.fragment\n        ):\n            raise RuntimeError(\n                "BAILIAN_BASE_URL must be a clean HTTPS URL"\n            )\n\n        if not (\n            parsed.path.rstrip("/")\n            .endswith(\n                "/compatible-mode/v1"\n            )\n        ):\n            raise RuntimeError(\n                "BAILIAN_BASE_URL must end with /compatible-mode/v1"\n            )\n\n        if not self.api_key:\n            raise RuntimeError(\n                "DASHSCOPE_API_KEY is not configured"\n            )\n\n        if not self.model:\n            raise RuntimeError(\n                "BAILIAN_MODEL is not configured"\n            )\n\n    async def chat(\n        self,\n        request: ChatRequest,\n    ) -> ChatResponse:\n        self.validate_configuration()\n\n        messages: list[\n            dict[str, Any]\n        ] = []\n\n        if request.system_prompt:\n            messages.append(\n                {\n                    "role": "system",\n                    "content": request.system_prompt,\n                }\n            )\n\n        messages.append(\n            {\n                "role": "user",\n                "content": request.user_prompt,\n            }\n        )\n\n        payload = {\n            "model": self.model,\n            "messages": messages,\n            "temperature": request.temperature,\n        }\n\n        headers = {\n            "Content-Type": "application/json",\n            "Authorization": (\n                f"Bearer {self.api_key}"\n            ),\n        }\n\n        async with httpx.AsyncClient(\n            timeout=30,\n        ) as client:\n            response = await client.post(\n                f"{self.base_url}/chat/completions",\n                json=payload,\n                headers=headers,\n            )\n\n            response.raise_for_status()\n            data = response.json()\n\n        choices = data.get(\n            "choices"\n        )\n\n        if (\n            not isinstance(\n                choices,\n                list,\n            )\n            or not choices\n            or not isinstance(\n                choices[0],\n                dict,\n            )\n        ):\n            raise RuntimeError(\n                "Bailian response choices are invalid"\n            )\n\n        message = choices[0].get(\n            "message"\n        )\n\n        if not isinstance(\n            message,\n            dict,\n        ):\n            raise RuntimeError(\n                "Bailian response message is invalid"\n            )\n\n        content = message.get(\n            "content"\n        )\n\n        if (\n            not isinstance(\n                content,\n                str,\n            )\n            or not content.strip()\n        ):\n            raise RuntimeError(\n                "Bailian response content is invalid"\n            )\n\n        usage = data.get(\n            "usage",\n            {},\n        )\n\n        if not isinstance(\n            usage,\n            dict,\n        ):\n            usage = {}\n\n        return ChatResponse(\n            content=content,\n            model=data.get(\n                "model",\n                self.model,\n            ),\n            prompt_tokens=usage.get(\n                "prompt_tokens",\n                0,\n            ),\n            completion_tokens=usage.get(\n                "completion_tokens",\n                0,\n            ),\n            total_tokens=usage.get(\n                "total_tokens",\n                0,\n            ),\n        )\n\n\n__all__ = [\n    "BailianCompatibleProvider",\n]\n'
TEST_SOURCE = 'from types import SimpleNamespace\n\nimport httpx\nimport pytest\n\nimport services.agent_runtime.app.evaluation.real_incident.llm_run as run_module\nimport services.agent_runtime.app.llm.provider_factory as provider_factory_module\n\nfrom services.agent_runtime.app.evaluation.real_incident.llm_run import (\n    create_historical_llm_runtime,\n)\nfrom services.agent_runtime.app.llm.factory import (\n    create_llm_registry,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n)\nfrom services.agent_runtime.app.llm.provider_factory import (\n    create_llm_provider,\n)\nfrom services.agent_runtime.app.llm.providers.bailian_compatible import (\n    BailianCompatibleProvider,\n)\n\n\ndef mock_settings():\n    return SimpleNamespace(\n        llm=SimpleNamespace(\n            provider="mock"\n        )\n    )\n\n\ndef configure_bailian(\n    monkeypatch,\n):\n    monkeypatch.setenv(\n        "BAILIAN_BASE_URL",\n        (\n            "https://llm-example."\n            "cn-beijing.maas.aliyuncs.com"\n            "/compatible-mode/v1"\n        ),\n    )\n\n    monkeypatch.setenv(\n        "DASHSCOPE_API_KEY",\n        "unit-test-secret",\n    )\n\n    monkeypatch.setenv(\n        "BAILIAN_MODEL",\n        "qwen-plus",\n    )\n\n\ndef test_bailian_provider_is_registered():\n    registry = create_llm_registry()\n\n    assert isinstance(\n        registry.get(\n            "bailian"\n        ),\n        BailianCompatibleProvider,\n    )\n\n\ndef test_explicit_bailian_override_does_not_mutate_mock_default(\n    monkeypatch,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    monkeypatch.setattr(\n        provider_factory_module,\n        "get_settings",\n        mock_settings,\n    )\n\n    provider = create_llm_provider(\n        provider_name="bailian"\n    )\n\n    assert isinstance(\n        provider,\n        BailianCompatibleProvider,\n    )\n\n    assert (\n        mock_settings().llm.provider\n        == "mock"\n    )\n\n\n@pytest.mark.parametrize(\n    (\n        "missing_env",\n        "message",\n    ),\n    [\n        (\n            "BAILIAN_BASE_URL",\n            "BAILIAN_BASE_URL is not configured",\n        ),\n        (\n            "DASHSCOPE_API_KEY",\n            "DASHSCOPE_API_KEY is not configured",\n        ),\n        (\n            "BAILIAN_MODEL",\n            "BAILIAN_MODEL is not configured",\n        ),\n    ],\n)\n@pytest.mark.asyncio\nasync def test_bailian_missing_configuration_fails_before_network(\n    monkeypatch,\n    missing_env,\n    message,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    monkeypatch.delenv(\n        missing_env,\n        raising=False,\n    )\n\n    network_calls = 0\n\n    class ForbiddenClient:\n        async def __aenter__(\n            self,\n        ):\n            return self\n\n        async def __aexit__(\n            self,\n            exc_type,\n            exc,\n            tb,\n        ):\n            return False\n\n        async def post(\n            self,\n            *args,\n            **kwargs,\n        ):\n            nonlocal network_calls\n            network_calls += 1\n            raise AssertionError(\n                "Network must not be reached with invalid config"\n            )\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        lambda *args, **kwargs: ForbiddenClient(),\n    )\n\n    provider = BailianCompatibleProvider()\n\n    with pytest.raises(\n        RuntimeError,\n        match=message,\n    ):\n        await provider.chat(\n            ChatRequest(\n                system_prompt="system",\n                user_prompt="user",\n                temperature=0.0,\n            )\n        )\n\n    assert network_calls == 0\n\n\ndef test_bailian_base_url_must_use_compatible_mode(\n    monkeypatch,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    monkeypatch.setenv(\n        "BAILIAN_BASE_URL",\n        "https://example.aliyuncs.com/api/v1",\n    )\n\n    provider = BailianCompatibleProvider()\n\n    with pytest.raises(\n        RuntimeError,\n        match="compatible-mode/v1",\n    ):\n        provider.validate_configuration()\n\n\n@pytest.mark.asyncio\nasync def test_bailian_chat_uses_openai_compatible_contract(\n    monkeypatch,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    captured = {}\n\n    class FakeResponse:\n        def raise_for_status(\n            self,\n        ):\n            return None\n\n        def json(\n            self,\n        ):\n            return {\n                "id": "chatcmpl-unit",\n                "model": "qwen-plus",\n                "choices": [\n                    {\n                        "message": {\n                            "role": "assistant",\n                            "content": \'{"stop":false}\',\n                        }\n                    }\n                ],\n                "usage": {\n                    "prompt_tokens": 11,\n                    "completion_tokens": 3,\n                    "total_tokens": 14,\n                },\n            }\n\n    class FakeClient:\n        def __init__(\n            self,\n            *args,\n            **kwargs,\n        ):\n            captured[\n                "client_kwargs"\n            ] = kwargs\n\n        async def __aenter__(\n            self,\n        ):\n            return self\n\n        async def __aexit__(\n            self,\n            exc_type,\n            exc,\n            tb,\n        ):\n            return False\n\n        async def post(\n            self,\n            url,\n            *,\n            json,\n            headers,\n        ):\n            captured[\n                "url"\n            ] = url\n            captured[\n                "json"\n            ] = json\n            captured[\n                "headers"\n            ] = headers\n            return FakeResponse()\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        FakeClient,\n    )\n\n    provider = BailianCompatibleProvider()\n\n    response = await provider.chat(\n        ChatRequest(\n            system_prompt="You are an SRE.",\n            user_prompt="Investigate this incident.",\n            temperature=0.0,\n        )\n    )\n\n    assert captured[\n        "url"\n    ].endswith(\n        "/compatible-mode/v1/chat/completions"\n    )\n\n    assert captured[\n        "json"\n    ] == {\n        "model": "qwen-plus",\n        "messages": [\n            {\n                "role": "system",\n                "content": "You are an SRE.",\n            },\n            {\n                "role": "user",\n                "content": "Investigate this incident.",\n            },\n        ],\n        "temperature": 0.0,\n    }\n\n    assert captured[\n        "headers"\n    ][\n        "Authorization"\n    ] == "Bearer unit-test-secret"\n\n    assert (\n        response.content\n        == \'{"stop":false}\'\n    )\n\n    assert (\n        response.model\n        == "qwen-plus"\n    )\n\n    assert (\n        response.total_tokens\n        == 14\n    )\n\n\ndef test_historical_runtime_accepts_bailian_override(\n    monkeypatch,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    captured = []\n\n    class NoNetworkGateway:\n        async def chat(\n            self,\n            request,\n        ):\n            raise AssertionError(\n                "Composition test must not make a real request"\n            )\n\n    def fake_gateway_factory(\n        provider_name=None,\n    ):\n        captured.append(\n            provider_name\n        )\n        return NoNetworkGateway()\n\n    monkeypatch.setattr(\n        run_module,\n        "create_llm_gateway",\n        fake_gateway_factory,\n    )\n\n    runtime = create_historical_llm_runtime(\n        provider_name="bailian"\n    )\n\n    assert captured == [\n        "bailian"\n    ]\n\n    assert (\n        runtime.historical_llm_provider_name\n        == "bailian"\n    )\n\n\ndef test_registry_creation_does_not_require_bailian_secrets(\n    monkeypatch,\n):\n    monkeypatch.delenv(\n        "BAILIAN_BASE_URL",\n        raising=False,\n    )\n    monkeypatch.delenv(\n        "DASHSCOPE_API_KEY",\n        raising=False,\n    )\n    monkeypatch.delenv(\n        "BAILIAN_MODEL",\n        raising=False,\n    )\n\n    registry = create_llm_registry()\n\n    assert (\n        registry.get(\n            "bailian"\n        ).name\n        == "bailian"\n    )\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. Run from inside ai-reliability-platform."
    )


def read_text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        ),
        encoding="utf-8",
        newline="\n",
    )


def backup_file(path: Path) -> Path:
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


def section(lines: list[str], title: str) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(lines: list[str], result: CommandResult) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(result.command),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip() or "<EMPTY>",
        ]
    )


def verify_app_yaml_mock(path: Path) -> None:
    text = read_text(
        path
    )

    start = text.find(
        "llm:"
    )

    if start < 0:
        raise RuntimeError(
            "configs/app.yaml has no llm section"
        )

    provider = None

    for line in text[
        start
        + len(
            "llm:"
        ) :
    ].splitlines():

        stripped = line.strip()

        if stripped and not line.startswith(
            (
                " ",
                "\t",
            )
        ):
            break

        if stripped.startswith(
            "provider:"
        ):
            provider = (
                stripped
                .split(
                    ":",
                    1,
                )[1]
                .strip()
            )
            break

    if provider != "mock":
        raise RuntimeError(
            "Safety invariant failed: configs/app.yaml must remain provider: mock"
        )


def add_bailian_to_factory(path: Path) -> None:
    text = read_text(
        path
    )

    tree = ast.parse(
        text
    )

    functions = [
        node
        for node in tree.body
        if (
            isinstance(
                node,
                ast.FunctionDef,
            )
            and node.name == "create_llm_registry"
        )
    ]

    if len(functions) != 1:
        raise RuntimeError(
            "factory.py must contain exactly one create_llm_registry"
        )

    import_exists = any(
        (
            isinstance(
                node,
                ast.ImportFrom,
            )
            and node.module
            == "services.agent_runtime.app.llm.providers.bailian_compatible"
        )
        for node in tree.body
    )

    function = functions[0]
    lines = text.splitlines(
        keepends=True
    )

    if not import_exists:
        import_block = (
            "from services.agent_runtime.app.llm."
            "providers.bailian_compatible import (\n"
            "    BailianCompatibleProvider,\n"
            ")\n\n"
        )

        lines.insert(
            function.lineno - 1,
            import_block,
        )

        text = "".join(
            lines
        )

        tree = ast.parse(
            text
        )

        function = next(
            node
            for node in tree.body
            if (
                isinstance(
                    node,
                    ast.FunctionDef,
                )
                and node.name == "create_llm_registry"
            )
        )

    function_source = ast.get_source_segment(
        text,
        function,
    )

    if function_source is None:
        raise RuntimeError(
            "Could not read create_llm_registry source"
        )

    if "BailianCompatibleProvider" in function_source:
        write_text(
            path,
            text,
        )
        return

    returns = [
        node
        for node in function.body
        if isinstance(
            node,
            ast.Return,
        )
    ]

    if len(returns) != 1:
        raise RuntimeError(
            "create_llm_registry must contain one top-level return"
        )

    lines = text.splitlines(
        keepends=True
    )

    registration = (
        "    registry.register(\n"
        "        BailianCompatibleProvider(),\n"
        "    )\n\n"
    )

    lines.insert(
        returns[0].lineno - 1,
        registration,
    )

    updated = "".join(
        lines
    )

    ast.parse(
        updated
    )

    write_text(
        path,
        updated,
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

    provider_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "providers"
        / "bailian_compatible.py"
    )

    factory_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "factory.py"
    )

    provider_factory_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "provider_factory.py"
    )

    gateway_factory_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "gateway"
        / "factory.py"
    )

    llm_run_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "real_incident"
        / "llm_run.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_bailian_provider.py"
    )

    app_yaml = (
        root
        / "configs"
        / "app.yaml"
    )

    for required in (
        factory_file,
        provider_factory_file,
        gateway_factory_file,
        llm_run_file,
        app_yaml,
    ):
        if not required.exists():
            raise RuntimeError(
                f"Required file missing: {required}"
            )

    report = [
        "Bailian Provider v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Goal:",
        "- register provider name: bailian",
        "- preserve configs/app.yaml provider=mock",
        "- reuse Shared LLM Gateway and Investigation Reasoner",
        "- no external Bailian request during install",
        "- focused tests included",
    ]

    backups = []
    provider_preexisted = provider_file.exists()
    test_preexisted = test_file.exists()

    try:
        verify_app_yaml_mock(
            app_yaml
        )

        section(
            report,
            "PRE-INSTALL SAFETY",
        )

        report.append(
            "configs/app.yaml provider=mock"
        )

        factory_backup = backup_file(
            factory_file
        )

        backups.append(
            (
                factory_file,
                factory_backup,
            )
        )

        report.append(
            "backup="
            + str(
                factory_backup.relative_to(
                    root
                )
            )
        )

        if provider_preexisted:
            backups.append(
                (
                    provider_file,
                    backup_file(
                        provider_file
                    ),
                )
            )

        if test_preexisted:
            backups.append(
                (
                    test_file,
                    backup_file(
                        test_file
                    ),
                )
            )

        write_text(
            provider_file,
            PROVIDER_SOURCE,
        )

        add_bailian_to_factory(
            factory_file
        )

        write_text(
            test_file,
            TEST_SOURCE,
        )

        verify_app_yaml_mock(
            app_yaml
        )

        syntax_targets = [
            provider_file,
            factory_file,
            provider_factory_file,
            gateway_factory_file,
            llm_run_file,
            test_file,
        ]

        syntax = run_command(
            root,
            "Python syntax",
            [
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
                    for path in syntax_targets
                ],
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        registry = run_command(
            root,
            "Bailian registry preflight",
            [
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.llm.factory "
                    "import create_llm_registry; "
                    "r=create_llm_registry(); "
                    "print('providers=' + ','.join("
                    "sorted(p.name for p in r.list()))); "
                    "print('bailian=' + r.get('bailian').name)"
                ),
            ],
        )

        add_command(
            report,
            registry,
        )

        if registry.returncode != 0:
            raise RuntimeError(
                "Bailian registry preflight failed"
            )

        tests = run_command(
            root,
            "Bailian + Historical LLM focused tests",
            [
                "uv",
                "run",
                "pytest",
                "services/agent_runtime/tests/test_bailian_provider.py",
                "services/agent_runtime/tests/test_llm_provider_override.py",
                "services/agent_runtime/tests/test_real_llm_historical_run.py",
                "services/agent_runtime/tests/test_historical_incident_investigation_runner.py",
                "services/agent_runtime/tests/test_investigation_reasoner.py",
                "services/agent_runtime/tests/test_llm_config.py",
                "-q",
            ],
        )

        add_command(
            report,
            tests,
        )

        if tests.returncode != 0:
            raise RuntimeError(
                "Focused tests failed"
            )

        env_check = run_command(
            root,
            "Bailian environment presence",
            [
                "uv",
                "run",
                "python",
                "-c",
                (
                    "import os; "
                    "print('BAILIAN_BASE_URL_PRESENT=' + "
                    "str(bool(os.getenv('BAILIAN_BASE_URL')))); "
                    "print('DASHSCOPE_API_KEY_PRESENT=' + "
                    "str(bool(os.getenv('DASHSCOPE_API_KEY')))); "
                    "print('BAILIAN_MODEL_PRESENT=' + "
                    "str(bool(os.getenv('BAILIAN_MODEL'))))"
                ),
            ],
        )

        add_command(
            report,
            env_check,
        )

        diff = run_command(
            root,
            "Git diff",
            [
                "git",
                "diff",
                "--",
                str(
                    provider_file.relative_to(
                        root
                    )
                ),
                str(
                    factory_file.relative_to(
                        root
                    )
                ),
                str(
                    test_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            diff,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Safe default:",
                "configs/app.yaml -> provider: mock",
                "",
                "Real historical Agent override:",
                "--provider bailian",
                "",
                "Required environment:",
                "BAILIAN_BASE_URL",
                "DASHSCOPE_API_KEY",
                "BAILIAN_MODEL",
                "",
                "No external Bailian request was sent.",
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
        print("BAILIAN PROVIDER V1 PASSED")
        print("=" * 72)
        print("")
        print("Safe default preserved: provider=mock")
        print("Real historical override: --provider bailian")
        print("")
        print("Upload:")
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

        if (
            not provider_preexisted
            and provider_file.exists()
        ):
            try:
                provider_file.unlink()

                rollback.append(
                    "REMOVED newly-created "
                    + str(
                        provider_file.relative_to(
                            root
                        )
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED removing provider: "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        if (
            not test_preexisted
            and test_file.exists()
        ):
            try:
                test_file.unlink()

                rollback.append(
                    "REMOVED newly-created "
                    + str(
                        test_file.relative_to(
                            root
                        )
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED removing test: "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        error_lines = [
            "Bailian Provider v1 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            f"{type(exc).__name__}: {exc}",
            "",
            "Traceback:",
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

        write_text(
            error,
            "\n".join(
                error_lines
            )
            + "\n",
        )

        print("=" * 72)
        print("BAILIAN PROVIDER V1 FAILED")
        print("=" * 72)
        print("")
        print("Modified files were rolled back where possible.")
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
