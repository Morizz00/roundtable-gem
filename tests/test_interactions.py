import io
import tarfile
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

import app.interactions as interactions
from app.config import AGENT_ID, RunBudget
from app.interactions import Gateway, SnapshotError, extract_workspace
from app.keys import KeyRing
from app.routing import AgentSpec
from app.tools import ToolRegistry

SPEC = AgentSpec(role="coder", model="gemini-3.5-flash-lite")


# ---- fakes ---------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, code, message="boom"):
        super().__init__(message)
        self.code = code


class FakeClient:
    def __init__(self, *responses):
        self.calls = []
        self._responses = list(responses)
        self.interactions = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def interaction(status="completed", id="i1", env="env1", text="", tokens=100, steps=()):
    return SimpleNamespace(status=status, id=id, environment_id=env, output_text=text,
                           usage=SimpleNamespace(total_tokens=tokens), steps=list(steps))


def fn_call(name, arguments, id="c1"):
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, id=id)


def verdict_registry():
    reg = ToolRegistry()

    async def submit_verdict(args, ctx):
        return "verdict recorded"

    reg.register("submit_verdict", "Submit the verdict.",
                 {"type": "object", "properties": {"passed": {"type": "boolean"}}, "required": ["passed"]},
                 submit_verdict)
    return reg


def gateway(client, **budget):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    gw = Gateway(client, RunBudget(**budget) if budget else None, sleep=fake_sleep)
    gw.slept = slept
    return gw


# ---- run_agent -----------------------------------------------------------

async def test_completed_call_returns_ok_result_and_sends_expected_request():
    client = FakeClient(interaction(text="4", tokens=250, env="envX", id="iX"))
    result = await gateway(client).run_agent(SPEC, "do it", system_instruction="be careful", environment="remote")

    assert result.ok and result.status == "completed"
    assert (result.output_text, result.tokens, result.environment_id, result.interaction_id) == ("4", 250, "envX", "iX")
    sent = client.calls[0]
    assert sent["agent"] == AGENT_ID and sent["input"] == "do it" and sent["system_instruction"] == "be careful"
    assert sent["agent_config"] == {"type": "antigravity", "model": SPEC.model, "max_total_tokens": RunBudget().max_total_tokens}
    assert sent["tools"] == [{"type": "code_execution"}]  # explicit: no google_search / url_context


async def test_function_tool_round_trip_runs_the_handler_and_replies_with_function_result():
    events = []
    client = FakeClient(
        interaction(status="requires_action", id="i1", env="envA", tokens=100,
                    steps=[fn_call("submit_verdict", {"passed": False}, id="call_1")]),
        interaction(status="completed", id="i2", env="envA", text="done", tokens=50),
    )
    result = await gateway(client).run_agent(
        SPEC, "judge", system_instruction="s", tools=verdict_registry(),
        environment={"type": "remote", "sources": []}, on_event=lambda kind, payload: events.append((kind, payload)),
    )

    assert result.ok and result.output_text == "done" and result.tokens == 150
    assert result.tool_call("submit_verdict") == {"name": "submit_verdict", "arguments": {"passed": False}, "result": "verdict recorded"}
    assert [k for k, _ in events] == ["tool_called", "tool_returned"]
    assert events[0][1] == {"tool": "submit_verdict", "arguments": {"passed": False}}

    first, second = client.calls
    assert {"type": "code_execution"} in first["tools"] and any(t.get("name") == "submit_verdict" for t in first["tools"])
    assert second["previous_interaction_id"] == "i1" and second["environment"] == "envA"
    assert second["input"] == [{"type": "function_result", "name": "submit_verdict", "call_id": "call_1", "result": {"output": "verdict recorded"}}]
    assert "agent_config" not in second  # continuations are proven without it (Gateway.followup_agent_config)


async def test_followup_agent_config_flag(monkeypatch):
    monkeypatch.setattr(Gateway, "followup_agent_config", True)
    client = FakeClient(interaction(status="requires_action", steps=[fn_call("submit_verdict", {"passed": True})]),
                        interaction(text="ok"))
    await gateway(client).run_agent(SPEC, "p", system_instruction="s", tools=verdict_registry())
    assert client.calls[1]["agent_config"]["model"] == SPEC.model


async def test_multiple_function_calls_in_one_step_are_all_answered():
    client = FakeClient(
        interaction(status="requires_action", steps=[fn_call("submit_verdict", {"passed": True}, id="a"),
                                                     fn_call("submit_verdict", {"passed": False}, id="b")]),
        interaction(text="ok"),
    )
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s", tools=verdict_registry())
    assert [i["call_id"] for i in client.calls[1]["input"]] == ["a", "b"]
    assert len(result.tool_calls) == 2


async def test_requires_action_without_tools_is_an_error_result_not_a_raise():
    client = FakeClient(interaction(status="requires_action", steps=[fn_call("x", {})]))
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s")
    assert not result.ok and result.status == "error" and "unhandled requires_action" in result.error


async def test_tool_round_limit_stops_a_runaway_agent():
    looping = [interaction(status="requires_action", steps=[fn_call("submit_verdict", {"passed": True})]) for _ in range(5)]
    client = FakeClient(*looping)
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s", tools=verdict_registry(), max_tool_rounds=2)
    assert result.status == "error" and len(client.calls) == 3  # first call + 2 follow-ups


async def test_unknown_tool_call_degrades_to_an_error_string_the_agent_can_see():
    client = FakeClient(interaction(status="requires_action", steps=[fn_call("nope", {})]), interaction(text="ok"))
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s", tools=verdict_registry())
    assert result.ok and result.tool_calls[0]["result"] == "error: unknown tool 'nope'"


async def test_incomplete_status_means_token_budget_exhausted_and_is_not_ok():
    client = FakeClient(interaction(status="incomplete", tokens=120_000))
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s")
    assert result.status == "incomplete" and not result.ok and result.tokens == 120_000


async def test_timeout_is_reported_not_raised():
    def slow_create(**kwargs):
        time.sleep(0.4)
        return interaction()

    client = SimpleNamespace(interactions=SimpleNamespace(create=slow_create))
    result = await gateway(client, interaction_timeout_s=0.05).run_agent(SPEC, "p", system_instruction="s")
    assert result.status == "timeout" and not result.ok and "exceeded" in result.error


# ---- retries + key hygiene ------------------------------------------------

async def test_retries_429_with_backoff_then_succeeds():
    client = FakeClient(ApiError(429), ApiError(503), interaction(text="ok"))
    gw = gateway(client)
    result = await gw.run_agent(SPEC, "p", system_instruction="s")
    assert result.ok and len(client.calls) == 3 and gw.slept == [2.0, 4.0]


async def test_gives_up_after_bounded_retries():
    client = FakeClient(ApiError(503), ApiError(503), ApiError(503))
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s")
    assert result.status == "error" and len(client.calls) == 3 and "ApiError" in result.error


async def test_non_retryable_error_fails_fast():
    client = FakeClient(ApiError(400, "bad request"))
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s")
    assert result.status == "error" and len(client.calls) == 1 and "bad request" in result.error


async def test_api_key_never_appears_in_error_text(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-secret-123")
    client = FakeClient(RuntimeError("request rejected for key sk-secret-123"))
    result = await gateway(client).run_agent(SPEC, "p", system_instruction="s")
    assert "sk-secret-123" not in result.error and "***" in result.error


# ---- run_model -------------------------------------------------------------

class Plan(BaseModel):
    steps: list[str]


async def test_run_model_returns_a_validated_object_and_sends_a_json_schema():
    client = FakeClient(interaction(id="p1", text='{"steps": ["a", "b"]}', tokens=40))
    result = await gateway(client).run_model("gemini-3.8-flash", "plan it", Plan, system_instruction="you plan")
    assert result.ok and result.value == Plan(steps=["a", "b"]) and result.interaction_id == "p1" and result.tokens == 40
    sent = client.calls[0]
    assert sent["model"] == "gemini-3.8-flash" and "agent" not in sent and sent["system_instruction"] == "you plan"
    assert sent["response_format"] == {"type": "text", "mime_type": "application/json", "schema": Plan.model_json_schema()}
    assert "previous_interaction_id" not in sent


async def test_run_model_chains_onto_a_previous_interaction():
    client = FakeClient(interaction(text='{"steps": []}'))
    await gateway(client).run_model("m", "reflect", Plan, previous_interaction_id="prev-1")
    assert client.calls[0]["previous_interaction_id"] == "prev-1"


async def test_run_model_retries_invalid_json_once_then_succeeds():
    client = FakeClient(interaction(text="not json", tokens=10), interaction(text='{"steps": ["x"]}', tokens=10))
    result = await gateway(client).run_model("m", "p", Plan)
    assert result.ok and result.tokens == 20 and len(client.calls) == 2


async def test_run_model_reports_persistently_invalid_output_instead_of_defaulting():
    client = FakeClient(interaction(text="nope"), interaction(text='{"wrong": 1}'))
    result = await gateway(client).run_model("m", "p", Plan)
    assert not result.ok and result.value is None and "invalid structured output" in result.error


async def test_run_model_api_failure_is_a_result_not_a_raise():
    client = FakeClient(ApiError(400, "bad schema"))
    result = await gateway(client).run_model("m", "p", Plan)
    assert not result.ok and "bad schema" in result.error


# ---- extract_workspace: snapshot tar safety ---------------------------------

def make_tar(entries):
    """entries: (name, kind, payload) with kind in file|dir|symlink|hardlink."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = payload if isinstance(payload, bytes) else payload.encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, payload
                tar.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, payload
                tar.addfile(info)
    return buffer.getvalue()


def test_extracts_text_files_under_workspace_with_relative_paths():
    tar = make_tar([("./workspace", "dir", None), ("./workspace/a.py", "file", "print(1)\n"),
                    ("./workspace/sub/b.txt", "file", "hi")])
    assert extract_workspace(tar) == {"a.py": "print(1)\n", "sub/b.txt": "hi"}


def test_ignores_unsafe_and_out_of_scope_members():
    tar = make_tar([
        ("./workspace/ok.py", "file", "ok"),
        ("/workspace/abs.py", "file", "absolute path"),
        ("../workspace/up.py", "file", "traversal"),
        ("workspace/../etc/passwd", "file", "traversal inside"),
        ("./etc/passwd", "file", "outside root"),
        ("./workspace/link.py", "symlink", "/etc/passwd"),
        ("./workspace/hard.py", "hardlink", "workspace/ok.py"),
        ("./workspace/.agents/AGENTS.md", "file", "hidden dir"),
        ("./workspace/.env", "file", "hidden file"),
        ("./workspace/__pycache__/x.pyc", "file", "cache"),
        ("./workspace/mod.pyc", "file", "compiled"),
        ("./workspace/blob.bin", "file", b"\xff\xfe\x00\x01"),
    ])
    assert extract_workspace(tar) == {"ok.py": "ok"}


def test_oversized_single_files_are_skipped_but_total_limit_is_an_error():
    tar = make_tar([("workspace/small.py", "file", "x"), ("workspace/huge.py", "file", "y" * 500)])
    assert extract_workspace(tar, max_file_bytes=100) == {"small.py": "x"}
    with pytest.raises(SnapshotError):
        extract_workspace(make_tar([("workspace/a.py", "file", "x" * 60), ("workspace/b.py", "file", "x" * 60)]),
                          max_total_bytes=100)


def test_too_many_files_is_an_error():
    tar = make_tar([(f"workspace/f{i}.py", "file", "x") for i in range(5)])
    with pytest.raises(SnapshotError):
        extract_workspace(tar, max_files=3)


def test_not_a_tar_is_a_snapshot_error():
    with pytest.raises(SnapshotError):
        extract_workspace(b"this is not a tar archive at all" * 50)


# ---- download_workspace ------------------------------------------------------

def download_gateway(handler, monkeypatch, key="test-key"):
    monkeypatch.setenv("GEMINI_API_KEY", key)
    return Gateway(FakeClient(), transport=httpx.MockTransport(handler))


async def test_download_sends_key_to_google_and_returns_the_workspace(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=make_tar([("./workspace/a.py", "file", "print(1)")]))

    files = await download_gateway(handler, monkeypatch).download_workspace("abc123")
    assert files == {"a.py": "print(1)"}
    assert seen[0].url.host == "generativelanguage.googleapis.com" and seen[0].url.params["alt"] == "media"
    assert seen[0].url.path.endswith("/files/environment-abc123:download")
    assert seen[0].headers["x-goog-api-key"] == "test-key"


async def test_download_never_forwards_the_key_to_a_non_google_redirect(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "https://storage.example.com/blob?sig=1"})
        return httpx.Response(200, content=make_tar([("workspace/a.py", "file", "x")]))

    files = await download_gateway(handler, monkeypatch).download_workspace("abc123")
    assert files == {"a.py": "x"}
    assert seen[0].headers.get("x-goog-api-key") == "test-key"
    assert seen[1].url.host == "storage.example.com" and "x-goog-api-key" not in seen[1].headers


async def test_download_keeps_the_key_on_a_google_redirect(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "https://storage.googleapis.com/blob?sig=1"})
        return httpx.Response(200, content=make_tar([("workspace/a.py", "file", "x")]))

    await download_gateway(handler, monkeypatch).download_workspace("abc123")
    assert seen[1].headers["x-goog-api-key"] == "test-key"


async def test_download_redirect_loop_is_bounded(monkeypatch):
    handler = lambda request: httpx.Response(302, headers={"location": "https://storage.googleapis.com/again"})  # noqa: E731
    with pytest.raises(SnapshotError, match="redirects"):
        await download_gateway(handler, monkeypatch).download_workspace("abc123")


async def test_download_http_error_is_a_snapshot_error(monkeypatch):
    with pytest.raises(SnapshotError, match="HTTP 404"):
        await download_gateway(lambda r: httpx.Response(404), monkeypatch).download_workspace("abc123")


async def test_download_rejects_bad_ids_and_missing_key_before_any_request(monkeypatch):
    calls = []
    gw = download_gateway(lambda r: calls.append(r) or httpx.Response(200), monkeypatch)
    for bad in ("", "../x", "a/b", "a b", "x" * 200, None):
        with pytest.raises(SnapshotError):
            await gw.download_workspace(bad)
    keyless = Gateway(FakeClient(), keys=KeyRing(), transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)))
    with pytest.raises(SnapshotError, match="no API key"):
        await keyless.download_workspace("abc123")
    assert calls == []


async def test_download_oversize_body_is_rejected(monkeypatch):
    monkeypatch.setattr(interactions, "MAX_SNAPSHOT_BYTES", 100)
    handler = lambda r: httpx.Response(200, content=b"x" * 5000)  # noqa: E731
    with pytest.raises(SnapshotError, match="size cap"):
        await download_gateway(handler, monkeypatch).download_workspace("abc123")


# ---- per-role keys and failover ----------------------------------------------

def key_ring():
    return KeyRing(by_role={"orchestrator": "kA", "coder": "kB", "critic": "kC"}, spares=["kD", "kE"])


def keyed_gateway(clients, ring=None, **budget):
    """A Gateway whose SDK client is chosen per key: clients maps key -> FakeClient."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    gw = Gateway(client_factory=lambda key: clients[key], keys=ring or key_ring(),
                 budget=RunBudget(**budget) if budget else None, sleep=fake_sleep)
    gw.slept = slept
    return gw


CODER = AgentSpec(role="coder", model="gemini-3.5-flash-lite")
CRITIC = AgentSpec(role="critic", model="gemini-3.8-flash")


async def test_each_role_runs_on_its_own_dedicated_key_and_reports_a_label_not_the_key():
    clients = {
        "kA": FakeClient(interaction(text='{"steps": ["a"]}')),
        "kB": FakeClient(interaction(text="coder-out")),
        "kC": FakeClient(interaction(text="critic-out")),
    }
    gw = keyed_gateway(clients)
    coder = await gw.run_agent(CODER, "p", system_instruction="s")
    critic = await gw.run_agent(CRITIC, "p", system_instruction="s")
    plan = await gw.run_model("m", "p", Plan)

    assert (coder.output_text, critic.output_text, plan.ok) == ("coder-out", "critic-out", True)  # coder -> kB, critic -> kC, plan -> kA
    assert all(len(c.calls) == 1 for c in clients.values())
    assert (coder.key, critic.key, plan.key) == ("k2", "k3", "k1")
    assert "kB" not in repr(coder) and "kC" not in repr(critic)  # results carry labels, never key material


async def test_429_on_a_fresh_call_fails_over_to_a_spare_immediately_and_sticks():
    clients = {"kB": FakeClient(ApiError(429, "quota")), "kD": FakeClient(interaction(text="ok"), interaction(text="again"))}
    ring = key_ring()
    gw = keyed_gateway(clients, ring)
    first = await gw.run_agent(CODER, "p", system_instruction="s")

    assert first.ok and first.output_text == "ok" and first.key == "k4"
    assert gw.failovers == [("coder", "k2", "k4")] and gw.slept == []  # no backoff sleep on failover
    assert ring.key_for("coder") == "kD" and "kB" in ring.spares  # exhausted key recycled to the back
    second = await gw.run_agent(CODER, "p2", system_instruction="s")
    assert second.key == "k4" and len(clients["kD"].calls) == 2  # sticky for the rest of the run


async def test_429_on_a_call_bound_to_an_existing_environment_does_not_fail_over():
    clients = {"kB": FakeClient(ApiError(429), ApiError(429), ApiError(429))}
    gw = keyed_gateway(clients)
    result = await gw.run_agent(CODER, "p", system_instruction="s", environment="env-abc123")  # an env id, not "remote"

    assert result.status == "error" and gw.failovers == []
    assert gw.keys.key_for("coder") == "kB" and gw.keys.spares == ["kD", "kE"]  # nothing rotated
    assert len(clients["kB"].calls) == 3  # ordinary bounded backoff instead


async def test_a_function_tool_chain_stays_on_the_key_that_started_it():
    clients = {"kC": FakeClient(interaction(status="requires_action", steps=[fn_call("submit_verdict", {"passed": True})]),
                                interaction(text="done"))}
    gw = keyed_gateway(clients)
    result = await gw.run_agent(CRITIC, "judge", system_instruction="s", tools=verdict_registry())
    assert result.ok and len(clients["kC"].calls) == 2 and gw.failovers == []


async def test_failover_during_a_critic_first_call_carries_the_followup_to_the_new_key():
    clients = {
        "kC": FakeClient(ApiError(429)),
        "kD": FakeClient(interaction(status="requires_action", steps=[fn_call("submit_verdict", {"passed": True})]), interaction(text="done")),
    }
    gw = keyed_gateway(clients)
    result = await gw.run_agent(CRITIC, "judge", system_instruction="s", tools=verdict_registry(),
                                environment={"type": "remote", "sources": []})
    assert result.ok and result.key == "k4" and len(clients["kD"].calls) == 2 and len(clients["kC"].calls) == 1


async def test_chained_orchestrator_calls_do_not_fail_over_but_fresh_ones_do():
    fresh = {"kA": FakeClient(ApiError(429)), "kD": FakeClient(interaction(text='{"steps": ["a"]}'))}
    assert (await keyed_gateway(fresh).run_model("m", "p", Plan)).ok

    chained = {"kA": FakeClient(ApiError(429), ApiError(429), ApiError(429))}
    gw = keyed_gateway(chained)
    result = await gw.run_model("m", "p", Plan, previous_interaction_id="prev-1")
    assert not result.ok and gw.failovers == []


async def test_a_pool_exhausted_everywhere_ends_in_a_bounded_error_not_a_loop():
    clients = {k: FakeClient(*[ApiError(429)] * 10) for k in ("kA", "kB", "kC", "kD", "kE")}
    gw = keyed_gateway(clients)
    result = await gw.run_agent(CODER, "p", system_instruction="s")
    assert result.status == "error" and len(gw.failovers) <= 5
    assert sum(len(c.calls) for c in clients.values()) <= 5 + 3


async def test_a_single_shared_key_has_nothing_to_rotate_to_so_429_is_ordinary_backoff():
    client = FakeClient(ApiError(429), interaction(text="ok"))
    gw = Gateway(client, keys=KeyRing.single("only"), sleep=lambda s: __import__("asyncio").sleep(0))
    result = await gw.run_agent(CODER, "p", system_instruction="s")
    assert result.ok and gw.failovers == [] and len(client.calls) == 2


async def test_snapshot_download_uses_the_coders_current_key_including_after_failover(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-goog-api-key"))
        return httpx.Response(200, content=make_tar([("workspace/a.py", "file", "x")]))

    ring = key_ring()
    gw = Gateway(FakeClient(), keys=ring, transport=httpx.MockTransport(handler))
    await gw.download_workspace("abc123")
    ring.rotate("coder")
    await gw.download_workspace("abc123")
    await gw.download_workspace("abc123", role="critic")
    assert seen == ["kB", "kD", "kC"]


async def test_every_configured_key_is_scrubbed_from_error_text():
    ring = KeyRing(by_role={"orchestrator": "AQ.aaa111", "coder": "AQ.bbb222", "critic": "AQ.ccc333"}, spares=["AQ.ddd444"])
    clients = {"AQ.bbb222": FakeClient(RuntimeError("rejected AQ.bbb222 (also tried AQ.ddd444 and AQ.aaa111)"))}
    result = await keyed_gateway(clients, ring).run_agent(CODER, "p", system_instruction="s")
    assert "AQ." not in result.error and result.error.count("***") == 3


# ---- snapshot download retries ----------------------------------------------------

def retrying_gateway(handler, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    gw = Gateway(FakeClient(), transport=httpx.MockTransport(handler), sleep=fake_sleep)
    gw.slept = slept
    return gw


async def test_download_retries_a_read_timeout_then_succeeds(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, content=make_tar([("workspace/a.py", "file", "x")]))

    gw = retrying_gateway(handler, monkeypatch)
    assert await gw.download_workspace("abc123") == {"a.py": "x"}
    assert len(calls) == 2 and gw.slept == [1.5]


async def test_download_retries_5xx_and_gives_up_after_three_tries(monkeypatch):
    calls = []
    gw = retrying_gateway(lambda r: calls.append(r) or httpx.Response(503), monkeypatch)
    with pytest.raises(SnapshotError, match="HTTP 503"):
        await gw.download_workspace("abc123")
    assert len(calls) == 3 and gw.slept == [1.5, 3.0]


async def test_download_does_not_retry_a_permanent_failure(monkeypatch):
    calls = []
    gw = retrying_gateway(lambda r: calls.append(r) or httpx.Response(404), monkeypatch)
    with pytest.raises(SnapshotError, match="HTTP 404"):
        await gw.download_workspace("abc123")
    assert len(calls) == 1 and gw.slept == []
