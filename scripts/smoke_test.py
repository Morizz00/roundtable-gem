"""Smoke test for the Antigravity Interactions API.

Resolves the open questions that block the orchestrator:
  1. Does a basic interaction on the Antigravity agent work at all?
  2. Function-tool round trip: flat vs nested schema, and the requires_action ->
     function_result -> previous_interaction_id flow.
  3. Environment reuse: can a second interaction (the critic) read a file the
     first (the coder) wrote, by passing the same environment id? If not, the
     inline-`sources` fallback is checked.

Run (needs GEMINI_API_KEY in the environment or .env):
    python scripts/smoke_test.py            # all checks
    python scripts/smoke_test.py --only 2   # one check

It prints raw shapes (steps, usage) so the real field names are visible; it
never prints the API key. Each check is independent and never aborts the rest.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AGENT_ID, MODEL_LADDER, api_key  # noqa: E402
from app.tools import ToolContext, ToolRegistry  # noqa: E402

AGENT_CFG: Dict[str, Any] = {"type": "antigravity", "model": MODEL_LADDER[0], "max_total_tokens": 20_000}


def dump(obj: Any, limit: int = 1200) -> str:
    """Best-effort readable dump of an SDK object, truncated."""
    for attr in ("model_dump", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                data = fn(mode="json") if attr == "model_dump" else fn()
                text = json.dumps(data, indent=2, default=str)
                return text if len(text) <= limit else text[:limit] + " ...[truncated]"
            except Exception:  # noqa: BLE001
                pass
    text = repr(obj)
    return text if len(text) <= limit else text[:limit] + " ...[truncated]"


def make_client() -> Any:
    from google import genai

    key = api_key()
    if not key:
        sys.exit("GEMINI_API_KEY is not set (put it in .env or the environment).")
    return genai.Client(api_key=key)


STRONG = MODEL_LADDER[-1]
TIMINGS: List[Dict[str, Any]] = []  # one row per successful call: wall seconds + total tokens


def _timed(kind: str, **kwargs: Any) -> Any:
    client = kwargs.pop("_client")
    t0 = time.monotonic()
    result = client.interactions.create(**kwargs)
    usage = getattr(result, "usage", None)
    TIMINGS.append({
        "call": len(TIMINGS) + 1, "kind": kind, "seconds": round(time.monotonic() - t0, 1),
        "tokens": getattr(usage, "total_tokens", None) if usage is not None else None,
    })
    return result


def create(client: Any, **kwargs: Any) -> Any:
    """An Antigravity agent interaction."""
    return _timed("agent", _client=client, agent=AGENT_ID, **kwargs)


def create_model(client: Any, model: str, **kwargs: Any) -> Any:
    """A plain-model interaction (no sandbox) on the same Interactions API."""
    return _timed("model", _client=client, model=model, **kwargs)


def summarize(label: str, interaction: Any) -> None:
    print(f"  [{label}] status={getattr(interaction, 'status', None)!r} "
          f"id={getattr(interaction, 'id', None)!r} "
          f"environment_id={getattr(interaction, 'environment_id', None)!r}")
    text = getattr(interaction, "output_text", None)
    print(f"  [{label}] output_text={(text or '')[:300]!r}")
    usage = getattr(interaction, "usage", None)
    if usage is not None:
        print(f"  [{label}] usage={dump(usage, 400)}")


def function_calls(interaction: Any) -> List[Any]:
    return [s for s in (getattr(interaction, "steps", None) or []) if getattr(s, "type", None) == "function_call"]


# ---------------------------------------------------------------- check 1
def check_basic(client: Any) -> bool:
    print("\n== 1. basic interaction ==")
    try:
        i = create(
            client,
            input="Use code execution to compute 2+2 in Python. Reply with just the number.",
            environment="remote",
            agent_config=AGENT_CFG,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL: {type(e).__name__}: {str(e)[:500]}")
        return False
    summarize("basic", i)
    ok = "4" in (getattr(i, "output_text", "") or "")
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- check 2
def _registry() -> ToolRegistry:
    async def add_numbers(args: Dict[str, Any], ctx: ToolContext) -> str:
        return str(float(args["a"]) + float(args["b"]))

    reg = ToolRegistry()
    reg.register(
        "add_numbers",
        "Adds two numbers and returns the sum.",
        {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        add_numbers,
    )
    return reg


def check_function_roundtrip(client: Any) -> Optional[str]:
    """Returns the schema style that worked ('flat' | 'nested'), or None."""
    print("\n== 2. function-tool round trip ==")
    reg = _registry()
    ctx = ToolContext(workspace_root=Path("."))
    for style in ("flat", "nested"):
        print(f" -- trying {style} schema")
        try:
            i = create(
                client,
                input="Use the add_numbers tool to add 17 and 25, then tell me the result.",
                environment="remote",
                tools=reg.interactions_tools_schema(style),
                agent_config=AGENT_CFG,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  {style}: create failed: {type(e).__name__}: {str(e)[:500]}")
            continue
        summarize(style, i)
        calls = function_calls(i)
        if getattr(i, "status", None) != "requires_action" or not calls:
            print(f"  {style}: no function call came back (status/steps above) -- shape may differ")
            print(f"  steps={dump(getattr(i, 'steps', None), 1500)}")
            continue
        step = calls[0]
        print(f"  {style}: function_call step shape:\n{dump(step, 800)}")
        name = getattr(step, "name", None)
        args = getattr(step, "arguments", None)
        if args is None:
            args = getattr(step, "args", None)
        result = asyncio.run(reg.call(name or "", args if args is not None else {}, ctx))
        print(f"  {style}: local tool result={result!r}")
        try:
            final = create(
                client,
                previous_interaction_id=i.id,
                environment=getattr(i, "environment_id", None) or "remote",
                input=[{"type": "function_result", "name": name, "call_id": step.id, "result": {"sum": result}}],
            )
        except Exception as e:  # noqa: BLE001
            print(f"  {style}: function_result follow-up failed: {type(e).__name__}: {str(e)[:500]}")
            continue
        summarize(f"{style}-final", final)
        if "42" in (getattr(final, "output_text", "") or ""):
            print(f"  PASS ({style})")
            return style
        print(f"  {style}: follow-up completed but 42 not in output")
    print("  FAIL: neither schema style completed the round trip")
    return None


# ---------------------------------------------------------------- check 3
def check_env_reuse(client: Any) -> str:
    """Returns 'env-id' if reuse-by-id works, 'inline-sources' if only the
    fallback works, else 'none'."""
    print("\n== 3. environment reuse (coder -> critic handoff) ==")
    marker = "banana"
    path = "/workspace/handoff.txt"
    try:
        a = create(
            client,
            input=f"Use code execution to write the single word {marker} to {path}, then reply 'done'.",
            environment="remote",
            agent_config=AGENT_CFG,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  writer create failed: {type(e).__name__}: {str(e)[:500]}")
        return "none"
    summarize("writer", a)
    env_id = getattr(a, "environment_id", None)
    read_prompt = f"Read {path} with code execution and reply with its exact contents. If it does not exist reply MISSING."
    if env_id:
        try:
            b = create(client, input=read_prompt, environment=env_id, agent_config=AGENT_CFG)
            summarize("reader(env-id)", b)
            if marker in (getattr(b, "output_text", "") or ""):
                print("  PASS: same environment id preserves files -> pass env id coder -> critic")
                return "env-id"
            print("  env id did not carry the file over; trying inline sources fallback")
        except Exception as e:  # noqa: BLE001
            print(f"  reader(env-id) failed: {type(e).__name__}: {str(e)[:500]}")
    else:
        print("  writer returned no environment_id; trying inline sources fallback")
    try:
        c = create(
            client,
            input=read_prompt,
            environment={"type": "remote", "sources": [{"type": "inline", "target": path, "content": marker}]},
            agent_config=AGENT_CFG,
        )
        summarize("reader(inline)", c)
        if marker in (getattr(c, "output_text", "") or ""):
            print("  PASS (fallback): pass the coder's files to the critic via inline sources")
            return "inline-sources"
    except Exception as e:  # noqa: BLE001
        print(f"  reader(inline) failed: {type(e).__name__}: {str(e)[:500]}")
    print("  FAIL: neither handoff mechanism worked")
    return "none"


# ---------------------------------------------------------------- check 4
# The four questions the wiring plan depends on (see the plan file, step A0).

TEST_SRC = (
    "import unittest\n"
    "from solution import add\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_add_positive(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n\n"
    "    def test_negative_rejected(self):\n"
    "        with self.assertRaises(ValueError):\n"
    "            add(-1, 3)\n\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)


def check_snapshot(client: Any) -> bool:
    """4a: can we pull a file the agent wrote out of its environment, safely, as a tar?"""
    print("\n== 4a. environment snapshot download ==")
    try:
        w = create(
            client,
            input="Use code execution to create /workspace/hello.py containing print('hi') and "
                  "/workspace/notes/out.txt containing the single word banana. Reply 'done'.",
            environment="remote",
            agent_config=AGENT_CFG,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  writer failed: {type(e).__name__}: {str(e)[:400]}")
        return False
    summarize("writer", w)
    env_id = getattr(w, "environment_id", None)
    if not env_id:
        print("  FAIL: no environment_id")
        return False
    try:
        r = httpx.get(
            f"https://generativelanguage.googleapis.com/v1beta/files/environment-{env_id}:download",
            params={"alt": "media"}, headers={"x-goog-api-key": api_key() or ""},
            follow_redirects=True, timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  download failed: {type(e).__name__}: {str(e)[:300]}")
        return False
    print(f"  http {r.status_code} content-type={r.headers.get('content-type')!r} bytes={len(r.content)}")
    if r.status_code != 200:
        print(f"  body head: {r.content[:300]!r}")
        return False
    try:
        with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
            members = tar.getmembers()
            names = [m.name for m in members]
            print(f"  tar has {len(members)} members; symlinks={sum(m.issym() or m.islnk() for m in members)}; "
                  f"absolute={sum(n.startswith('/') for n in names)}; dotdot={sum('..' in n.split('/') for n in names)}")
            interesting = [n for n in names if any(k in n for k in ("hello", "out.txt", "workspace"))]
            print(f"  relevant members: {interesting[:30]}")
            found = {}
            for m in members:
                if m.isfile() and m.name.endswith(("hello.py", "out.txt")):
                    found[m.name] = (tar.extractfile(m).read() or b"").decode("utf-8", "replace")[:80]
            print(f"  file contents: {found}")
            ok = any("banana" in v for v in found.values())
    except tarfile.TarError as e:
        print(f"  not a readable tar: {e}; head={r.content[:120]!r}")
        return False
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def check_critic_pattern(client: Any) -> bool:
    """4b: fresh sandbox + inline sources + code_execution AND a custom function tool together."""
    print("\n== 4b. critic pattern: inline sources + code_execution + submit_verdict ==")
    captured: Dict[str, Any] = {}

    async def submit_verdict(args: Dict[str, Any], ctx: ToolContext) -> str:
        captured.update(args)
        return "verdict recorded"

    reg = ToolRegistry()
    reg.register(
        "submit_verdict", "Submit your final pass/fail verdict.",
        {"type": "object", "properties": {
            "passed": {"type": "boolean"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "failing_tests": {"type": "array", "items": {"type": "string"}}},
         "required": ["passed", "reasons", "failing_tests"]},
        submit_verdict,
    )
    sources = [
        {"type": "inline", "target": "/workspace/solution.py", "content": "def add(a, b):\n    return a + b\n"},
        {"type": "inline", "target": "/workspace/test_hidden.py", "content": TEST_SRC},
    ]
    try:
        i = create(
            client,
            input="You are a strict reviewer. In /workspace, run `python -m unittest test_hidden -v` with code "
                  "execution. Then call submit_verdict: passed=true only if every test passed, otherwise "
                  "passed=false with the failing test names.",
            environment={"type": "remote", "sources": sources},
            tools=[{"type": "code_execution"}] + reg.interactions_tools_schema(),
            agent_config=AGENT_CFG,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  create failed: {type(e).__name__}: {str(e)[:500]}")
        return False
    summarize("critic", i)
    calls = function_calls(i)
    if getattr(i, "status", None) != "requires_action" or not calls:
        print(f"  no function call; steps={dump(getattr(i, 'steps', None), 1500)}")
        return False
    step = calls[0]
    result = asyncio.run(reg.call(getattr(step, "name", ""), getattr(step, "arguments", None) or {},
                                  ToolContext(workspace_root=Path("."))))
    print(f"  verdict captured: {json.dumps(captured)} -> tool result {result!r}")
    try:
        final = create(
            client, previous_interaction_id=i.id,
            environment=getattr(i, "environment_id", None) or "remote",
            input=[{"type": "function_result", "name": step.name, "call_id": step.id, "result": {"status": result}}],
        )
        summarize("critic-final", final)
    except Exception as e:  # noqa: BLE001
        print(f"  function_result follow-up failed: {type(e).__name__}: {str(e)[:400]}")
        return False
    ok = captured.get("passed") is False and any("negative" in t for t in captured.get("failing_tests", []))
    print(f"  {'PASS' if ok else 'FAIL'} (expected passed=false with test_negative_rejected failing)")
    return ok


def check_model_swap(client: Any) -> Dict[str, Optional[bool]]:
    """4c: escalate models: same env with a different model, and chained with previous_interaction_id."""
    print(f"\n== 4c. model swap ({MODEL_LADDER[0]} -> {STRONG}) ==")
    out: Dict[str, Optional[bool]] = {"env_swap": None, "chain_swap": None}
    try:
        w = create(client, input="Use code execution to write the single word mango to /workspace/swap.txt. Reply 'done'.",
                   environment="remote", agent_config=AGENT_CFG)
    except Exception as e:  # noqa: BLE001
        print(f"  writer failed: {type(e).__name__}: {str(e)[:400]}")
        return out
    env_id = getattr(w, "environment_id", None)
    strong_cfg = {**AGENT_CFG, "model": STRONG}
    read = "Read /workspace/swap.txt with code execution and reply with its exact contents only."
    try:
        r = create(client, input=read, environment=env_id or "remote", agent_config=strong_cfg)
        summarize("env-swap", r)
        out["env_swap"] = "mango" in (getattr(r, "output_text", "") or "")
    except Exception as e:  # noqa: BLE001
        print(f"  env-swap failed: {type(e).__name__}: {str(e)[:400]}")
        out["env_swap"] = False
    try:
        c = create(client, previous_interaction_id=w.id, environment=env_id or "remote",
                   input="Which word did you write to swap.txt? Reply with the word only.", agent_config=strong_cfg)
        summarize("chain-swap", c)
        out["chain_swap"] = "mango" in (getattr(c, "output_text", "") or "")
    except Exception as e:  # noqa: BLE001
        print(f"  chain-swap failed: {type(e).__name__}: {str(e)[:400]}")
        out["chain_swap"] = False
    print(f"  env_swap={out['env_swap']} chain_swap={out['chain_swap']}")
    return out


def check_structured(client: Any) -> Dict[str, Optional[bool]]:
    """4d: plain model + response_format (nested schema) + previous_interaction_id chaining."""
    print("\n== 4d. structured output on a plain model, chained ==")
    from pydantic import BaseModel

    class Step(BaseModel):
        title: str
        instruction: str
        acceptance: str
        modifies_code: bool

    class Plan(BaseModel):
        steps: List[Step]

    fmt = {"type": "text", "mime_type": "application/json", "schema": Plan.model_json_schema()}
    out: Dict[str, Optional[bool]] = {"structured": None, "chained": None}
    try:
        i1 = create_model(client, STRONG, response_format=fmt,
                          input="Plan 3 steps to fix a Python script that crashes on empty input, then add a regression test.")
        summarize("plan", i1)
        plan = Plan.model_validate_json(i1.output_text)
        out["structured"] = 2 <= len(plan.steps) <= 5
        print(f"  parsed {len(plan.steps)} steps: {[s.title for s in plan.steps]}")
    except Exception as e:  # noqa: BLE001
        print(f"  structured call failed: {type(e).__name__}: {str(e)[:500]}")
        out["structured"] = False
        return out
    try:
        i2 = create_model(client, STRONG, response_format=fmt, previous_interaction_id=i1.id,
                          input="Rewrite the plan so the second step's instruction is more specific.")
        summarize("plan-v2", i2)
        Plan.model_validate_json(i2.output_text)
        out["chained"] = True
    except Exception as e:  # noqa: BLE001
        print(f"  chained call failed: {type(e).__name__}: {str(e)[:500]}")
        out["chained"] = False
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=str, choices=("1", "2", "3", "4", "4a", "4b", "4c", "4d"), help="run one check")
    args = parser.parse_args()
    want = lambda n: args.only is None or args.only == n or (n.startswith("4") and args.only == "4")  # noqa: E731

    client = make_client()
    summary: Dict[str, Any] = {}
    if want("1"):
        summary["basic_ok"] = check_basic(client)
    if want("2"):
        summary["schema_style"] = check_function_roundtrip(client)
    if want("3"):
        summary["env_handoff"] = check_env_reuse(client)
    if want("4a"):
        summary["snapshot_ok"] = check_snapshot(client)
    if want("4b"):
        summary["critic_pattern_ok"] = check_critic_pattern(client)
    if want("4c"):
        summary["model_swap"] = check_model_swap(client)
    if want("4d"):
        summary["structured"] = check_structured(client)

    print("\n== call timings ==")
    for row in TIMINGS:
        print(f"  #{row['call']} {row['kind']:<5} {row['seconds']:>6}s  tokens={row['tokens']}")
    print("\n== summary (set these in the code) ==")
    print(json.dumps(summary, indent=2))
    if summary.get("schema_style") == "nested":
        print("-> set app/tools.py SCHEMA_STYLE = 'nested'")
    if summary.get("env_handoff") == "inline-sources":
        print("-> coder->critic handoff must use inline environment sources, not env id reuse")


if __name__ == "__main__":
    main()
