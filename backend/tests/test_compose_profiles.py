"""Verify the standard smoke and legacy launch contracts against app:8080."""

import hashlib
import json
import os
import shlex
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
        "import json, os, sys\nfrom pathlib import Path\n"
        f"with open({str(log)!r}, 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"with open({str(log) + '.meta'!r}, 'a') as meta:\n"
        "    here = Path.cwd()\n"
        "    safe_context = here.name.startswith('na-maldivy-check.') and not (here / '.git').exists()\n"
        "    example = here / '.env.example'\n"
        "    env_file = here / '.env'\n"
        "    record = {'cwd': str(here), 'environment': {key: os.environ.get(key) for key in "
        "['APP_PORT','TOOLS_MODE','FLIGHTS_MODE','TRAINS_MODE','CACHE_MODE','LLM_PROVIDER']}, "
        "'credentials_empty': all(not os.environ.get(key) for key in "
        "['LLM_API_KEY','AVIASALES_API_TOKEN','YANDEX_RASP_API_KEY','CODEX_LOCAL_TOKEN','OPENAI_API_KEY']), "
        "'compose_file_override_absent': not os.environ.get('COMPOSE_FILE'), "
        "'git_absent': not (here / '.git').exists(), "
        "'local_files_absent': not (here / 'local-only.txt').exists() "
        "and not (here / 'artifacts/private.txt').exists(), "
        "'example_matches_env': safe_context and example.is_file() and env_file.is_file() "
        "and example.read_bytes() == env_file.read_bytes(), "
        "'working_marker': (here / 'working-marker.txt').read_text() "
        "if (here / 'working-marker.txt').is_file() else None}\n"
        "    meta.write(json.dumps(record) + '\\n')\n"
        f"control = Path({str(tools / 'behavior.json')!r})\n"
        "behavior = json.loads(control.read_text()) if control.exists() else {}\n"
        "if 'check_running_three_destinations.py' in ' '.join(sys.argv):\n"
        "    action = 'resume' if '--resume' in sys.argv else 'acceptance'\n"
        "    if behavior.get('fail') == action: sys.exit(71)\n"
        "    print(json.dumps({'status':'passed','scope':'synthetic-command-contract'}))\n"
        "if behavior.get('fail') == 'build' and 'up' in sys.argv and '--build' in sys.argv: sys.exit(72)\n"
        "if sys.argv[1] == 'curl':\n"
        "    print('event: done\\ndata: {}\\n' if any('/api/chat' in v for v in sys.argv) else '{}')\n"
        "if sys.argv[1:4] == ['docker', 'image', 'save'] and os.environ.get('COMPOSE_CONTRACT_STOP_SAVE'):\n"
        "    sys.exit(77)\n"
    )
    for name in ["docker", "curl"]:
        executable = tools / name
        executable.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(runner))} {name} "$@"\n')
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


def smoke_checkout(tmp_path: Path) -> Path:
    """A tiny Git index is enough to verify copying working files without secrets."""
    checkout = tmp_path / "checkout with spaces"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "artifacts").mkdir()
    shutil.copy(ROOT / "scripts/check.sh", checkout / "scripts/check.sh")
    shutil.copy(ROOT / "docker-compose.yml", checkout / "docker-compose.yml")
    (checkout / ".env.example").write_text("TOOLS_MODE=real\nLLM_API_KEY=\nAPP_PORT=8080\n")
    (checkout / ".gitignore").write_text(".env\nlocal-only.txt\nartifacts/\n")
    (checkout / "working-marker.txt").write_text("indexed version\n")
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True, capture_output=True)
    (checkout / "working-marker.txt").write_text("modified working contents\n")
    (checkout / ".env").write_text("LLM_API_KEY=PRIVATE-SMOKE-CANARY\nAPP_PORT=7777\n")
    (checkout / "local-only.txt").write_text("PRIVATE-SMOKE-CANARY\n")
    (checkout / "artifacts/private.txt").write_text("PRIVATE-SMOKE-CANARY\n")
    # Even accidentally tracked private paths must not enter the build context.
    subprocess.run(["git", "add", "-f", ".env", "artifacts/private.txt"], cwd=checkout, check=True, capture_output=True)
    return checkout


@pytest.mark.parametrize("port", [None, "28180"])
def test_smoke_uses_clean_standard_app_and_agent_then_checks_restart_and_cleans_only_its_project(
    tmp_path: Path, port: str | None
) -> None:
    env, log = command_stubs(tmp_path)
    checkout = smoke_checkout(tmp_path)
    env.update(
        LLM_PROVIDER="openai",
        TOOLS_MODE="stub",
        FLIGHTS_MODE="aviasales",
        CACHE_MODE="live",
        TRAINS_MODE="yandex",
        LLM_API_KEY="PRIVATE-SMOKE-CANARY",
        AVIASALES_API_TOKEN="PRIVATE-SMOKE-CANARY",
        YANDEX_RASP_API_KEY="PRIVATE-SMOKE-CANARY",
        CODEX_LOCAL_TOKEN="PRIVATE-SMOKE-CANARY",
        OPENAI_API_KEY="PRIVATE-SMOKE-CANARY",
        COMPOSE_FILE="private-compose.yml",
        APP_PORT="7777",
    )
    if port:
        env["NA_MALDIVY_CHECK_PORT"] = port
    result = subprocess.run(
        ["bash", "scripts/check.sh", "smoke"], cwd=checkout, env=env, capture_output=True, timeout=20
    )
    assert result.returncode == 0
    calls = commands_from(log)
    start = next(call for call in calls if "up" in call)
    project = start[start.index("-p") + 1]
    clean = Path(start[start.index("-f") + 1]).parent
    assert start[-2:] == ["app", "agent"] and "--build" in start and "--wait" in start
    assert project.startswith("na-maldivy-check-") and project.removeprefix("na-maldivy-check-").isdigit()
    assert clean != checkout and not clean.exists()
    for call in calls:
        assert call[:2] == ["docker", "compose"]
        assert call[call.index("-p") + 1] == project
        assert call.count("-f") == 1 and call[call.index("-f") + 1] == str(clean / "docker-compose.yml")
        assert call[call.index("--env-file") + 1] == str(clean / ".env")
        assert "gateway" not in call and "docker-compose.demo.yml" not in " ".join(call)
    checks = [call for call in calls if "/app/scripts/check_running_three_destinations.py" in call]
    assert len(checks) == 2 and "--resume" not in checks[0] and "--resume" in checks[1]
    assert all(call[call.index("--base-url") + 1] == "http://app:8080" for call in checks)
    assert all(call[call.index("--context") + 1] == "/state/check-private.json" for call in checks)
    restart = next(call for call in calls if "restart" in call)
    assert restart[-1] == "agent" and calls.index(checks[0]) < calls.index(restart) < calls.index(checks[1])
    wait = next(call for call in calls if "up" in call and "--no-build" in call)
    assert "--wait" in wait and wait[-2:] == ["app", "agent"]
    assert calls[-1][-3:] == ["down", "--volumes", "--remove-orphans"]
    metadata = [json.loads(line) for line in Path(str(log) + ".meta").read_text().splitlines()]
    for row in metadata:
        assert row["cwd"] == str(clean)
        assert row["credentials_empty"] and row["compose_file_override_absent"]
        assert row["git_absent"] and row["local_files_absent"] and row["example_matches_env"]
        assert row["working_marker"] == "modified working contents\n"
        assert row["environment"] == {
            "APP_PORT": port or "18080",
            "TOOLS_MODE": "real",
            "FLIGHTS_MODE": "demo",
            "TRAINS_MODE": "synthetic",
            "CACHE_MODE": "replay",
            "LLM_PROVIDER": "fake",
        }
    assert b"PRIVATE-SMOKE-CANARY" not in result.stdout + result.stderr
    assert (checkout / ".env").read_text() == "LLM_API_KEY=PRIVATE-SMOKE-CANARY\nAPP_PORT=7777\n"


@pytest.mark.parametrize("failure", ["build", "acceptance", "resume"])
def test_smoke_failure_preserves_the_failure_and_shows_safe_diagnostics_then_removes_test_resources(
    tmp_path: Path, failure: str
) -> None:
    env, log = command_stubs(tmp_path)
    checkout = smoke_checkout(tmp_path)
    (tmp_path / "tools/behavior.json").write_text(json.dumps({"fail": failure}))
    result = subprocess.run(
        ["bash", "scripts/check.sh", "smoke"], cwd=checkout, env=env, capture_output=True, timeout=20
    )
    assert result.returncode == (72 if failure == "build" else 71)
    calls = commands_from(log)
    assert any("logs" in call for call in calls)
    assert any(call[-3:] == ["agent", "cat", "/state/check-report.json"] for call in calls)
    assert calls[-1][-3:] == ["down", "--volumes", "--remove-orphans"]
    clean = Path(calls[0][calls[0].index("-f") + 1]).parent
    assert not clean.exists()
    assert b"PRIVATE-SMOKE-CANARY" not in result.stdout + result.stderr


@pytest.mark.parametrize("port", ["invalid", "0", "65536", "99999999999999999999"])
def test_smoke_rejects_invalid_ports_before_starting_any_container(tmp_path: Path, port: str) -> None:
    env, log = command_stubs(tmp_path)
    checkout = smoke_checkout(tmp_path)
    env["NA_MALDIVY_CHECK_PORT"] = port
    result = subprocess.run(
        ["bash", "scripts/check.sh", "smoke"], cwd=checkout, env=env, capture_output=True, timeout=20
    )
    assert result.returncode == 2
    assert not log.exists()
    assert b"PRIVATE-SMOKE-CANARY" not in result.stdout + result.stderr


def test_make_demo_commands_ignore_local_env_interpolation(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    env, log = command_stubs(tmp_path)
    result = subprocess.run(["make", "demo-up", "demo-down"], cwd=ROOT, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0
    calls = commands_from(log)
    assert len(calls) == 2
    assert all(call[call.index("--env-file") + 1] == "/dev/null" for call in calls)
