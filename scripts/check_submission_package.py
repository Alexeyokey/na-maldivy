"""Read-only checks for the default Docker package; never builds or starts it.

The package result does not certify main, the submission tag, a video, or a
deadline. Those human handoff items are recorded separately in the checklist.
Only .env.example is rendered, so private local .env values are not reported.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REGISTRIES = {"docker.io", "ghcr.io"}
PUBLIC_IMAGE_NAMES = {"python", "node", "nginx", "caddy", "ghcr.io/astral-sh/uv"}
PIN = re.compile(r"(?:@sha256:[a-fA-F0-9]{64}|:[^/:]+)$")


def _image_issue(image: str) -> str | None:
    if not PIN.search(image) or image.rsplit(":", 1)[-1].casefold() == "latest":
        return "An external image needs an explicit version or digest."
    first = image.split("/", 1)[0]
    registry = (
        first
        if "/" in image and ("." in first or ":" in first or first == "localhost")
        else "docker.io"
    )
    if registry not in PUBLIC_REGISTRIES:
        return "An external image uses an unverified registry."
    name = image.split("@", 1)[0].rsplit(":", 1)[0]
    name = name.removeprefix("docker.io/").removeprefix("library/")
    if name not in PUBLIC_IMAGE_NAMES:
        return "An image namespace is outside the audited public-source allowlist."
    return None


def _dockerfile_issues(path: Path) -> list[str]:
    if not path.is_file():
        return ["The active Dockerfile is absent."]
    source = path.read_text(encoding="utf-8")
    stages = re.findall(
        r"^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?",
        source,
        re.IGNORECASE | re.MULTILINE,
    )
    issues = []
    if len(stages) < 2:
        issues.append("The active Dockerfile must use multiple stages.")
    aliases = set()
    for image, alias in stages:
        if image.casefold() not in aliases:
            problem = _image_issue(image)
            if problem:
                issues.append(problem)
        if alias:
            aliases.add(alias.casefold())
    for image in re.findall(
        r"^COPY\s+[^\n]*?--from=(\S+)", source, re.IGNORECASE | re.MULTILINE
    ):
        if image.casefold() not in aliases and not image.isdecimal():
            problem = _image_issue(image)
            if problem:
                issues.append(problem)
    final = re.split(r"^FROM\s+", source, flags=re.IGNORECASE | re.MULTILINE)[-1]
    users = re.findall(r"^USER\s+(\S+)", final, re.IGNORECASE | re.MULTILINE)
    if not users or users[-1].split(":", 1)[0].casefold() in {"root", "0"}:
        issues.append("The runtime stage must declare a non-root user.")
    if not re.search(r"^HEALTHCHECK\s+(?!NONE\b)", final, re.IGNORECASE | re.MULTILINE):
        issues.append("The runtime stage must declare a health check.")
    if re.search(
        r"^RUN\s+.*\b(?:pytest|ruff|build-essential|gcc|g\+\+)\b",
        final,
        re.IGNORECASE | re.MULTILINE,
    ):
        issues.append("Development tooling is installed in the runtime stage.")
    return issues


def inspect_package(root: Path, config: dict) -> dict:
    """Inspect the resolved default topology, rather than individual YAML lines."""
    checks = []

    def add(name: str, passed: bool, detail: str, *, required: bool = True) -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "detail": detail,
                "required": required,
            }
        )

    mandatory = ["README.md", "docker-compose.yml", ".env.example", "backend/uv.lock"]
    missing = [name for name in mandatory if not (root / name).is_file()]
    add(
        "mandatory_source_files",
        not missing,
        "All required files exist."
        if not missing
        else "Missing: " + ", ".join(missing),
    )
    services = config.get("services", {})
    if not isinstance(services, dict) or not services:
        add("default_services", False, "The default Compose project has no services.")
        return {"status": "failed", "scope": "default_docker_package", "checks": checks}
    public = []
    for name, service in services.items():
        for port in service.get("ports", []):
            public.append((name, str(port.get("published")), port.get("target")))
    add(
        "single_browser_entrypoint",
        len(public) == 1 and public[0][1:] == ("8080", 8080),
        "The only published endpoint must map host 8080 to runtime 8080.",
    )
    web_name = public[0][0] if len(public) == 1 else None
    dockerfiles = []
    for name, service in services.items():
        add(
            name + ":platform",
            service.get("platform") == "linux/amd64",
            "Default services explicitly target linux/amd64.",
        )
        health = service.get("healthcheck", {})
        add(
            name + ":health",
            bool(health.get("test"))
            and not health.get("disable")
            and health.get("test") != ["NONE"],
            "Compose must include an active container health check.",
        )
        add(
            name + ":resources",
            bool(service.get("mem_limit")) and float(service.get("cpus", 0)) > 0,
            "Optional container CPU and memory limits complement the required README resource guidance.",
            required=False,
        )
        add(
            name + ":logs",
            service.get("logging", {}).get("driver") != "none",
            "Container stdout/stderr logging is enabled.",
        )
        user = str(service.get("user", ""))
        add(
            name + ":user_override",
            not user or user.split(":", 1)[0].casefold() not in {"root", "0"},
            "Compose must not override the runtime user to root.",
        )
        if service.get("image") and not service.get("build"):
            add(
                name + ":image",
                _image_issue(service["image"]) is None,
                "An image from a verified public registry has an explicit version.",
            )
        build = service.get("build")
        if build:
            context = Path(build["context"])
            context = context if context.is_absolute() else root / context
            path = context / build.get("dockerfile", "Dockerfile")
            dockerfiles.append(path)
            problems = (
                _dockerfile_issues(path)
                if context.resolve().is_relative_to(root.resolve())
                and path.resolve().is_relative_to(root.resolve())
                else ["An active build depends on files outside this repository."]
            )
            add(
                name + ":runtime_image",
                not problems,
                " ".join(problems)
                if problems
                else "Versioned public images, multiple stages, non-root runtime and health check.",
            )
    add(
        "application_dockerfiles",
        bool(dockerfiles),
        "Application builds are defined by checked-in Dockerfiles.",
    )
    if web_name:
        web = services[web_name]
        add(
            "ui_runtime_port",
            str(web.get("environment", {}).get("PORT")) == "8080",
            "The UI process itself listens on 8080.",
        )
        depends = web.get("depends_on", {})
        add(
            "ready_dependencies",
            bool(depends)
            and all(v.get("condition") == "service_healthy" for v in depends.values()),
            "The browser service waits for healthy dependencies.",
        )
    agent_env = services.get("agent", {}).get("environment", {})
    expected = {
        "LLM_PROVIDER": "fake",
        "TOOLS_MODE": "real",
        "FLIGHTS_MODE": "demo",
        "TRAINS_MODE": "synthetic",
        "CACHE_MODE": "replay",
    }
    add(
        "keyless_seeded_demo",
        all(agent_env.get(k) == v for k, v in expected.items()),
        "Default app uses the seeded real tool registry, explicit demo data, and fake LLM without keys.",
    )
    secret_names = {
        "LLM_API_KEY",
        "AVIASALES_API_TOKEN",
        "YANDEX_RASP_API_KEY",
        "CODEX_LOCAL_TOKEN",
    }
    add(
        "example_credentials",
        all(
            not service.get("environment", {}).get(key)
            for service in services.values()
            for key in secret_names
        ),
        "Rendered example configuration contains no credentials.",
    )
    example = {}
    if (root / ".env.example").is_file():
        for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                example[key.strip()] = value.strip().strip("\"'")
    add(
        "example_file_credentials",
        all(not example.get(name) for name in secret_names),
        "The example file itself contains only empty credential values.",
    )
    lock = (
        (root / "backend/uv.lock").read_text(encoding="utf-8")
        if (root / "backend/uv.lock").is_file()
        else ""
    )
    registries = set(re.findall(r'registry\s*=\s*"([^\"]+)"', lock))
    add(
        "dependency_registry",
        bool(registries) and registries <= {"https://pypi.org/simple"},
        "Python lock resolves from public PyPI; no private package registry is required.",
    )
    add(
        "frontend_dependencies",
        (root / "frontend/package.json").is_file()
        and not any(
            json.loads((root / "frontend/package.json").read_text()).get(k)
            for k in ("dependencies", "devDependencies")
        ),
        "The packaged frontend requires no private npm dependencies.",
    )
    return {
        "status": "passed"
        if all(row["passed"] for row in checks if row["required"])
        else "failed",
        "scope": "default_docker_package",
        "checks": checks,
        "handoff_certified": False,
    }


def render_compose(root: Path) -> dict:
    allowed = {
        "PATH",
        "HOME",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_API_VERSION",
        "DOCKER_CONFIG",
        "XDG_RUNTIME_DIR",
        "SYSTEMROOT",
        "USERPROFILE",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    process = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(root / ".env.example"),
            "-f",
            str(root / "docker-compose.yml"),
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=True,
    )
    return json.loads(process.stdout)


def probe_http(base_url: str) -> list[dict]:
    url = urllib.parse.urlsplit(base_url)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost"}
        or url.port != 8080
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or url.username
        or url.password
    ):
        raise ValueError(
            "Only the local default entrypoint http://localhost:8080 is accepted."
        )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    checks = []
    for path in ("/", "/health"):
        try:
            with opener.open(base_url.rstrip("/") + path, timeout=5) as response:
                body = response.read(65537)
                if path == "/":
                    valid = (
                        response.status == 200
                        and "text/html" in response.headers.get("content-type", "")
                        and b"<html" in body.lower()
                    )
                else:
                    value = json.loads(body) if len(body) <= 65536 else None
                    valid = (
                        response.status == 200
                        and isinstance(value, dict)
                        and value.get("status") == "ok"
                    )
            checks.append(
                {
                    "name": "http:" + path,
                    "passed": valid,
                    "detail": "Anonymous local HTTP endpoint is ready.",
                }
            )
        except (OSError, ValueError, urllib.error.URLError):
            checks.append(
                {
                    "name": "http:" + path,
                    "passed": False,
                    "detail": "Local endpoint is unavailable, redirects, or returns an invalid response.",
                }
            )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--base-url",
        help="Optionally probe the already running http://localhost:8080 app.",
    )
    args = parser.parse_args()
    try:
        report = inspect_package(ROOT, render_compose(ROOT))
        if args.base_url:
            report["checks"].extend(probe_http(args.base_url))
            report["status"] = (
                "passed"
                if all(
                    row["passed"]
                    for row in report["checks"]
                    if row.get("required", True)
                )
                else "failed"
            )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        report = {
            "status": "failed",
            "scope": "default_docker_package",
            "error": "Could not render the default Compose package with .env.example; private configuration was not printed.",
            "handoff_certified": False,
        }
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
