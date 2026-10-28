"""Локальный адаптер против настоящего дочернего процесса без аккаунта и сети."""

import asyncio
import json
import os
import signal
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.llm.base import LLMError, Message, ToolCall, ToolSpec
from app.llm.codex_local import CodexLocalClient

HELLO = [Message(role="user", content="Привет, помоги с отпуском")]
PRIVATE = "private-provider-token-do-not-repeat"
SEARCH = ToolSpec(
    name="search_hotels",
    description="Поиск отелей",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)


def envelope(text: str | None = "Привет!", calls: list[dict[str, Any]] | None = None) -> str:
    return json.dumps({"text": text, "tool_calls": calls or []}, ensure_ascii=False)


@dataclass
class FakeCodex:
    binary: Path
    record: Path

    def invocation(self) -> dict[str, Any]:
        return json.loads(self.record.read_text())


@pytest.fixture
def fake_codex(tmp_path: Path) -> Iterator[Callable[..., FakeCodex]]:
    records: list[Path] = []

    def create(
        *,
        output: str | None = None,
        events: list[dict[str, Any]] | None = None,
        returncode: int = 0,
        stderr: str = "",
        hang: bool = False,
        child: bool = False,
        stubborn_child: bool = False,
        flood: str | None = None,
        version: str = "codex-cli 0.163.0-alpha.11",
    ) -> FakeCodex:
        index = len(records)
        record = tmp_path / f"invocation-{index}.json"
        binary = tmp_path / f"codex-stub-{index}"
        records.append(record)
        scenario = {
            "record": str(record),
            "output": envelope() if output is None else output,
            "events": events
            if events is not None
            else [{"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}}],
            "returncode": returncode,
            "stderr": stderr,
            "hang": hang,
            "child": child,
            "stubborn_child": stubborn_child,
            "flood": flood,
            "version": version,
        }
        binary.write_text(
            f"#!{Path(sys.executable).resolve()}\n"
            "import json, os, pathlib, subprocess, sys, time\n"
            f"scenario = json.loads({json.dumps(json.dumps(scenario))})\n"
            "args = sys.argv[1:]\n"
            "if args == ['--version']:\n"
            "    print(scenario['version'])\n"
            "    sys.exit(0)\n"
            "def option(*names):\n"
            "    return next((args[i + 1] for i, arg in enumerate(args[:-1]) if arg in names), None)\n"
            "schema_path = option('--output-schema')\n"
            "record = {'args': args, 'cwd': os.getcwd(), 'stdin': sys.stdin.read(), 'pid': os.getpid(),\n"
            "          'schema': json.loads(pathlib.Path(schema_path).read_text()) if schema_path else None}\n"
            "record['forbidden_env'] = [key for key in ['OPENAI_API_KEY', 'LLM_API_KEY', 'TRAVELPAYOUTS_TOKEN']\n"
            "                           if key in os.environ]\n"
            "if scenario['child']:\n"
            "    ready = scenario['record'] + '.ready'\n"
            "    code = ('import pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '\n"
            "            + 'pathlib.Path(' + repr(ready) + ').touch(); time.sleep(30)')\n"
            "    if not scenario['stubborn_child']:\n"
            "        code = 'import time; time.sleep(30)'\n"
            "    proc = subprocess.Popen([sys.executable, '-c', code])\n"
            "    record['child_pid'] = proc.pid\n"
            "    if scenario['stubborn_child']:\n"
            "        while not pathlib.Path(ready).exists():\n"
            "            time.sleep(0.01)\n"
            "pathlib.Path(scenario['record']).write_text(json.dumps(record))\n"
            "if scenario['flood']:\n"
            "    stream = sys.stdout if scenario['flood'] == 'stdout' else sys.stderr\n"
            "    stream.buffer.write(b'x' * (256 * 1024))\n"
            "    stream.buffer.flush()\n"
            "    time.sleep(30)\n"
            "if scenario['hang']:\n"
            "    time.sleep(30)\n"
            "target = option('--output-last-message', '-o')\n"
            "if target:\n"
            "    pathlib.Path(target).write_text(scenario['output'])\n"
            "sys.stderr.write(scenario['stderr'])\n"
            "for event in scenario['events']:\n"
            "    print(json.dumps(event), flush=True)\n"
            "sys.exit(scenario['returncode'])\n"
        )
        binary.chmod(0o700)
        return FakeCodex(binary=binary, record=record)

    yield create
    # A failed cleanup assertion must not leave the deliberately stalled fixture alive.
    for record in records:
        if not record.exists():
            continue
        invocation = json.loads(record.read_text())
        for key in ("child_pid", "pid"):
            pid = invocation.get(key)
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def make_client(fake: FakeCodex, *, timeout_s: float = 3.0) -> CodexLocalClient:
    return CodexLocalClient(
        binary=str(fake.binary), models={"main": "main-example", "fast": "fast-example"}, timeout_s=timeout_s
    )


async def wait_started(fake: FakeCodex) -> dict[str, Any]:
    for _ in range(100):
        if fake.record.exists():
            return fake.invocation()
        await asyncio.sleep(0.01)
    pytest.fail("fake CLI did not receive the request")


def running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # POSIX permits kill(pid, 0) for zombies; these cannot read files or execute tools.
    proc_stat = Path(f"/proc/{pid}/stat")
    return not (proc_stat.exists() and proc_stat.read_text().split()[2] == "Z")


async def assert_stopped(invocation: dict[str, Any]) -> None:
    pids = [invocation[k] for k in ("pid", "child_pid") if k in invocation]
    for _ in range(100):
        if not any(running(pid) for pid in pids):
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"CLI processes survived request cleanup: {pids}")


def assert_workspace_removed(invocation: dict[str, Any]) -> None:
    assert not Path(invocation["cwd"]).exists()


async def test_complete_preserves_response_and_selects_fast_model(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex()
    async_client = make_client(fake)
    response = await async_client.complete(HELLO, tier="fast")
    await async_client.aclose()

    assert response.text == "Привет!"
    assert response.tool_calls == []
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 3
    assert async_client.completed_calls == 1
    assert async_client.last_model == response.model
    assert async_client.last_usage == response.usage
    args = fake.invocation()["args"]
    assert args[args.index("--model") + 1] == "fast-example"
    assert_workspace_removed(fake.invocation())


async def test_unset_model_keeps_codex_default(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex()
    client = CodexLocalClient(binary=str(fake.binary), timeout_s=3)
    assert (await client.complete(HELLO)).text == "Привет!"
    await client.aclose()
    assert "--model" not in fake.invocation()["args"]


@pytest.mark.parametrize("version", ["codex-cli 0.162.0", "codex-cli 0.164.0", PRIVATE])
async def test_unknown_cli_version_cannot_start_a_model_request(
    version: str, fake_codex: Callable[..., FakeCodex]
) -> None:
    fake = fake_codex(version=version)
    client = make_client(fake)
    with pytest.raises(LLMError) as caught:
        await client.complete(HELLO)
    await client.aclose()
    assert PRIVATE not in str(caught.value)
    assert not fake.record.exists()
    assert client.completed_calls == 0


async def test_isolates_working_directory_and_sends_private_prompt_only_on_stdin(
    fake_codex: Callable[..., FakeCodex], monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "TRAVELPAYOUTS_TOKEN"):
        monkeypatch.setenv(key, PRIVATE)
    fake = fake_codex()
    client = make_client(fake)
    await client.complete([Message(role="user", content=PRIVATE)])
    await client.aclose()

    invocation = fake.invocation()
    args = invocation["args"]
    assert PRIVATE in invocation["stdin"]
    assert PRIVATE not in " ".join(args)
    assert "--ignore-user-config" in args
    assert "--ignore-rules" in args
    assert "--ephemeral" in args
    assert "read-only" in args
    assert "--json" in args
    assert "--output-schema" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert 'approval_policy="never"' in args
    assert 'model_reasoning_effort="low"' in args
    for feature in ("shell_tool", "apps", "plugins", "multi_agent", "browser_use", "computer_use"):
        assert f"features.{feature}=false" in args
    assert invocation["forbidden_env"] == []
    assert_workspace_removed(invocation)
    assert invocation["schema"]["type"] == "object"


async def test_tool_round_trip_preserves_roles_ids_and_arguments(fake_codex: Callable[..., FakeCodex]) -> None:
    call = {"id": "call-1", "name": "search_hotels", "arguments_json": '{"city":"Male"}'}
    first = fake_codex(output=envelope(None, [call]))
    client = make_client(first)
    result = await client.complete(HELLO, tools=[SEARCH])
    await client.aclose()
    assert result.tool_calls == [ToolCall(id="call-1", name="search_hotels", arguments={"city": "Male"})]
    assert result.text is None

    history = [
        Message(role="system", content="Используй только предложения из поиска."),
        *HELLO,
        Message(role="assistant", tool_calls=result.tool_calls),
        Message(role="tool", tool_call_id="call-1", content='{"hotels":[]}'),
    ]
    second = fake_codex(output=envelope("Подходящих предложений нет."))
    follow_up = make_client(second)
    assert (await follow_up.complete(history, tools=[SEARCH])).text == "Подходящих предложений нет."
    await follow_up.aclose()
    prompt = second.invocation()["stdin"]
    for expected in ("system", "assistant", "tool", "call-1", "search_hotels", "Male", "hotels"):
        assert expected in prompt
    assert second.invocation()["schema"] is not None


async def test_json_mode_returns_json_object_text(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex(output=envelope('{"destination":"MLE","children":null}'))
    client = make_client(fake)
    response = await client.complete(HELLO, json_mode=True)
    await client.aclose()
    assert json.loads(response.text or "") == {"destination": "MLE", "children": None}


@pytest.mark.parametrize("text", ["not json", "[]", '"text"', "null"])
async def test_json_mode_rejects_non_object_text(text: str, fake_codex: Callable[..., FakeCodex]) -> None:
    client = make_client(fake_codex(output=envelope(text)))
    with pytest.raises(LLMError):
        await client.complete(HELLO, json_mode=True)
    await client.aclose()


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        "[]",
        envelope(None, [{"id": "call-1", "name": "shell", "arguments_json": "{}"}]),
        envelope(None, [{"id": "call-1", "name": "search_hotels", "arguments_json": "[1,2]"}]),
        envelope(None, [{"id": "call-1", "name": "search_hotels", "arguments_json": "{broken"}]),
        envelope(None, [{"id": "", "name": "search_hotels", "arguments_json": "{}"}]),
        envelope(
            None,
            [
                {"id": "same", "name": "search_hotels", "arguments_json": "{}"},
                {"id": "same", "name": "search_hotels", "arguments_json": "{}"},
            ],
        ),
    ],
)
async def test_untrusted_final_output_cannot_escape_tool_boundary(
    output: str, fake_codex: Callable[..., FakeCodex]
) -> None:
    client = make_client(fake_codex(output=output))
    with pytest.raises(LLMError):
        await client.complete(HELLO, tools=[SEARCH])
    await client.aclose()


@pytest.mark.parametrize(
    "events,returncode",
    [
        ([], 0),
        ([{"type": "turn.failed", "error": {"message": PRIVATE}}], 0),
        ([{"type": "error", "message": PRIVATE}], 0),
        ([{"type": "turn.completed"}], 1),
        ([{"type": "turn.completed", "usage": None}], 0),
        ([{"type": "turn.completed"}, {"type": "turn.completed"}], 0),
        ([{"type": "item.completed", "item": {"type": "command_execution"}}, {"type": "turn.completed"}], 0),
    ],
)
async def test_failure_never_returns_stale_output_or_provider_diagnostics(
    events: list[dict[str, Any]], returncode: int, fake_codex: Callable[..., FakeCodex]
) -> None:
    fake = fake_codex(events=events, returncode=returncode, stderr=PRIVATE * 2000)
    client = make_client(fake)
    with pytest.raises(LLMError) as caught:
        await client.complete([Message(role="user", content=PRIVATE)])
    await client.aclose()
    assert PRIVATE not in str(caught.value)
    assert len(str(caught.value)) < 512
    assert client.completed_calls == 0
    assert_workspace_removed(fake.invocation())


async def test_stream_yields_only_validated_text(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex(
        output=envelope("Мале — подходящий вариант."),
        events=[
            {"type": "item.completed", "item": {"type": "agent_message", "text": PRIVATE}},
            {"type": "turn.completed"},
        ],
    )
    client = make_client(fake)
    chunks = [chunk async for chunk in client.stream(HELLO)]
    await client.aclose()
    assert "".join(chunks) == "Мале — подходящий вариант."
    assert PRIVATE not in "".join(chunks)
    assert all(isinstance(chunk, str) for chunk in chunks)


async def test_timeout_stops_cli_and_child_process(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex(hang=True, child=True)
    client = make_client(fake, timeout_s=1.5)
    with pytest.raises(LLMError):
        await client.complete(HELLO)
    await client.aclose()
    await assert_stopped(fake.invocation())
    assert_workspace_removed(fake.invocation())


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
async def test_oversized_live_pipe_is_stopped_before_provider_timeout(
    pipe: str, fake_codex: Callable[..., FakeCodex], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import codex_local

    monkeypatch.setattr(codex_local, "_MAX_OUTPUT_BYTES", 128 * 1024)
    fake = fake_codex(flood=pipe, child=True)
    client = make_client(fake, timeout_s=20)
    # The fixture writes too much and then hangs. A post-exit length check cannot
    # satisfy this bound: the adapter must stop reading and kill the process now.
    async with asyncio.timeout(4):
        with pytest.raises(LLMError) as caught:
            await client.complete(HELLO)
    assert PRIVATE not in str(caught.value)
    assert client.completed_calls == 0
    await client.aclose()
    await assert_stopped(fake.invocation())
    assert_workspace_removed(fake.invocation())


async def test_timeout_stops_orphan_that_retains_cli_pipes(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex(child=True, stubborn_child=True)
    client = make_client(fake, timeout_s=1.5)
    with pytest.raises(LLMError):
        await client.complete(HELLO)
    await client.aclose()
    await assert_stopped(fake.invocation())
    assert_workspace_removed(fake.invocation())


async def test_cancellation_stops_cli_and_child_process(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex(hang=True, child=True)
    client = make_client(fake)
    request = asyncio.create_task(client.complete(HELLO))
    invocation = await wait_started(fake)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await client.aclose()
    await assert_stopped(invocation)
    assert_workspace_removed(invocation)


async def test_close_stops_active_request_and_rejects_new_requests(fake_codex: Callable[..., FakeCodex]) -> None:
    fake = fake_codex(hang=True, child=True)
    client = make_client(fake)
    request = asyncio.create_task(client.complete(HELLO))
    invocation = await wait_started(fake)
    await client.aclose()
    with pytest.raises((LLMError, asyncio.CancelledError)):
        await request
    await assert_stopped(invocation)
    with pytest.raises(LLMError):
        await client.complete(HELLO)
    await client.aclose()
