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
    FailureMode,
    RunDiagnosis,
    RunLog,
    TurnEvent,
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
        status = belief.door_status.lower()
        belief_locked = "lock" in status and "unlock" not in status
        belief_unlocked = "unlock" in status
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


_DIRECTION_DELTAS = {
    "north": (-1, 0), "south": (1, 0), "east": (0, 1), "west": (0, -1),
    "up": (-1, 0), "down": (1, 0), "right": (0, 1), "left": (0, -1),
}


def compute_decision_quality(
    agent_id: str,
    tool_name: str,
    tool_input: dict,
    tool_success: bool,
    actual: WorldSnapshot,
    staleness: dict[str, int],
) -> tuple[str, bool, int]:
    """Compute expected outcome, whether it matched, and decision info age.

    Returns (expected_outcome, outcome_matched_expectation, decision_info_age).
    expected_outcome is "success" or "failure" based purely on actual world state.
    decision_info_age = max staleness of fields relevant to this action.
    """
    agent_pos = actual.agent_positions.get(agent_id)
    if agent_pos is None:
        return "unknown", True, 0

    if tool_name == "move":
        direction = str(tool_input.get("direction", "")).lower()
        delta = _DIRECTION_DELTAS.get(direction)
        if delta is None:
            return "failure", not tool_success, 0

        tr, tc = agent_pos[0] + delta[0], agent_pos[1] + delta[1]
        rows = len(actual.grid)
        cols = len(actual.grid[0]) if rows else 0

        if not (0 <= tr < rows and 0 <= tc < cols):
            expected = "failure"
        else:
            cell = actual.grid[tr][tc]
            if cell == "wall":
                expected = "failure"
            elif cell == "door":
                expected = "success" if not actual.door_locked else "failure"
            else:
                expected = "success"
        info_age = max(0, staleness.get("my_position", 0))

    elif tool_name == "pick_up":
        item = str(tool_input.get("item", ""))
        # Key is at actual.key_position if not yet picked up
        if item == "key":
            if actual.key_position and tuple(actual.key_position) == tuple(agent_pos):
                expected = "success"
            else:
                expected = "failure"
            info_age = max(0, staleness.get("key_location", 0))
        else:
            # Other items: check actual.items
            item_pos = actual.items.get(item)
            expected = "success" if item_pos and tuple(item_pos) == tuple(agent_pos) else "failure"
            info_age = 0

    elif tool_name == "use_item":
        item = str(tool_input.get("item", ""))
        if item == "key":
            inv = actual.agent_inventories.get(agent_id, [])
            has_key = "key" in inv
            door_r, door_c = actual.door_position
            ar, ac = agent_pos
            adjacent = abs(door_r - ar) + abs(door_c - ac) == 1
            if has_key and adjacent and actual.door_locked:
                expected = "success"
            else:
                expected = "failure"
            info_age = max(
                max(0, staleness.get("key_location", 0)),
                max(0, staleness.get("door_status", 0)),
            )
        else:
            expected = "failure"
            info_age = 0

    elif tool_name == "send_message":
        expected = "success"
        info_age = 0

    elif tool_name == "wait":
        expected = "success"
        info_age = 0

    else:
        expected = "success"
        info_age = 0

    matched = tool_success == (expected == "success")
    return expected, matched, info_age


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


# ---------------------------------------------------------------------------
# Post-hoc run diagnosis
# ---------------------------------------------------------------------------

def generate_diagnosis(events: list[TurnEvent]) -> RunDiagnosis:
    """Analyze a completed run and identify the primary failure mode."""
    if not events:
        return RunDiagnosis(primary_failure_mode=FailureMode.NONE)

    total = len(events)
    stale_threshold = 3  # turns: info older than this counts as "stale decision"

    # Count stale decisions (agent acted on old info and expectation didn't match)
    stale_decisions = sum(
        1 for e in events
        if e.decision_info_age > stale_threshold and e.outcome_matched_expectation is False
    )
    stale_decision_rate = stale_decisions / total

    # Wasted turns: waits + failed tool calls
    wasted = sum(1 for e in events if e.tool_name == "wait" or not e.tool_success)

    # Total divergences
    total_divs = sum(len(e.divergences) for e in events)
    avg_divs = total_divs / total

    # Coordination gap: turns where both agents have contradictory key/door beliefs
    turn_beliefs: dict[int, dict[str, dict]] = {}
    for e in events:
        t = e.turn_number
        if t not in turn_beliefs:
            turn_beliefs[t] = {}
        turn_beliefs[t][e.agent_id] = {
            "key": e.belief_state.key_location,
            "door": e.belief_state.door_status,
        }
    coordination_gaps = 0
    for t, agents_data in turn_beliefs.items():
        if len(agents_data) < 2:
            continue
        beliefs_list = list(agents_data.values())
        # Contradiction: one thinks key is at X, other thinks it's elsewhere
        keys = [b["key"] for b in beliefs_list if b["key"]]
        if len(keys) == 2 and keys[0] != keys[1]:
            # Only count if neither is "unknown" and they differ in a meaningful way
            if not any("unknown" in (k or "").lower() for k in keys):
                coordination_gaps += 1

    # Find bottleneck: the earliest turn with a stale-decision failure (per-agent)
    bottleneck_turn = None
    bottleneck_agent = None
    for e in events:
        if e.decision_info_age > stale_threshold and e.outcome_matched_expectation is False:
            bottleneck_turn = e.turn_number
            bottleneck_agent = e.agent_id
            break

    # Determine primary failure mode
    if stale_decision_rate > 0.15:
        mode = FailureMode.STALE_BELIEFS
    elif coordination_gaps > 5:
        mode = FailureMode.POOR_COORDINATION
    elif wasted / total > 0.4:
        mode = FailureMode.STUCK_LOOP
    elif avg_divs > 2.0:
        mode = FailureMode.EXPLORATION_INEFFICIENCY
    else:
        mode = FailureMode.NONE

    # Build human-readable insights
    insights: list[str] = []
    if stale_decisions > 0:
        insights.append(
            f"{stale_decisions} turns where agent acted on >{stale_threshold}-turn-old information and failed"
        )
    if coordination_gaps > 0:
        insights.append(f"{coordination_gaps} turns where agents held contradictory beliefs about key/door")
    if wasted > 0:
        insights.append(f"{wasted} wasted turns (waits + failed moves) out of {total} total")
    if avg_divs > 1.0:
        insights.append(f"Average {avg_divs:.1f} belief divergences per turn")

    return RunDiagnosis(
        primary_failure_mode=mode,
        bottleneck_turn=bottleneck_turn,
        bottleneck_agent=bottleneck_agent,
        stale_decision_rate=round(stale_decision_rate, 3),
        avg_divergences_per_turn=round(avg_divs, 2),
        coordination_gap_turns=coordination_gaps,
        wasted_turns=wasted,
        key_insights=insights,
    )
