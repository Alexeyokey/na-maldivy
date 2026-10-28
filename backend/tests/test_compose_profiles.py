"""Verify legacy launch contracts against the submission app:8080 stack."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def compose_config(*files: str, **settings: str) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is needed to validate the merged profiles")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=10)
    if version.returncode:
        pytest.skip("Docker Compose plugin is not installed")
    env = {
        **os.environ,
        "APP_PORT": "8080",
        "WEB_PORT": "5173",
        "AGENT_PORT": "18083",
        "NA_MALDIVY_RELEASE": "contract-test",
        "AVIASALES_API_TOKEN": "synthetic-compose-canary",
        "YANDEX_RASP_API_KEY": "synthetic-compose-canary",
        "LLM_API_KEY": "synthetic-compose-canary",
        **settings,
    }
    command = ["docker", "compose", "--env-file", "/dev/null", "--project-name", "na-maldivy-profile-contract"]
    for filename in files:
        command.extend(["-f", str(ROOT / filename)])
    command.extend(["config", "--no-env-resolution", "--format", "json"])
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, "Compose profile must validate without reading service env files"
    return json.loads(result.stdout)


@pytest.mark.parametrize("app_port,web_port", [("8080", "5173"), ("28080", "25173")])
def test_offline_overlay_replaces_default_app_port_and_keeps_backend_private(app_port: str, web_port: str) -> None:
    config = compose_config("docker-compose.yml", "docker-compose.demo.yml", APP_PORT=app_port, WEB_PORT=web_port)
    services = config["services"]
    assert set(services) == {"agent", "gateway", "app"}
    assert all(service["platform"] == "linux/amd64" for service in services.values())
    app = services["app"]
    assert app["environment"]["PORT"] == "5173"
    assert [(port["host_ip"], port["published"], port["target"]) for port in app["ports"]] == [
        ("127.0.0.1", web_port, 5173)
    ]
    assert "5173/health" in " ".join(app["healthcheck"]["test"])
    assert not services["agent"].get("ports")
    assert not services["agent"].get("env_file")
    assert set(services["agent"]["networks"]) == {"offline-demo"}
    assert config["networks"]["offline-demo"]["internal"] is True
    assert services["agent"]["environment"]["LLM_PROVIDER"] == "fake"
    assert services["agent"]["environment"]["CACHE_MODE"] == "replay"
    for key in ["LLM_API_KEY", "AVIASALES_API_TOKEN", "YANDEX_RASP_API_KEY"]:
        assert services["agent"]["environment"][key] == ""


def test_standalone_offline_package_keeps_its_existing_web_image_and_port() -> None:
    services = compose_config("docker-compose.release.yml")["services"]
    assert set(services) == {"agent", "gateway", "web"}
    assert services["web"]["image"] == "na-maldivy-demo-web:20261028-full-ui"
    assert services["web"]["environment"]["PORT"] == "5173"
    assert services["web"]["ports"][0]["target"] == 5173
    assert services["web"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert not services["agent"].get("ports")
    for service in services.values():
        assert "build" not in service
        assert not service.get("env_file")
        assert service["pull_policy"] == "never"


def test_vps_web_explicitly_preserves_5173_after_the_frontend_image_default_changes() -> None:
    services = compose_config("deploy/vps/compose.yml")["services"]
    assert services["web"]["environment"]["PORT"] == "5173"
    assert "5173/health" in " ".join(services["web"]["healthcheck"]["test"])
    assert not services["web"].get("ports")
    assert not services["agent"].get("ports")
    gateway_base = next(
        line.split()[1]
        for line in (ROOT / "backend/Dockerfile.gateway").read_text().splitlines()
        if line.startswith("FROM ")
    )
    assert "@sha256:" in gateway_base
    assert services["edge"]["image"] == gateway_base


def test_frontend_ci_uses_the_same_reviewed_node_base_as_the_app_image() -> None:
    node_base = next(
        line.split()[1] for line in (ROOT / "frontend/Dockerfile").read_text().splitlines() if line.startswith("FROM ")
    )
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    command = workflow["jobs"]["frontend"]["steps"][-1]["run"]
    assert "@sha256:" in node_base
    assert node_base in command.split()


def command_stubs(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Record public command arguments without starting Docker or exporting data."""
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "commands.jsonl"
    runner = tools / "record.py"
    runner.write_text(
        "import json, os, sys\n"
        "with open(os.environ['COMPOSE_CONTRACT_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'curl':\n"
        "    print('event: done\\ndata: {}\\n' if any('/api/chat' in v for v in sys.argv) else '{}')\n"
        "if sys.argv[1:4] == ['docker', 'image', 'save'] and os.environ.get('COMPOSE_CONTRACT_STOP_SAVE'):\n"
        "    sys.exit(77)\n"
    )
    for name in ["docker", "curl"]:
        executable = tools / name
        executable.write_text(f'#!/bin/sh\nexec "$COMPOSE_CONTRACT_PYTHON" "$COMPOSE_CONTRACT_RUNNER" {name} "$@"\n')
        executable.chmod(0o755)
    return {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "COMPOSE_CONTRACT_PYTHON": sys.executable,
        "COMPOSE_CONTRACT_RUNNER": str(runner),
        "COMPOSE_CONTRACT_LOG": str(log),
    }, log


def commands_from(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_packager_builds_app_but_exports_the_legacy_web_image(tmp_path: Path) -> None:
    env, log = command_stubs(tmp_path)
    env["COMPOSE_CONTRACT_STOP_SAVE"] = "1"
    result = subprocess.run(
        ["sh", "scripts/package_offline_demo.sh", str(tmp_path / "bundle with spaces")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 77  # Stop before copying public reports or constructing an archive.
    calls = commands_from(log)
    build = next(call for call in calls if "build" in call)
    assert build[build.index("build") + 1 :] == ["agent", "gateway", "app"]
    assert ["docker", "image", "tag", "na-maldivy-package-app", "na-maldivy-demo-web:20261028-full-ui"] in calls
    assert build[build.index("--env-file") + 1] == "/dev/null"


def test_offline_launcher_never_builds_or_downloads_images(tmp_path: Path) -> None:
    env, log = command_stubs(tmp_path)
    bundle = tmp_path / "offline bundle"
    bundle.mkdir()
    shutil.copy(ROOT / "scripts/start_offline_demo.sh", bundle / "start.sh")
    shutil.copy(ROOT / "docker-compose.release.yml", bundle / "compose.yml")
    (bundle / "images.tar").write_bytes(b"synthetic-test-archive")
    digest = hashlib.sha256((bundle / "images.tar").read_bytes()).hexdigest()
    (bundle / "SHA256SUMS").write_text(digest + "  images.tar\n")
    result = subprocess.run(["sh", str(bundle / "start.sh")], env=env, capture_output=True, timeout=20)
    assert result.returncode == 0
    calls = commands_from(log)
    assert calls[0][:3] == ["docker", "image", "load"]
    launch = next(call for call in calls if "up" in call)
    assert "--no-build" in launch
    assert launch[launch.index("--pull") + 1] == "never"
    assert "--wait" in launch


def test_smoke_starts_only_agent_and_gateway_without_publishing_the_root_app(tmp_path: Path) -> None:
    env, log = command_stubs(tmp_path)
    result = subprocess.run(["bash", "scripts/check.sh", "smoke"], cwd=ROOT, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0
    calls = commands_from(log)
    start = next(call for call in calls if "up" in call)
    assert start[-2:] == ["agent", "gateway"]
    assert "app" not in start and "web" not in start
    assert start[start.index("--env-file") + 1] == "/dev/null"
    assert start[start.index("-p") + 1] == "na-maldivy-check"
    assert any("localhost:18000/health" in call for call in calls)


def test_make_demo_commands_ignore_local_env_interpolation(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    env, log = command_stubs(tmp_path)
    result = subprocess.run(["make", "demo-up", "demo-down"], cwd=ROOT, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0
    calls = commands_from(log)
    assert len(calls) == 2
    assert all(call[call.index("--env-file") + 1] == "/dev/null" for call in calls)
