"""Tracing: Langfuse integration + structured event logging + divergence computation.

Two complementary systems:
1. Langfuse @observe decorators for standard LLM observability (tool calls, I/O, latency)
2. Structured event logger with belief divergence computation for the custom legibility layer

Beliefs come directly from the agent's mandatory BELIEFS block — no secondary LLM call needed.
"""

from __future__ import annotations

import json
from pathlib import Path

from langfuse import observe

from .schemas import (
    BeliefDivergence,
    BeliefState,
    DivergenceCategory,
    DivergenceSeverity,
    RunLog,
    WorldSnapshot,
)


# ---------------------------------------------------------------------------
# Divergence computation
# ---------------------------------------------------------------------------

def compute_divergences(
    agent_id: str,
    belief: BeliefState,
    actual: WorldSnapshot,
    staleness: dict[str, int],
) -> list[BeliefDivergence]:
    """Compare belief state against ground truth. Returns list of divergences."""
    divergences: list[BeliefDivergence] = []

    # 1. Position divergence
    if belief.my_position is not None:
        actual_pos = actual.agent_positions.get(agent_id)
        if actual_pos and tuple(belief.my_position) != tuple(actual_pos):
            divergences.append(
                BeliefDivergence(
                    field="my_position",
                    believed_value=str(belief.my_position),
                    actual_value=str(actual_pos),
                    staleness_turns=max(0, staleness.get("my_position", 0)),
                    severity=DivergenceSeverity.CRITICAL,
                    category=DivergenceCategory.INCORRECT_INFERENCE,
                )
            )

    # 2. Other agent position
    other_id = "agent_b" if agent_id == "agent_a" else "agent_a"
    if belief.other_agent_position is not None:
        actual_other = actual.agent_positions.get(other_id)
        if actual_other and tuple(belief.other_agent_position) != tuple(actual_other):
            stale = staleness.get("other_agent_position", -1)
            divergences.append(
                BeliefDivergence(
                    field="other_agent_position",
                    believed_value=str(belief.other_agent_position),
                    actual_value=str(actual_other),
                    staleness_turns=max(0, stale),
                    severity=DivergenceSeverity.MEDIUM,
                    category=(
                        DivergenceCategory.STALE_INFORMATION
                        if stale > 0
                        else DivergenceCategory.INCORRECT_INFERENCE
                    ),
                )
            )

    # 3. Key location
    if belief.key_location is not None:
        actual_key = _describe_key(actual)
        if not _beliefs_match_key(belief.key_location, actual):
            stale = staleness.get("key_location", -1)
            divergences.append(
                BeliefDivergence(
                    field="key_location",
                    believed_value=belief.key_location,
                    actual_value=actual_key,
                    staleness_turns=max(0, stale),
                    severity=DivergenceSeverity.HIGH,
                    category=(
                        DivergenceCategory.STALE_INFORMATION
                        if stale > 2
                        else DivergenceCategory.NEVER_OBSERVED
                        if stale < 0
                        else DivergenceCategory.INCORRECT_INFERENCE
                    ),
                )
            )

    # 4. Door status
    if belief.door_status is not None:
        actual_door = "locked" if actual.door_locked else "unlocked"
        belief_locked = "lock" in belief.door_status.lower()
        belief_unlocked = "unlock" in belief.door_status.lower()
        if (belief_locked and not actual.door_locked) or (
            belief_unlocked and actual.door_locked
        ):
            stale = staleness.get("door_status", -1)
            divergences.append(
                BeliefDivergence(
                    field="door_status",
                    believed_value=belief.door_status,
                    actual_value=f"{actual_door} at {actual.door_position}",
                    staleness_turns=max(0, stale),
                    severity=DivergenceSeverity.HIGH,
                    category=(
                        DivergenceCategory.STALE_INFORMATION
                        if stale > 2
                        else DivergenceCategory.INCORRECT_INFERENCE
                    ),
                )
            )

    # 5. Exit location
    if belief.exit_location is not None:
        if tuple(belief.exit_location) != tuple(actual.exit_position):
            stale = staleness.get("exit_location", -1)
            divergences.append(
                BeliefDivergence(
                    field="exit_location",
                    believed_value=str(belief.exit_location),
                    actual_value=str(actual.exit_position),
                    staleness_turns=max(0, stale),
                    severity=DivergenceSeverity.HIGH,
                    category=(
                        DivergenceCategory.STALE_INFORMATION
                        if stale > 0
                        else DivergenceCategory.INCORRECT_INFERENCE
                    ),
                )
            )

    return divergences


def _describe_key(actual: WorldSnapshot) -> str:
    """Describe the key's actual state."""
    if actual.key_holder:
        return f"{actual.key_holder} has it"
    if actual.key_position:
        return f"at {actual.key_position}"
    return "used (door unlocked)"


def _beliefs_match_key(belief_key: str, actual: WorldSnapshot) -> bool:
    """Fuzzy check if the agent's key belief matches reality."""
    belief_lower = belief_key.lower()

    if actual.key_holder:
        return actual.key_holder in belief_lower or "has" in belief_lower
    if actual.key_position:
        pos_str = str(actual.key_position)
        return pos_str in belief_key or (
            str(actual.key_position[0]) in belief_key
            and str(actual.key_position[1]) in belief_key
        )
    # Key has been used
    return "used" in belief_lower or "unlock" in belief_lower


# ---------------------------------------------------------------------------
# Run log persistence
# ---------------------------------------------------------------------------

def save_run_log(run_log: RunLog, output_dir: str | Path = "runs") -> Path:
    """Save a run log to a JSON file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"{run_log.manifest.run_id}.json"
    with open(output_file, "w") as f:
        json.dump(run_log.model_dump(mode="json"), f, indent=2)
    return output_file


def load_run_log(path: str | Path) -> RunLog:
    """Load a run log from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    return RunLog.model_validate(data)
