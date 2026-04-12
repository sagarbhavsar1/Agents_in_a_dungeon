"""Simplified causal chain builder.

For each belief field, tracks:
  - last_correct_turn:          last turn the agent's belief matched reality
  - ground_truth_changed_turn:  when the actual world state changed (triggering the divergence)
  - stale_start_turn:           first turn the divergence appeared
  - stale_end_turn:             when it resolved (or None if still wrong at run end)
  - duration_turns:             how long the agent acted on wrong information

Only tracks the three fields where staleness is diagnostically interesting:
  key_location, door_status, other_agent_position
"""

from __future__ import annotations

from .schemas import CausalChain, FieldStalenessWindow, TurnEvent

# Fields worth tracing (my_position and exit_location rarely diverge in meaningful ways)
TRACKED_FIELDS = ["key_location", "door_status", "other_agent_position"]


def build_causal_chain(events: list[TurnEvent]) -> CausalChain:
    """Walk the event log and extract all stale windows per field per agent."""
    if not events:
        return CausalChain(run_id="", summary="no events")

    run_id = events[0].run_id
    total_turns = events[-1].turn_number

    # Group by agent, preserving turn order
    by_agent: dict[str, list[TurnEvent]] = {}
    for e in events:
        by_agent.setdefault(e.agent_id, []).append(e)
    for agent_events in by_agent.values():
        agent_events.sort(key=lambda e: e.turn_number)

    all_windows: list[FieldStalenessWindow] = []

    for agent_id, agent_events in by_agent.items():
        for field in TRACKED_FIELDS:
            windows = _extract_windows(agent_id, field, agent_events, total_turns)
            all_windows.extend(windows)

    # Sort by duration descending so worst offenders surface first
    all_windows.sort(key=lambda w: w.duration_turns, reverse=True)

    total_stale = sum(w.duration_turns for w in all_windows)
    worst = all_windows[0] if all_windows else None

    summary = _make_summary(worst, all_windows, total_turns)

    return CausalChain(
        run_id=run_id,
        windows=all_windows,
        total_stale_agent_turns=total_stale,
        worst_window=worst,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Core window extraction
# ---------------------------------------------------------------------------

def _extract_windows(
    agent_id: str,
    field: str,
    events: list[TurnEvent],
    total_turns: int,
) -> list[FieldStalenessWindow]:
    """Find all stale windows for one (agent, field) pair."""
    windows: list[FieldStalenessWindow] = []

    in_stale = False
    window_start = 0
    last_correct_turn: int | None = None
    gt_changed_turn = 0
    believed_val = ""
    actual_val = ""

    prev_actual: str | None = None

    for event in events:
        turn = event.turn_number
        curr_actual = _get_actual_str(field, agent_id, event)

        # Does this event have a divergence for this field?
        div = next((d for d in event.divergences if d.field == field), None)
        has_div = div is not None

        if has_div and not in_stale:
            # --- Stale window opens ---
            in_stale = True
            window_start = turn
            believed_val = div.believed_value
            actual_val = div.actual_value

            # Ground truth changed on the turn when actual value first differed from prev
            # Compare this event's actual to the previous event's actual
            if prev_actual is not None and prev_actual != curr_actual:
                gt_changed_turn = turn
            else:
                # GT was already different — best estimate is this window's start
                gt_changed_turn = turn

        elif has_div and in_stale:
            # --- Still stale — keep going, update believed/actual in case they evolved ---
            believed_val = div.believed_value
            actual_val = div.actual_value

        elif not has_div and in_stale:
            # --- Stale window closes ---
            duration = turn - window_start
            windows.append(FieldStalenessWindow(
                field=field,
                agent_id=agent_id,
                believed_value=believed_val,
                actual_value=actual_val,
                last_correct_turn=last_correct_turn,
                ground_truth_changed_turn=gt_changed_turn,
                stale_start_turn=window_start,
                stale_end_turn=turn,
                duration_turns=duration,
            ))
            in_stale = False
            last_correct_turn = turn

        else:
            # Belief correct (or null/unknown — not divergent)
            last_correct_turn = turn

        prev_actual = curr_actual

    # If still stale at end of run
    if in_stale:
        duration = total_turns - window_start + 1
        windows.append(FieldStalenessWindow(
            field=field,
            agent_id=agent_id,
            believed_value=believed_val,
            actual_value=actual_val,
            last_correct_turn=last_correct_turn,
            ground_truth_changed_turn=gt_changed_turn,
            stale_start_turn=window_start,
            stale_end_turn=None,
            duration_turns=duration,
        ))

    return windows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_actual_str(field: str, agent_id: str, event: TurnEvent) -> str:
    """Canonical string for an actual world state field — used for change detection."""
    actual = event.actual_world_state
    if field == "door_status":
        return "locked" if actual.door_locked else "unlocked"
    if field == "key_location":
        if actual.key_holder:
            return f"held:{actual.key_holder}"
        if actual.key_position:
            return f"floor:{actual.key_position}"
        return "used"
    if field == "other_agent_position":
        other = "agent_b" if agent_id == "agent_a" else "agent_a"
        pos = actual.agent_positions.get(other)
        return str(pos) if pos else "unknown"
    return "unknown"


def _make_summary(
    worst: FieldStalenessWindow | None,
    all_windows: list[FieldStalenessWindow],
    total_turns: int,
) -> str:
    if not worst:
        return "No belief staleness detected — agents stayed well-informed."

    resolved = "resolved" if worst.stale_end_turn else "never resolved"
    parts = [
        f"Worst: {worst.agent_id} held stale {worst.field} for {worst.duration_turns} turns "
        f"(T{worst.stale_start_turn}→{'T' + str(worst.stale_end_turn) if worst.stale_end_turn else 'end'}, {resolved})."
    ]
    if len(all_windows) > 1:
        parts.append(f"{len(all_windows)} total stale windows across all fields and agents.")
    return " ".join(parts)
