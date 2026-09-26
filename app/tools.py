"""Tool registry -- PORTED from RoundtableCI.

Source: roundtable-service/app/services/tool_registry.py (register / schema
conversion / never-raises dispatch, plus resolve_safe_path).

Changes from the original:
- Dropped ToolContext.sqlite_db_path / write_allowlist / proposed_edits (the
  file-editing-engineer trust model); the Antigravity sandbox owns file edits.
- register() takes (name, description, parameters, handler) and builds the wire
  schema itself, instead of validating a pre-built OpenAI-shaped dict.
- openai_tools_schema() became interactions_tools_schema(): the Interactions
  API's function-tool shape. The Gemini docs disagree on it (one page shows the
  flat {"type","name","description","parameters"}, another the OpenAI-style
  nested {"type","function":{...}}), so the shape lives in exactly one place --
  SCHEMA_STYLE. Verified against the live API (2026-09-26): flat works.

Contract kept as-is: call() NEVER raises. An unknown tool, malformed
arguments, or a handler exception all degrade to an "error: ..." string that
goes back to the agent as the function result, so a bad tool call is something
the agent can react to rather than something that kills the run.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# "flat" | "nested" -- see module docstring.
SCHEMA_STYLE = "flat"


@dataclass
class ToolContext:
    """Per-invocation scope a tool call is resolved against."""

    workspace_root: Path


# A handler gets the parsed argument dict and the ToolContext and returns its
# result as a string (success or error-shaped) -- never raises.
ToolHandler = Callable[[Dict[str, Any], ToolContext], Awaitable[str]]


def resolve_safe_path(ctx: ToolContext, requested_path: str) -> Optional[Path]:
    """Resolve `requested_path` against ctx.workspace_root; None on any violation.

    Rejects: non-string/empty, null bytes, absolute POSIX/UNC/Windows-drive
    forms, and anything resolving outside workspace_root (covers `..`
    traversal and symlink escapes, since Path.resolve() follows symlinks).
    Ported verbatim from RoundtableCI's resolve_safe_path.
    """
    if not isinstance(requested_path, str) or not requested_path.strip():
        return None
    if "\x00" in requested_path:
        return None
    candidate = requested_path.strip().replace("\\", "/")
    if candidate.startswith("/") or candidate.startswith("//") or _DRIVE_RE.match(candidate):
        return None
    root_resolved = ctx.workspace_root.resolve()
    try:
        target = (root_resolved / candidate).resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return None
    return target


@dataclass
class _RegisteredTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler


def _to_wire(tool: _RegisteredTool, style: str) -> Dict[str, Any]:
    if style == "flat":
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
    if style == "nested":
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
    raise ValueError(f"unknown schema style {style!r} (expected 'flat' or 'nested')")


class ToolRegistry:
    """Registration / schema conversion / dispatch for agent function tools.

    register() raises on a duplicate name: registration happens at developer
    time, not under LLM control, so a duplicate is a bug to fail fast on.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, _RegisteredTool] = {}

    def register(
        self, name: str, description: str, parameters: Dict[str, Any], handler: ToolHandler
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool name must be a non-empty string")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError(f"tool '{name}': parameters must be a JSON-schema object ({{'type': 'object', ...}})")
        if name in self._tools:
            raise ValueError(f"tool '{name}' is already registered -- duplicate registration is not allowed")
        self._tools[name] = _RegisteredTool(name, description, parameters, handler)

    def names(self) -> List[str]:
        return list(self._tools)

    def interactions_tools_schema(self, style: Optional[str] = None) -> List[Dict[str, Any]]:
        """The `tools=` list for client.interactions.create, in registration order."""
        chosen = style or SCHEMA_STYLE
        return [_to_wire(tool, chosen) for tool in self._tools.values()]

    async def call(self, name: str, arguments: Any, ctx: Any) -> str:
        """Dispatch by name. Never raises -- always returns a string.

        `arguments` is normally an already-parsed dict; a raw JSON string is
        also accepted and parsed here (a parse failure is an error string, not
        a crash).
        """
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as e:
                return f"error: could not parse arguments as JSON: {e}"

        if not isinstance(arguments, dict):
            return f"error: arguments must be a JSON object, got {type(arguments).__name__}"

        tool = self._tools.get(name)
        if tool is None:
            return f"error: unknown tool '{name}'"

        try:
            return await tool.handler(arguments, ctx)
        except Exception as e:  # noqa: BLE001 -- the never-raises contract
            logger.warning("tools: tool '%s' raised during call(): %s", name, e)
            return f"error: tool '{name}' failed: {e}"
