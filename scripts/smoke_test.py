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
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def create(client: Any, **kwargs: Any) -> Any:
    return client.interactions.create(agent=AGENT_ID, **kwargs)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=int, choices=(1, 2, 3), help="run a single check")
    args = parser.parse_args()

    client = make_client()
    summary: Dict[str, Any] = {}
    if args.only in (None, 1):
        summary["basic_ok"] = check_basic(client)
    if args.only in (None, 2):
        summary["schema_style"] = check_function_roundtrip(client)
    if args.only in (None, 3):
        summary["env_handoff"] = check_env_reuse(client)

    print("\n== summary (set these in the code) ==")
    print(json.dumps(summary, indent=2))
    if summary.get("schema_style") == "nested":
        print("-> set app/tools.py SCHEMA_STYLE = 'nested'")
    if summary.get("env_handoff") == "inline-sources":
        print("-> coder->critic handoff must use inline environment sources, not env id reuse")


if __name__ == "__main__":
    main()
