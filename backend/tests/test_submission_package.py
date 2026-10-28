"""Exercise the package verifier as a CLI without a daemon, build, or credentials."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_submission_package.py"
SPEC = importlib.util.spec_from_file_location("submission_package", SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "package"
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "scripts").mkdir()
    for name in ("README.md", "docker-compose.yml", ".env.example"):
        (root / name).write_text("", encoding="utf-8")
    (root / ".env").write_text(
        "LLM_API_KEY=PRIVATE-TEST-ONLY\nTRAP=${SUBMISSION_PRIVATE_FILE_MUST_NOT_BE_READ:?private-env-trap}\n",
        encoding="utf-8",
    )
    (root / "backend/uv.lock").write_text('source = { registry = "https://pypi.org/simple" }\n', encoding="utf-8")
    (root / "frontend/package.json").write_text("{}", encoding="utf-8")
    for directory, image, user in (
        ("backend", "python:3.12.14-slim", "1000"),
        ("frontend", "node:22.21.0-alpine", "node"),
    ):
        (root / directory / "Dockerfile").write_text(
            f"FROM {image} AS build\nWORKDIR /app\n"
            f"FROM {image} AS runtime\nCOPY --from=build /app /app\n"
            f'USER {user}\nHEALTHCHECK CMD ["true"]\n',
            encoding="utf-8",
        )
    config = {
        "services": {
            "agent": {
                "build": {"context": str(root / "backend")},
                "platform": "linux/amd64",
                "healthcheck": {"test": ["CMD", "true"]},
                "cpus": 1,
                "mem_limit": 536870912,
                "environment": {
                    "LLM_PROVIDER": "fake",
                    "TOOLS_MODE": "real",
                    "FLIGHTS_MODE": "demo",
                    "TRAINS_MODE": "synthetic",
                    "CACHE_MODE": "replay",
                },
            },
            "app": {
                "build": {"context": str(root / "frontend")},
                "platform": "linux/amd64",
                "healthcheck": {"test": ["CMD", "true"]},
                "cpus": 0.5,
                "mem_limit": 134217728,
                "ports": [{"target": 8080, "published": "8080"}],
                "environment": {"PORT": "8080"},
                "depends_on": {"agent": {"condition": "service_healthy"}},
            },
        }
    }
    shutil.copyfile(SCRIPT, root / "scripts/check_submission_package.py")
    return root, config


def run_cli(root, config):
    binary = root / "bin"
    binary.mkdir()
    docker = binary / "docker"
    # A fake renderer rejects leaks and mutations before returning a resolved graph.
    docker.write_text(
        "#!/usr/bin/env python3\nimport os,sys,json\n"
        "assert 'LLM_API_KEY' not in os.environ\n"
        "assert sys.argv[1] == 'compose'\n"
        "assert sys.argv[sys.argv.index('--env-file')+1].endswith('/.env.example')\n"
        "assert sys.argv[-4:] == ['config','--no-env-resolution','--format','json']\n"
        "assert not any(x in sys.argv for x in ['up','build','pull','push'])\n"
        f"print(json.dumps({config!r}))\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ.get("PATH", ""), LLM_API_KEY="PRIVATE-TEST-ONLY")
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_submission_package.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "PRIVATE-TEST-ONLY" not in result.stdout + result.stderr
    return result, json.loads(result.stdout)


def test_cli_renders_only_example_configuration_and_does_not_claim_handoff(package):
    root, config = package
    result, report = run_cli(root, config)
    assert result.returncode == 0, report
    assert report["status"] == "passed"
    assert report["handoff_certified"] is False
    assert all(row["passed"] for row in report["checks"])


def test_old_dev_topology_and_root_runtime_cannot_pass_as_the_default_submission(package):
    root, config = package
    config["services"]["app"]["ports"] = [{"target": 5173, "published": "8080"}]
    config["services"]["app"]["environment"]["PORT"] = "5173"
    config["services"]["agent"]["ports"] = [{"target": 8000, "published": "8000"}]
    dockerfile = root / "backend/Dockerfile"
    dockerfile.write_text(dockerfile.read_text().replace("USER 1000", "USER root"), encoding="utf-8")
    result, report = run_cli(root, config)
    assert result.returncode == 2
    failures = {row["name"] for row in report["checks"] if not row["passed"]}
    assert {"single_browser_entrypoint", "agent:runtime_image"} <= failures


def test_private_registry_or_example_credentials_are_reported_without_values(package):
    root, config = package
    config["services"]["agent"]["environment"]["LLM_API_KEY"] = "PRIVATE-TEST-ONLY"
    (root / "backend/uv.lock").write_text(
        'source = { registry = "https://private.invalid/simple" }\n', encoding="utf-8"
    )
    result, report = run_cli(root, config)
    assert result.returncode == 2
    failures = {row["name"] for row in report["checks"] if not row["passed"]}
    assert {"example_credentials", "dependency_registry"} <= failures


def test_local_private_env_is_never_resolved_and_example_itself_is_audited(package):
    root, config = package
    config["services"]["agent"]["env_file"] = [{"path": str(root / ".env"), "required": False}]
    (root / ".env.example").write_text("LLM_API_KEY=PRIVATE-TEST-ONLY\n", encoding="utf-8")
    result, report = run_cli(root, config)
    assert result.returncode == 2
    failures = {row["name"] for row in report["checks"] if not row["passed"]}
    assert "example_file_credentials" in failures


def test_copy_from_an_external_image_is_part_of_the_active_dependency_graph(package):
    root, config = package
    path = root / "backend/Dockerfile"
    path.write_text(
        path.read_text().replace(
            "WORKDIR /app", "COPY --from=ghcr.io/private-team/tool:1 /bin/tool /bin/tool\nWORKDIR /app"
        ),
        encoding="utf-8",
    )
    result, report = run_cli(root, config)
    assert result.returncode == 2
    assert any(row["name"] == "agent:runtime_image" and not row["passed"] for row in report["checks"])


@pytest.mark.parametrize("value", [[], None, {"status": "starting"}])
def test_http_probe_does_not_accept_a_200_with_an_invalid_health_payload(monkeypatch, value):
    class Response:
        status = 200
        headers = {"content-type": "text/html"}

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return self.body

    class Opener:
        def open(self, url, **_kwargs):
            return Response(json.dumps(value).encode() if url.endswith("/health") else b"<!DOCTYPE html><html></html>")

    monkeypatch.setattr(CHECKER.urllib.request, "build_opener", lambda *_args: Opener())
    checks = CHECKER.probe_http("http://localhost:8080")
    assert checks[0]["passed"] is True
    assert checks[1]["passed"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:5173",
        "http://owner:secret@localhost:8080",
        "http://localhost:8080/?token=private",
    ],
)
def test_http_probe_never_sends_a_request_outside_the_default_local_entrypoint(url):
    with pytest.raises(ValueError):
        CHECKER.probe_http(url)
