"""The ONLY module that touches the google-genai SDK and Google's environment-download endpoint.

Everything else (orchestrator, agents) talks to a `Gateway`, which tests replace with a fake client.

Verified live (scripts/smoke_test.py, 2026-09-26):
- Antigravity agent call: create(agent=AGENT_ID, environment=..., tools=[...], agent_config={type,model,max_total_tokens}).
- A custom function tool returns status "requires_action" with steps of type "function_call"
  (id, name, arguments as a parsed dict); reply with input=[{type: function_result, name, call_id, result}]
  + previous_interaction_id + environment.
- Explicit tools=[{"type": "code_execution"}, <function schema>] works together (and is ~half the token
  baseline of the default tool set).
- A file written in one interaction is readable by another that shares the environment_id.
- GET .../files/environment-{id}:download?alt=media returns a tar with ./workspace/... members.
Documented, run in the smoke test's check 4d: plain model= + response_format (structured JSON).

Contracts:
- run_agent / run_model NEVER raise. SDK errors, timeouts, an exhausted token budget ("incomplete"), and
  unhandled requires_action all come back as a result with a non-ok status, because the orchestrator's whole
  job is to recover from those.
- download_workspace raises SnapshotError (callers turn it into a failed attempt).
- The API key is never logged: error text is scrubbed before it leaves this module.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Generic, List, Optional, Sequence, Type, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.config import AGENT_ID, RunBudget
from app.keys import KeyRing
from app.routing import AgentSpec
from app.tools import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
EventFn = Callable[[str, Dict[str, Any]], None]  # (state-log kind, payload), bound to step/attempt by the caller

_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3  # first try + 2 retries, exponential backoff (rate limits are unpublished)
_DOWNLOAD_URL = "https://generativelanguage.googleapis.com/v1beta/files/environment-{env_id}:download"
_ENV_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_REDIRECTS = 3

MAX_SNAPSHOT_BYTES = 25_000_000


class SnapshotError(Exception):
    """The environment snapshot could not be downloaded or safely read.

    `retryable` marks transient failures (transport errors, 429/5xx) that download_workspace retries itself.
    """

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class InteractionTimeout(Exception):
    """One interaction exceeded RunBudget.interaction_timeout_s."""


@dataclass
class AgentResult:
    status: str  # "completed" | "incomplete" | "requires_action" | "timeout" | "error" | whatever the API says
    interaction_id: Optional[str] = None
    environment_id: Optional[str] = None  # reuse for the next attempt so the coder's files persist
    output_text: str = ""
    tokens: int = 0  # summed usage.total_tokens across every round of this call
    elapsed_s: float = 0.0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # {"name", "arguments", "result"}
    error: Optional[str] = None
    key: Optional[str] = None  # non-secret label (k1, k2, ...) of the API key this call ran on

    @property
    def ok(self) -> bool:
        return self.status == "completed" and self.error is None

    def tool_call(self, name: str) -> Optional[Dict[str, Any]]:
        """The last executed call to `name` (e.g. the critic's submit_verdict), if any."""
        for call in reversed(self.tool_calls):
            if call["name"] == name:
                return call
        return None


@dataclass
class ModelResult(Generic[T]):
    ok: bool
    value: Optional[T] = None
    interaction_id: Optional[str] = None  # chain the next orchestrator call onto this
    tokens: int = 0
    elapsed_s: float = 0.0
    error: Optional[str] = None
    key: Optional[str] = None  # non-secret label of the API key this call ran on


# ---------------------------------------------------------------- helpers

def _describe(e: BaseException, ring: Optional[KeyRing] = None) -> str:
    """One-line error text with every known API key scrubbed out (the ring's and the environment's)."""
    text = f"{type(e).__name__}: {e}"
    for known in (ring, KeyRing.from_env()):
        if known is not None:
            text = known.scrub(text)
    return text[:400]


def _status_code(e: BaseException) -> Optional[int]:
    for attr in ("code", "status_code"):
        value = getattr(e, attr, None)
        if isinstance(value, int):
            return value
    return None


def _retryable(e: BaseException) -> bool:
    return _status_code(e) in _RETRYABLE_CODES or isinstance(e, (httpx.TransportError, ConnectionError))


def _tokens(interaction: Any) -> int:
    usage = getattr(interaction, "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0)


def _function_calls(interaction: Any) -> List[Any]:
    return [s for s in (getattr(interaction, "steps", None) or []) if getattr(s, "type", None) == "function_call"]


def _safe_rel(name: str) -> Optional[str]:
    """Normalise a tar member name to a relative posix path, or None if it is unsafe."""
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def extract_workspace(
    tar_bytes: bytes,
    *,
    root: str = "workspace",
    max_files: int = 50,
    max_file_bytes: int = 256_000,
    max_total_bytes: int = 2_000_000,
) -> Dict[str, str]:
    """Read the text files under `root/` from an environment snapshot, entirely in memory.

    Deliberately NOT tar.extractall: nothing is written to disk, and only regular files are read, so
    symlinks, hardlinks, devices, absolute paths and `..` traversal are ignored/rejected by construction.
    Hidden files/dirs, __pycache__ and non-UTF-8 (binary) files are skipped. Returns {relative path: text}.
    """
    files: Dict[str, str] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            for member in tar:
                if not member.isreg():
                    continue
                rel = _safe_rel(member.name)
                if rel is None or not rel.startswith(root + "/"):
                    continue
                sub = rel[len(root) + 1:]
                parts = sub.split("/")
                if any(p.startswith(".") or p == "__pycache__" for p in parts) or sub.endswith(".pyc"):
                    continue
                if member.size > max_file_bytes:
                    logger.warning("interactions: skipping oversized snapshot file %s (%d bytes)", sub, member.size)
                    continue
                total += member.size
                if total > max_total_bytes or len(files) >= max_files:
                    raise SnapshotError(f"workspace snapshot exceeds limits ({max_files} files / {max_total_bytes} bytes)")
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                try:
                    files[sub] = handle.read().decode("utf-8")
                except UnicodeDecodeError:
                    continue
    except tarfile.TarError as e:
        raise SnapshotError(f"snapshot is not a readable tar: {e}") from e
    return files


# ---------------------------------------------------------------- gateway

class Gateway:
    """One place for SDK calls, retries, timeouts, token accounting, and snapshot download.

    `client` is a google-genai Client (or a test fake exposing .interactions.create); it is built lazily so
    importing this module needs neither the SDK nor a key. `transport` lets tests fake the download endpoint.
    """

    # Whether continuations (function_result follow-ups) also carry agent_config. Continuations are proven
    # without it; flip only if scripts/smoke_test.py check 4c shows agent_config is accepted there.
    followup_agent_config = False

    def __init__(
        self,
        client: Any = None,
        budget: Optional[RunBudget] = None,
        *,
        keys: Optional[KeyRing] = None,
        client_factory: Optional[Callable[[str], Any]] = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        """`client` (one fake for every key) and `client_factory` (key -> client) exist for tests; in production
        a google-genai Client is built lazily per key. `keys` defaults to KeyRing.from_env()."""
        self._client = client
        self._client_factory = client_factory
        self._clients: Dict[str, Any] = {}
        self.keys = keys if keys is not None else KeyRing.from_env()
        self.failovers: List[tuple] = []  # (role, from_label, to_label), for logs and tests
        self.budget = budget or RunBudget()
        self._sleep = sleep
        self._transport = transport

    def _sdk(self, key: Optional[str]) -> Any:
        if self._client is not None:
            return self._client
        if not key:
            raise RuntimeError("no API key configured for this call")
        if key not in self._clients:
            if self._client_factory is not None:
                self._clients[key] = self._client_factory(key)
            else:
                from google import genai

                self._clients[key] = genai.Client(api_key=key)
        return self._clients[key]

    async def _create(self, role: str, key: Optional[str], allow_failover: bool, **kwargs: Any) -> tuple:
        """interactions.create in a worker thread -> (interaction, key_used).

        Hard timeout; bounded retry with backoff on 429/5xx/transport errors. On a 429 with `allow_failover` the
        role is moved to a spare key immediately (no sleep) -- allowed only for a fresh call, because chains and
        environments belong to the key's project. Failovers per call are capped at the number of keys, so a pool
        that is exhausted everywhere falls through to normal backoff instead of ping-ponging.
        """
        delay = 2.0
        attempt = 0
        failovers_left = len(self.keys.all_keys())
        while True:
            attempt += 1
            try:
                interaction = await asyncio.wait_for(
                    asyncio.to_thread(self._sdk(key).interactions.create, **kwargs),
                    timeout=self.budget.interaction_timeout_s,
                )
                return interaction, key
            except asyncio.TimeoutError as e:
                raise InteractionTimeout(f"interaction exceeded {self.budget.interaction_timeout_s:.0f}s") from e
            except Exception as e:  # noqa: BLE001
                if _status_code(e) == 429 and allow_failover and failovers_left > 0:
                    failovers_left -= 1
                    new_key = self.keys.rotate(role)
                    if new_key:
                        self.failovers.append((role, self.keys.label_of(key), self.keys.label_of(new_key)))
                        logger.warning("interactions: %s key %s hit its limit; failing over to %s",
                                       role, self.keys.label_of(key), self.keys.label_of(new_key))
                        key = new_key
                        attempt -= 1  # a failover is not a backoff attempt
                        continue
                if attempt < _MAX_ATTEMPTS and _retryable(e):
                    logger.warning("interactions: retryable error (attempt %d/%d): %s", attempt, _MAX_ATTEMPTS, _describe(e, self.keys))
                    await self._sleep(delay)
                    delay *= 2
                    continue
                raise

    # ---- Antigravity agent (coder / critic) ----

    async def run_agent(
        self,
        spec: AgentSpec,
        prompt: str,
        *,
        system_instruction: str,
        tools: Optional[ToolRegistry] = None,
        builtin_tools: Sequence[str] = ("code_execution",),
        environment: Any = "remote",  # "remote" | env id | {"type": "remote", "sources": [...]}
        on_event: Optional[EventFn] = None,
        tool_ctx: Optional[ToolContext] = None,
        max_tool_rounds: int = 4,
    ) -> AgentResult:
        """One Antigravity interaction, including any function-tool round trips. Never raises."""
        t0 = time.monotonic()
        result = AgentResult(status="error")
        emit = on_event or (lambda kind, payload: None)
        ctx = tool_ctx or ToolContext(workspace_root=Path("."))
        agent_config = {"type": "antigravity", "model": spec.model, "max_total_tokens": self.budget.max_total_tokens}
        # Explicit tool list: no google_search / url_context for coder or critic, and a smaller harness prompt.
        tool_defs: List[Dict[str, Any]] = [{"type": name} for name in builtin_tools]
        if tools is not None:
            tool_defs += tools.interactions_tools_schema()
        role = spec.role
        key = self.keys.key_for(role)
        result.key = self.keys.label_of(key)
        fresh = isinstance(environment, dict) or environment == "remote"  # an env id belongs to one key's project
        try:
            interaction, key = await self._create(
                role, key, fresh,
                agent=AGENT_ID, input=prompt, system_instruction=system_instruction,
                environment=environment, tools=tool_defs, agent_config=agent_config,
            )
            result.key = self.keys.label_of(key)
            self._absorb(result, interaction)
            rounds = 0
            while result.status == "requires_action":
                calls = _function_calls(interaction)
                rounds += 1
                if tools is None or not calls or rounds > max_tool_rounds:
                    result.status = "error"
                    result.error = "unhandled requires_action (no tools registered, no function call, or round limit)"
                    break
                outputs = await self._execute_calls(calls, tools, ctx, emit, result)
                followup: Dict[str, Any] = dict(
                    agent=AGENT_ID, previous_interaction_id=interaction.id,
                    environment=getattr(interaction, "environment_id", None) or environment, input=outputs,
                )
                if self.followup_agent_config:
                    followup["agent_config"] = agent_config
                interaction, key = await self._create(role, key, False, **followup)  # a chain stays on its key
                self._absorb(result, interaction)
        except InteractionTimeout as e:
            result.status, result.error = "timeout", str(e)
        except Exception as e:  # noqa: BLE001 -- the never-raises contract
            result.status, result.error = "error", _describe(e, self.keys)
        result.elapsed_s = round(time.monotonic() - t0, 2)
        return result

    @staticmethod
    def _absorb(result: AgentResult, interaction: Any) -> None:
        result.status = str(getattr(interaction, "status", None) or "unknown")
        result.interaction_id = getattr(interaction, "id", None) or result.interaction_id
        result.environment_id = getattr(interaction, "environment_id", None) or result.environment_id
        result.output_text = getattr(interaction, "output_text", None) or result.output_text
        result.tokens += _tokens(interaction)

    @staticmethod
    async def _execute_calls(
        calls: List[Any], tools: ToolRegistry, ctx: ToolContext, emit: EventFn, result: AgentResult,
    ) -> List[Dict[str, Any]]:
        outputs: List[Dict[str, Any]] = []
        for step in calls:
            name = getattr(step, "name", "") or ""
            args = getattr(step, "arguments", None)
            emit("tool_called", {"tool": name, "arguments": args})
            out = await tools.call(name, args if args is not None else {}, ctx)
            emit("tool_returned", {"tool": name, "result": out})
            result.tool_calls.append({"name": name, "arguments": args, "result": out})
            outputs.append({"type": "function_result", "name": name, "call_id": getattr(step, "id", None), "result": {"output": out}})
        return outputs

    # ---- plain model, structured output (orchestrator planning / reflection) ----

    async def run_model(
        self,
        model: str,
        prompt: str,
        schema: Type[T],
        *,
        system_instruction: Optional[str] = None,
        previous_interaction_id: Optional[str] = None,
        attempts: int = 2,
    ) -> ModelResult[T]:
        """A plain-model interaction returning a validated pydantic object. Never raises.

        `previous_interaction_id` chains onto an earlier orchestrator call (server-side state). Invalid JSON
        is retried once, then reported as ok=False with the validation error -- never silently defaulted.
        """
        t0 = time.monotonic()
        kwargs: Dict[str, Any] = dict(
            model=model, input=prompt,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema.model_json_schema()},
        )
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if previous_interaction_id:
            kwargs["previous_interaction_id"] = previous_interaction_id
        tokens = 0
        error: Optional[str] = None
        key = self.keys.key_for("orchestrator")
        fresh = previous_interaction_id is None  # a chained call must stay on the key that owns the chain
        for _ in range(max(1, attempts)):
            try:
                interaction, key = await self._create("orchestrator", key, fresh, **kwargs)
            except InteractionTimeout as e:
                return ModelResult(ok=False, tokens=tokens, elapsed_s=round(time.monotonic() - t0, 2), error=str(e),
                                   key=self.keys.label_of(key))
            except Exception as e:  # noqa: BLE001
                return ModelResult(ok=False, tokens=tokens, elapsed_s=round(time.monotonic() - t0, 2),
                                   error=_describe(e, self.keys), key=self.keys.label_of(key))
            tokens += _tokens(interaction)
            try:
                value = schema.model_validate_json(getattr(interaction, "output_text", None) or "")
            except ValueError as e:  # pydantic.ValidationError is a ValueError
                error = f"invalid structured output: {str(e)[:300]}"
                continue
            return ModelResult(
                ok=True, value=value, interaction_id=getattr(interaction, "id", None),
                tokens=tokens, elapsed_s=round(time.monotonic() - t0, 2), key=self.keys.label_of(key),
            )
        return ModelResult(ok=False, tokens=tokens, elapsed_s=round(time.monotonic() - t0, 2), error=error,
                           key=self.keys.label_of(key))

    # ---- environment snapshot ----

    async def download_workspace(self, environment_id: str, *, role: str = "coder", **limits: Any) -> Dict[str, str]:
        """Download an environment's snapshot (see _download_once), retrying transient failures.

        A ReadTimeout on the download cost a whole coder attempt in a live run, so transport errors and 429/5xx
        are retried up to 3 times with a short backoff before the failure is reported.
        """
        for attempt in range(1, 4):
            try:
                return await self._download_once(environment_id, role=role, **limits)
            except SnapshotError as e:
                if e.retryable and attempt < 3:
                    logger.warning("interactions: snapshot download failed (attempt %d/3): %s", attempt, e)
                    await self._sleep(1.5 * attempt)
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _download_once(self, environment_id: str, *, role: str = "coder", **limits: Any) -> Dict[str, str]:
        """Download an environment's snapshot tar and return its workspace text files, in memory.

        Uses `role`'s current key: an environment is only readable with the key (project) that created it, and the
        coder's key is sticky once its environment exists. The key goes only to *.googleapis.com: redirects are
        followed manually and the key header is dropped on any other host, so a redirect can never carry it off.
        """
        if not isinstance(environment_id, str) or not _ENV_ID_RE.match(environment_id):
            raise SnapshotError("invalid environment id")
        key = self.keys.key_for(role)
        if not key:
            raise SnapshotError("no API key configured")
        url = _DOWNLOAD_URL.format(env_id=environment_id)
        params: Optional[Dict[str, str]] = {"alt": "media"}
        try:
            async with httpx.AsyncClient(timeout=45.0, follow_redirects=False, transport=self._transport) as http:
                for _ in range(_MAX_REDIRECTS + 1):
                    host = urlparse(url).hostname or ""
                    headers = {"x-goog-api-key": key} if host == "googleapis.com" or host.endswith(".googleapis.com") else {}
                    async with http.stream("GET", url, params=params, headers=headers) as response:
                        if response.is_redirect and response.headers.get("location"):
                            url = str(response.url.join(response.headers["location"]))
                            params = None  # the redirect target carries its own query
                            continue
                        if response.status_code != 200:
                            raise SnapshotError(f"snapshot download failed: HTTP {response.status_code}",
                                                retryable=response.status_code in _RETRYABLE_CODES)
                        buffer = bytearray()
                        async for chunk in response.aiter_bytes():
                            buffer += chunk
                            if len(buffer) > MAX_SNAPSHOT_BYTES:
                                raise SnapshotError("snapshot exceeds size cap")
                        return extract_workspace(bytes(buffer), **limits)
                raise SnapshotError("too many redirects")
        except httpx.HTTPError as e:
            raise SnapshotError(_describe(e, self.keys), retryable=True) from e
