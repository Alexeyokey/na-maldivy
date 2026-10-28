"""Start the personal Codex demo on loopback, without copying login or loading .env."""

import argparse
import contextlib
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port_number(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("Выберите порт от 1024 до 65535")
    return port


def stop(process: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    # A child can retain the process group after the service itself exits.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def stop_requested(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Личный чат проекта через ваш вход в Codex"
    )
    parser.add_argument("--port", type=port_number, default=18800)
    parser.add_argument("--agent-port", type=port_number, default=18801)
    args = parser.parse_args()
    if args.port == args.agent_port:
        parser.error("Для сайта и агента нужны разные порты")
    binary, node = shutil.which("codex"), shutil.which("node")
    python = ROOT / "backend/.venv/bin/python"
    if not binary or not node or not python.is_file():
        print(
            "Нужны установленный Codex, Node.js 22+ и окружение backend/.venv.",
            file=sys.stderr,
        )
        return 1
    # CLI owns the account session. Do not open, copy or print its credential files.
    status = subprocess.run(
        [binary, "login", "status"], capture_output=True, timeout=20, check=False
    )
    if status.returncode or b"ChatGPT" not in status.stdout + status.stderr:
        print("Сначала войдите через ChatGPT командой codex login.", file=sys.stderr)
        return 1
    for port in (args.port, args.agent_port):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                print(
                    f"Локальный порт {port} занят; выберите другой через --port / --agent-port.",
                    file=sys.stderr,
                )
                return 1
    private = ROOT / "artifacts/personal-codex-private"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.chmod(0o700)
    token = secrets.token_urlsafe(48)
    env = os.environ.copy()
    for name in (
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "AVIASALES_API_TOKEN",
        "YANDEX_RASP_API_KEY",
    ):
        env.pop(name, None)
    env.update(
        LLM_PROVIDER="codex_local",
        LLM_MODEL_MAIN="gpt-6-luna",
        LLM_MODEL_FAST="gpt-6-luna",
        CODEX_BINARY=binary,
        CODEX_LOCAL_TOKEN=token,
        LLM_TIMEOUT_S="90",
        TOOLS_MODE="real",
        FLIGHTS_MODE="demo",
        TRAINS_MODE="synthetic",
        CACHE_MODE="replay",
        DATA_DIR=str(ROOT / "data"),
        AVIASALES_CACHE_DIR=str(ROOT / "data/demo/aviasales"),
        CHECKOUT_DB_PATH=str(private / "checkout.sqlite3"),
        LOG_LEVEL="WARNING",
        CORS_ORIGINS=f"http://127.0.0.1:{args.port},http://localhost:{args.port}",
    )
    # This personal profile deliberately uses only the fast model, with no slower fallback.
    processes: list[subprocess.Popen] = []
    previous_term = signal.signal(signal.SIGTERM, stop_requested)
    try:
        processes.append(
            subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "app.personal_codex:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.agent_port),
                    "--workers",
                    "1",
                    "--timeout-graceful-shutdown",
                    "3",
                    "--no-access-log",
                ],
                cwd=ROOT / "backend",
                env=env,
                start_new_session=True,
            )
        )
        health = urllib.request.Request(
            f"http://127.0.0.1:{args.agent_port}/health",
            headers={"x-local-codex-token": token},
        )
        local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            if processes[0].poll() is not None:
                raise RuntimeError("Агент не запустился")
            try:
                with local_http.open(health, timeout=1) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        else:
            raise RuntimeError("Агент не ответил при запуске")
        env.update(
            HOST="127.0.0.1",
            PORT=str(args.port),
            AGENT_URL=f"http://127.0.0.1:{args.agent_port}",
            CHAT_MODE="api",
            LOCAL_ONLY="1",
        )
        processes.append(
            subprocess.Popen(
                [node, str(ROOT / "frontend/server.mjs")],
                cwd=ROOT,
                env=env,
                start_new_session=True,
            )
        )
        print(
            f"\nЛичный Codex: http://127.0.0.1:{args.port}\nДля остановки нажмите Ctrl+C.\n",
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.3)
        raise RuntimeError("Один из локальных сервисов остановился")
    except KeyboardInterrupt:
        return 0
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            stop(process)
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
