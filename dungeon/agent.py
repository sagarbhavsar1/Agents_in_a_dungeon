"""LLM agent: tool calling loop via Anthropic SDK.

Each agent maintains its own conversation history and calls Claude
with the dungeon tools. Belief extraction happens in a separate,
isolated call (never injected into the agent's conversation).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import anthropic
from langfuse import get_client, observe

from .schemas import BeliefState, Message, ObservableState
from .tools import AGENT_TOOLS

langfuse = get_client()


@dataclass
class ToolCall:
    """Parsed tool call from the LLM response."""
    name: str
    input: dict
    reasoning: str       # full text block from the agent
    belief: BeliefState  # parsed from the mandatory BELIEFS block
    belief_parse_failed: bool  # True if we couldn't parse the BELIEFS block
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int


SYSTEM_PROMPT_TEMPLATE = """You are {agent_id}, an AI agent exploring a dungeon with another agent ({other_agent_id}).

## Objective
Both agents must reach the exit. The dungeon has a locked door blocking the path — one of you needs to find the key and unlock the door so both agents can pass through and reach the exit.

## What you know
- The dungeon is an 8x8 grid. You can only see your current cell and the 4 adjacent cells (fog of war).
- You start at a random position. You don't know where anything is until you explore.
- There is exactly one key, one locked door, and one exit somewhere in the dungeon.
- There may be other items (they are not useful for the objective, but you can pick them up).
- The other agent ({other_agent_id}) is also exploring. You can send them messages, but messages are delivered on their next turn, not instantly.

## Response format — REQUIRED every turn
You MUST start every response with a BELIEFS block. This is mandatory and must be valid JSON.
Use "unknown" for anything you haven't seen yet. Never leave a field out.

BELIEFS:
{{
  "my_position": [row, col],
  "other_agent_position": [row, col] or "unknown",
  "key_location": "at (row, col)" or "agent_X has it" or "I have it" or "used on door" or "unknown",
  "door_status": "locked at (row, col)" or "unlocked at (row, col)" or "unknown",
  "exit_location": [row, col] or "unknown",
  "current_goal": "one sentence describing what you are trying to do right now",
  "plan": "your intended next 1-2 actions and why"
}}

After the BELIEFS block, write your reasoning, then call a tool.

## Rules
- You can only move to floor cells, unlocked doors, or the exit. Walls block movement.
- You must be adjacent to the door to use the key on it.
- The game ends when BOTH agents reach the exit cell."""


class DungeonAgent:
    """An LLM-powered dungeon agent using the Anthropic SDK."""

    def __init__(
        self,
        agent_id: str,
        client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-20250514",
    ):
        self.agent_id = agent_id
        self.other_agent_id = "agent_b" if agent_id == "agent_a" else "agent_a"
        self.client = client
        self.model = model

        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            agent_id=agent_id,
            other_agent_id=self.other_agent_id,
        )

        # Conversation history — the agent's full context
        self.messages: list[dict] = []

        # Staleness tracking: field -> last turn directly observed
        self.last_observed: dict[str, int | None] = {
            "my_position": None,
            "other_agent_position": None,
            "key_location": None,
            "door_status": None,
            "exit_location": None,
        }

    @observe(name="agent_turn", as_type="generation")
    def take_turn(
        self,
        observable_state: ObservableState,
        pending_messages: list[Message],
        turn_number: int,
    ) -> ToolCall:
        """Get the agent's next action.

        1. Build user message from observable state + messages
        2. Call Claude with tools
        3. Parse the tool_use response
        4. Append assistant response and tool result placeholder to history
        5. Return structured ToolCall
        """
        # Update staleness from observable state
        self._update_staleness(observable_state, turn_number)

        # Build the turn message
        user_content = self._build_turn_message(observable_state, pending_messages, turn_number)
        self.messages.append({"role": "user", "content": user_content})

        # Call Claude
        start = time.monotonic()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self.system_prompt,
            tools=AGENT_TOOLS,
            messages=self.messages,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        # Parse response: extract reasoning text and tool_use block
        reasoning = ""
        tool_name = "wait"
        tool_input: dict = {}

        for block in response.content:
            if block.type == "text":
                reasoning = block.text
            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

        # Parse the mandatory BELIEFS block from the reasoning text
        belief, parse_failed = self._parse_belief_block(reasoning)

        # Enrich the Langfuse generation span with structured metadata
        langfuse.update_current_generation(
            name=f"{self.agent_id}_turn_{turn_number}",
            model=self.model,
            input=user_content,
            output={"reasoning": reasoning, "tool": tool_name, "tool_input": tool_input},
            usage_details={
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            },
            metadata={
                "agent_id": self.agent_id,
                "turn_number": turn_number,
                "tool_called": tool_name,
                "belief_extraction_failed": parse_failed,
                "belief_my_position": str(belief.my_position) if belief.my_position else "unknown",
                "belief_key_location": belief.key_location or "unknown",
                "belief_door_status": belief.door_status or "unknown",
                "latency_ms": latency_ms,
            },
            level="WARNING" if parse_failed else "DEFAULT",
        )

        # Append assistant response to conversation history
        self.messages.append({"role": "assistant", "content": response.content})

        return ToolCall(
            name=tool_name,
            input=tool_input,
            reasoning=reasoning,
            belief=belief,
            belief_parse_failed=parse_failed,
            latency_ms=latency_ms,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )

    def receive_tool_result(self, tool_name: str, result: dict, success: bool) -> None:
        """Feed the tool execution result back into the conversation history."""
        # Find the tool_use block id from the last assistant message
        tool_use_id = None
        last_msg = self.messages[-1]
        if last_msg["role"] == "assistant":
            for block in last_msg["content"]:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_use_id = block.id
                    break

        if tool_use_id:
            self.messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": json.dumps(result),
                        "is_error": not success,
                    }
                ],
            })

    def _parse_belief_block(self, text: str) -> tuple[BeliefState, bool]:
        """Parse the mandatory BELIEFS JSON block from the agent's response text.

        Returns (BeliefState, parse_failed). parse_failed=True means the agent
        didn't produce a parseable block — that itself is diagnostic information.
        """
        try:
            # Find the JSON object after "BELIEFS:"
            match = re.search(r"BELIEFS:\s*(\{.*?\})", text, re.DOTALL)
            if not match:
                return BeliefState(extraction_failed=True), True

            raw = match.group(1).strip()
            data = json.loads(raw)

            def parse_pos(val) -> tuple[int, int] | None:
                if val is None or val == "unknown":
                    return None
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    return (int(val[0]), int(val[1]))
                return None

            return BeliefState(
                my_position=parse_pos(data.get("my_position")),
                other_agent_position=parse_pos(data.get("other_agent_position")),
                key_location=None if data.get("key_location") == "unknown" else data.get("key_location"),
                door_status=None if data.get("door_status") == "unknown" else data.get("door_status"),
                exit_location=parse_pos(data.get("exit_location")),
                current_goal=data.get("current_goal", "unknown"),
                plan=data.get("plan", "unknown"),
                extraction_failed=False,
            ), False

        except Exception:
            return BeliefState(extraction_failed=True), True

    def _build_turn_message(
        self,
        obs: ObservableState,
        messages: list[Message],
        turn_number: int,
    ) -> str:
        """Build the user message for this turn."""
        parts = [f"=== Turn {turn_number} ==="]
        parts.append(f"Your position: ({obs.position[0]}, {obs.position[1]})")
        parts.append(f"Your inventory: {obs.inventory if obs.inventory else '(empty)'}")
        parts.append("")

        # Current cell
        parts.append(f"Current cell: {obs.current_cell.type.value}")
        if obs.current_cell.items:
            parts.append(f"  Items here: {obs.current_cell.items}")
        if obs.current_cell.agents:
            parts.append(f"  Other agents here: {obs.current_cell.agents}")
        if obs.current_cell.is_locked is not None:
            parts.append(f"  Door locked: {obs.current_cell.is_locked}")
        parts.append("")

        # Adjacent cells
        parts.append("Adjacent cells:")
        for direction, cell in obs.adjacent_cells.items():
            desc = f"  {direction}: {cell.type.value}"
            if cell.items:
                desc += f" (items: {cell.items})"
            if cell.agents:
                desc += f" (agents: {cell.agents})"
            if cell.is_locked is not None:
                desc += f" (locked: {cell.is_locked})"
            parts.append(desc)

        # Pending messages
        if messages:
            parts.append("")
            parts.append("Messages received:")
            for msg in messages:
                parts.append(f"  From {msg.from_agent}: \"{msg.content}\"")

        parts.append("")
        parts.append("Choose your next action. Think step by step about what you know and what to do.")

        return "\n".join(parts)

    def _update_staleness(self, obs: ObservableState, turn: int) -> None:
        """Update last_observed based on what's directly visible this turn."""
        # Position is always observable
        self.last_observed["my_position"] = turn

        # Check adjacent cells and current cell for other agents, key, door, exit
        all_cells = [("current", obs.current_cell)]
        all_cells.extend(obs.adjacent_cells.items())

        for _label, cell in all_cells:
            if cell.agents:
                self.last_observed["other_agent_position"] = turn
            if "key" in cell.items:
                self.last_observed["key_location"] = turn
            if cell.type.value == "door":
                self.last_observed["door_status"] = turn
            if cell.type.value == "exit":
                self.last_observed["exit_location"] = turn

        # Also update key_location if agent is carrying the key
        if "key" in obs.inventory:
            self.last_observed["key_location"] = turn

    def get_staleness(self, current_turn: int) -> dict[str, int]:
        """Compute staleness for each tracked field."""
        result = {}
        for field_name, last_turn in self.last_observed.items():
            if last_turn is None:
                result[field_name] = -1  # never observed
            else:
                result[field_name] = current_turn - last_turn
        return result
