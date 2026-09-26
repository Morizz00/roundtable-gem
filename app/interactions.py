"""The ONLY module that touches the google-genai SDK.

STUB -- implemented in the next step, after scripts/smoke_test.py settles the
open API questions (function-tool schema shape, environment reuse, the exact
shape of interaction.steps / status / usage).

Contract:
    run_agent(...) issues one Antigravity interaction and returns an
    AgentResult. It NEVER raises: SDK errors, timeouts and "incomplete"
    (token budget exceeded) all come back as an AgentResult with a non-success
    status, because the orchestrator's whole job is to recover from those.

    It runs the sync SDK call via asyncio.to_thread, honours
    RunBudget.interaction_timeout_s, and passes RunBudget.max_total_tokens as
    agent_config.max_total_tokens.

    Function-tool round trips (status == "requires_action") are handled here:
    execute the calls through app.tools.ToolRegistry, send the function_result
    back with previous_interaction_id, repeat until the interaction settles,
    emitting tool_called / tool_returned events to the StateLog as it goes.

Every model swap goes through AgentSpec.model -> agent_config.model on the
inline base agent (named managed agents cannot change model per interaction).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.routing import AgentSpec


@dataclass
class AgentResult:
    interaction_id: Optional[str]
    environment_id: Optional[str]  # reuse to hand the coder's files to the critic
    status: str  # "completed" | "incomplete" | "timeout" | "error" | ...
    output_text: str = ""
    tokens: Optional[int] = None
    function_calls: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


async def run_agent(
    spec: AgentSpec,
    prompt: str,
    *,
    system_instruction: str,
    tools: Any = None,  # app.tools.ToolRegistry
    environment: Any = "remote",  # "remote" | env id | {"type": "remote", "sources": [...]}
    previous_interaction_id: Optional[str] = None,
) -> AgentResult:
    raise NotImplementedError("implemented after scripts/smoke_test.py resolves the open API questions")
