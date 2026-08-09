from __future__ import annotations

import os
import socket
import ssl
import sys
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx


AFTER_NAME = "openai_network_diagnostic_v1_after.txt"
ERROR_NAME = "openai_network_diagnostic_v1_error.txt"

HOST = "api.openai.com"
PORT = 443
BASE_URL = "https://api.openai.com/v1"


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. Run this script from inside "
        "ai-reliability-platform."
    )


def write_text(path: Path, text: str) -> None:
    path.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
        newline="\n",
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


def redact_proxy(value: str | None) -> str:
    if not value:
        return "<NOT_SET>"

    try:
        parsed = urlsplit(value)

        if parsed.scheme and parsed.hostname:
            host = parsed.hostname
            port = (
                f":{parsed.port}"
                if parsed.port is not None
                else ""
            )

            redacted_netloc = host + port

            if parsed.username is not None:
                redacted_netloc = (
                    "<REDACTED_USER>:<REDACTED_PASSWORD>@"
                    + redacted_netloc
                )

            return urlunsplit(
                (
                    parsed.scheme,
                    redacted_netloc,
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )

    except Exception:
        pass

    return "<SET_BUT_REDACTED>"


def format_exception(exc: BaseException) -> list[str]:
    lines = [
        f"type={type(exc).__name__}",
        f"message={str(exc)!r}",
    ]

    cause = exc.__cause__

    depth = 0

    while cause is not None and depth < 8:
        lines.append(
            f"cause[{depth}]={type(cause).__name__}: {str(cause)!r}"
        )
        cause = cause.__cause__
        depth += 1

    context = exc.__context__

    depth = 0

    while context is not None and depth < 8:
        lines.append(
            f"context[{depth}]={type(context).__name__}: {str(context)!r}"
        )
        context = context.__context__
        depth += 1

    return lines


def dns_test(lines: list[str]) -> list[tuple]:
    section(
        lines,
        "DNS RESOLUTION",
    )

    try:
        results = socket.getaddrinfo(
            HOST,
            PORT,
            type=socket.SOCK_STREAM,
        )

        unique = []

        seen = set()

        for family, socktype, proto, canonname, sockaddr in results:
            key = (
                family,
                sockaddr,
            )

            if key in seen:
                continue

            seen.add(key)

            unique.append(
                (
                    family,
                    socktype,
                    proto,
                    canonname,
                    sockaddr,
                )
            )

        lines.append(
            f"result_count={len(unique)}"
        )

        for item in unique:
            family = item[0]
            sockaddr = item[4]

            family_name = {
                socket.AF_INET: "IPv4",
                socket.AF_INET6: "IPv6",
            }.get(
                family,
                str(family),
            )

            lines.append(
                f"{family_name}={sockaddr}"
            )

        lines.append(
            "DNS=PASSED"
        )

        return unique

    except Exception as exc:
        lines.append(
            "DNS=FAILED"
        )

        lines.extend(
            format_exception(
                exc
            )
        )

        return []


def tcp_test(
    lines: list[str],
    addresses: list[tuple],
) -> None:
    section(
        lines,
        "TCP CONNECTIVITY",
    )

    if not addresses:
        lines.append(
            "SKIPPED: no resolved addresses"
        )
        return

    attempts = 0
    successes = 0

    for family, socktype, proto, _canonname, sockaddr in addresses[:8]:
        attempts += 1

        sock = socket.socket(
            family,
            socktype,
            proto,
        )

        sock.settimeout(
            5.0
        )

        try:
            sock.connect(
                sockaddr
            )

            successes += 1

            lines.append(
                f"PASS {sockaddr}"
            )

        except Exception as exc:
            lines.append(
                f"FAIL {sockaddr}"
            )
            lines.extend(
                "  " + item
                for item in format_exception(
                    exc
                )
            )

        finally:
            sock.close()

    lines.append("")
    lines.append(
        f"attempts={attempts}"
    )
    lines.append(
        f"successes={successes}"
    )

    if successes:
        lines.append(
            "TCP=PASSED"
        )
    else:
        lines.append(
            "TCP=FAILED"
        )


def tls_test(
    lines: list[str],
) -> None:
    section(
        lines,
        "TLS HANDSHAKE",
    )

    context = ssl.create_default_context()

    try:
        with socket.create_connection(
            (
                HOST,
                PORT,
            ),
            timeout=8.0,
        ) as raw_socket:

            with context.wrap_socket(
                raw_socket,
                server_hostname=HOST,
            ) as tls_socket:

                cert = tls_socket.getpeercert()

                lines.append(
                    f"protocol={tls_socket.version()}"
                )

                cipher = tls_socket.cipher()

                if cipher:
                    lines.append(
                        f"cipher={cipher[0]}"
                    )

                subject = cert.get(
                    "subject"
                )

                issuer = cert.get(
                    "issuer"
                )

                lines.append(
                    f"subject={subject}"
                )

                lines.append(
                    f"issuer={issuer}"
                )

                lines.append(
                    "TLS=PASSED"
                )

    except Exception as exc:
        lines.append(
            "TLS=FAILED"
        )

        lines.extend(
            format_exception(
                exc
            )
        )


def httpx_test(
    lines: list[str],
    *,
    trust_env: bool,
) -> bool:
    name = (
        "HTTPX WITH ENVIRONMENT PROXY"
        if trust_env
        else "HTTPX DIRECT / IGNORE ENVIRONMENT PROXY"
    )

    section(
        lines,
        name,
    )

    url = (
        BASE_URL
        + "/models"
    )

    try:
        with httpx.Client(
            timeout=10.0,
            trust_env=trust_env,
            follow_redirects=False,
        ) as client:

            response = client.get(
                url,
                headers={
                    # Deliberately no Authorization header.
                    # Any HTTP response proves DNS/TCP/TLS/HTTP reached OpenAI.
                    "User-Agent": (
                        "ai-reliability-platform-network-diagnostic/1"
                    ),
                },
            )

        lines.append(
            f"status_code={response.status_code}"
        )

        lines.append(
            "HTTP_RESPONSE=RECEIVED"
        )

        lines.append(
            "Note: 401/403 is acceptable here because "
            "this diagnostic intentionally sends no API key."
        )

        request_id = (
            response.headers.get(
                "x-request-id"
            )
            or response.headers.get(
                "request-id"
            )
        )

        lines.append(
            f"request_id={request_id}"
        )

        return True

    except Exception as exc:
        lines.append(
            "HTTP_RESPONSE=NOT_RECEIVED"
        )

        lines.extend(
            format_exception(
                exc
            )
        )

        return False


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = (
        root
        / AFTER_NAME
    )

    error = (
        root
        / ERROR_NAME
    )

    for path in (
        after,
        error,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report = [
        "OpenAI Network Diagnostic v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- diagnose httpx.ConnectError before any OpenAI HTTP response",
        "- test DNS",
        "- test TCP port 443",
        "- test TLS handshake",
        "- compare httpx trust_env=True vs trust_env=False",
        "",
        "Safety:",
        "- does NOT send OPENAI_API_KEY",
        "- does NOT call a model",
        "- does NOT consume model tokens",
        "- does NOT modify project configuration",
        "- does NOT call Kubernetes / Action / Approval / Verification",
    ]

    try:
        section(
            report,
            "PROCESS / PROXY ENVIRONMENT",
        )

        report.append(
            f"python={sys.executable}"
        )

        report.append(
            f"python_version={sys.version}"
        )

        report.append(
            f"OPENAI_BASE_URL={os.getenv('OPENAI_BASE_URL', BASE_URL)}"
        )

        report.append(
            f"OPENAI_MODEL={os.getenv('OPENAI_MODEL', '<NOT_SET>')}"
        )

        report.append(
            "OPENAI_API_KEY_PRESENT="
            + str(
                bool(
                    os.getenv(
                        "OPENAI_API_KEY",
                        "",
                    )
                )
            )
        )

        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        ):
            report.append(
                f"{name}="
                + redact_proxy(
                    os.getenv(
                        name
                    )
                )
            )

        addresses = dns_test(
            report
        )

        tcp_test(
            report,
            addresses,
        )

        tls_test(
            report
        )

        with_env = httpx_test(
            report,
            trust_env=True,
        )

        without_env = httpx_test(
            report,
            trust_env=False,
        )

        section(
            report,
            "INTERPRETATION",
        )

        if with_env and without_env:
            interpretation = (
                "Both proxy-aware and direct httpx paths reached an HTTP "
                "response. The earlier ConnectError may be intermittent or "
                "specific to the async/provider path."
            )

        elif with_env and not without_env:
            interpretation = (
                "Proxy-aware httpx succeeds while direct httpx fails. "
                "This environment likely requires an HTTP/HTTPS proxy."
            )

        elif not with_env and without_env:
            interpretation = (
                "Direct httpx succeeds while proxy-aware httpx fails. "
                "A proxy environment variable is likely incorrect or "
                "unreachable. httpx uses environment proxy settings by default."
            )

        else:
            interpretation = (
                "Neither httpx path received an HTTP response. "
                "Use the DNS/TCP/TLS sections to identify the failing layer."
            )

        report.append(
            interpretation
        )

        section(
            report,
            "RESULT",
        )

        if with_env or without_env:
            report.append(
                "NETWORK_DIAGNOSTIC=HTTP_REACHABLE"
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
                "OPENAI NETWORK DIAGNOSTIC COMPLETED"
            )
            print("=" * 72)
            print("")
            print(
                "At least one HTTP path reached api.openai.com."
            )
            print("")
            print("Upload:")
            print(after)

            return 0

        report.append(
            "NETWORK_DIAGNOSTIC=HTTP_UNREACHABLE"
        )

        write_text(
            error,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "OPENAI NETWORK DIAGNOSTIC FOUND A CONNECTIVITY FAILURE"
        )
        print("=" * 72)
        print("")
        print("Upload:")
        print(error)

        return 1

    except Exception as exc:
        error_lines = [
            "OpenAI Network Diagnostic v1 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            f"{type(exc).__name__}: {exc}",
            "",
            "Traceback:",
            traceback.format_exc(),
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
        print(
            "OPENAI NETWORK DIAGNOSTIC SCRIPT FAILED"
        )
        print("=" * 72)
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
