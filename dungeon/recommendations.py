"""Recommendations engine: answers "What should change next?"

Given a completed run's events, diagnosis, and causal chain, generates
concrete, evidence-backed recommendations for improving the system.

Deterministic/rule-based — not another LLM call. The failure modes are
enumerable and the patterns are well-understood from the trace data.
Each recommendation cites specific turns so engineers can jump straight
to the evidence.
"""

from __future__ import annotations

from typing import Literal

from .schemas import (
    CausalChain,
    FailureMode,
    FieldStalenessWindow,
    Recommendation,
    RunDiagnosis,
    TurnEvent,
)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

_STALE_THRESHOLD_CRITICAL = 4  # windows longer than this → critical priority
_STALE_THRESHOLD_HIGH = 2


def generate_recommendations(
    events: list[TurnEvent],
    diagnosis: RunDiagnosis | None,
    causal_chain: CausalChain | None,
) -> list[Recommendation]:
    """Generate prioritized recommendations for a completed run."""
    if not events:
        return []

    recs: list[Recommendation] = []
    total_turns = events[-1].turn_number if events else 0

    # --- 1. Causal chain: stale windows are the strongest signal ---
    if causal_chain and causal_chain.windows:
        for window in causal_chain.windows:
            rec = _recommend_for_stale_window(window, events, total_turns)
            if rec:
                recs.append(rec)

    # --- 2. Diagnosis-level patterns ---
    if diagnosis:
        recs.extend(_recommend_for_diagnosis(diagnosis, events, total_turns))

    # --- 3. Structural / architectural signals from raw events ---
    recs.extend(_recommend_structural(events, total_turns))

    # --- 4. Prompt quality signals ---
    recs.extend(_recommend_prompt(events))

    # Deduplicate by (category, recommendation prefix) and sort by priority
    seen: set[str] = set()
    deduped: list[Recommendation] = []
    for r in recs:
        key = f"{r.category}:{r.recommendation[:60]}"
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    priority_order = {"critical": 0, "high": 1, "medium": 2}
    deduped.sort(key=lambda r: priority_order[r.priority])
    return deduped


# ---------------------------------------------------------------------------
# Per-stale-window rules
# ---------------------------------------------------------------------------

def _recommend_for_stale_window(
    w: FieldStalenessWindow,
    events: list[TurnEvent],
    total_turns: int,
) -> Recommendation | None:
    """Map one FieldStalenessWindow to a concrete recommendation."""
    agent_label = "A" if w.agent_id == "agent_a" else "B"
    duration = w.duration_turns
    resolved = "resolved" if w.stale_end_turn else "never resolved by run end"
    evidence = list(range(w.stale_start_turn, min(w.stale_start_turn + 3, total_turns + 1)))

    priority: Literal["critical", "high", "medium"] = (
        "critical" if duration >= _STALE_THRESHOLD_CRITICAL else
        "high" if duration >= _STALE_THRESHOLD_HIGH else
        "medium"
    )

    if w.field == "key_location":
        # Was the other agent the key_holder during the window?
        other_id = "agent_b" if w.agent_id == "agent_a" else "agent_a"
        other_label = "B" if agent_label == "A" else "A"
        actual_lower = (w.actual_value or "").lower()
        is_partner_held = other_id in actual_lower
        is_self_held = "i have" in actual_lower or w.agent_id in actual_lower

        if is_partner_held:
            return Recommendation(
                priority=priority,
                category="coordination",
                finding=(
                    f"Agent {agent_label} believed key was '{w.believed_value}' for {duration} turns "
                    f"(T{w.stale_start_turn}–T{w.stale_end_turn or total_turns}, {resolved}), "
                    f"while Agent {other_label} was actually holding it since T{w.ground_truth_changed_turn}."
                ),
                recommendation=(
                    f"Add an automatic 'key pickup' broadcast: when any agent picks up the key, "
                    f"immediately send a message to the partner agent with the new key holder. "
                    f"This single message at T{w.ground_truth_changed_turn} would have resolved "
                    f"Agent {agent_label}'s {duration}-turn stale window."
                ),
                expected_impact=(
                    f"Eliminates key-location staleness caused by partner pickup. "
                    f"Would have removed {duration} turns of incorrect key beliefs."
                ),
                evidence_turns=evidence,
            )
        else:
            return Recommendation(
                priority=priority,
                category="coordination",
                finding=(
                    f"Agent {agent_label} had stale key_location belief for {duration} turns "
                    f"(T{w.stale_start_turn}–T{w.stale_end_turn or total_turns}, {resolved}). "
                    f"Believed: '{w.believed_value}'. Actual: '{w.actual_value}'."
                ),
                recommendation=(
                    f"Add a staleness budget for key_location: if belief hasn't been confirmed "
                    f"in 3+ turns, move toward last known key position or query partner before "
                    f"continuing other actions. Agent {agent_label} acted on {duration}-turn-old "
                    f"key info starting T{w.stale_start_turn}."
                ),
                expected_impact=(
                    f"Forces re-verification before key-dependent actions, cutting stale "
                    f"decision failures on key location."
                ),
                evidence_turns=evidence,
            )

    elif w.field == "door_status":
        # Count moves toward door during the stale window
        door_moves = _count_moves_toward(w.agent_id, w.stale_start_turn,
                                          w.stale_end_turn or total_turns, events)
        return Recommendation(
            priority=priority,
            category="coordination",
            finding=(
                f"Agent {agent_label} believed door was '{w.believed_value}' for {duration} turns "
                f"(T{w.stale_start_turn}–T{w.stale_end_turn or total_turns}, {resolved}). "
                + (f"Made {door_moves} moves toward the door on this incorrect belief." if door_moves else "")
            ),
            recommendation=(
                f"Add a door-status change broadcast: when the door is unlocked (use_item key), "
                f"send an immediate message to the partner with updated door status. "
                f"Agents blocked by a door they believe is locked when it's unlocked is a "
                f"wasted-turn multiplier."
            ),
            expected_impact=(
                f"Eliminates door-status staleness after unlock event. "
                f"Would have unblocked Agent {agent_label} {duration} turns sooner."
            ),
            evidence_turns=evidence,
        )

    elif w.field == "other_agent_position":
        return Recommendation(
            priority="medium",
            category="coordination",
            finding=(
                f"Agent {agent_label} had stale other-agent position for {duration} turns "
                f"(T{w.stale_start_turn}–T{w.stale_end_turn or total_turns}). "
                f"Believed partner at '{w.believed_value}', actually at '{w.actual_value}'."
            ),
            recommendation=(
                f"Add periodic position pings: agents should broadcast their position every "
                f"3 turns when actively coordinating, especially near the key or door. "
                f"Stale partner position prevents effective task splitting."
            ),
            expected_impact=(
                f"Reduces coordination gaps from partner position uncertainty. "
                f"Enables agents to divide exploration quadrants without overlap."
            ),
            evidence_turns=evidence,
        )

    return None


# ---------------------------------------------------------------------------
# Diagnosis-level rules
# ---------------------------------------------------------------------------

def _recommend_for_diagnosis(
    diagnosis: RunDiagnosis,
    events: list[TurnEvent],
    total_turns: int,
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    mode = diagnosis.primary_failure_mode

    if mode == FailureMode.STUCK_LOOP:
        wasted = diagnosis.wasted_turns
        # Find the earliest stuck run
        stuck_turns = _find_stuck_sequence(events, min_length=3)
        evidence = stuck_turns[:3]
        recs.append(Recommendation(
            priority="critical",
            category="architecture",
            finding=(
                f"{wasted} of {total_turns} turns were waits or failed moves "
                f"({wasted / total_turns * 100:.0f}% wasted). "
                + (f"Longest stuck sequence started at T{stuck_turns[0]}." if stuck_turns else "")
            ),
            recommendation=(
                "Add randomized backtracking: after 3 consecutive failed moves or waits, "
                "randomly choose from unexplored directions rather than retrying the same action. "
                "Also add an explicit 'I am stuck' message to partner so they can coordinate a path."
            ),
            expected_impact=(
                "Breaks stuck loops that currently drain turn budget without progress. "
                "Also gives the partner agent actionable stuck-signal to coordinate around."
            ),
            evidence_turns=evidence,
        ))

    if mode == FailureMode.POOR_COORDINATION:
        gaps = diagnosis.coordination_gap_turns
        recs.append(Recommendation(
            priority="high",
            category="coordination",
            finding=(
                f"Agents held contradictory beliefs about key/door for {gaps} turns. "
                "This means both agents were planning around different versions of reality "
                "simultaneously — neither could make optimal decisions."
            ),
            recommendation=(
                "Add explicit acknowledgment in the BELIEFS block: when an agent receives a "
                "message about a key fact (key location, door status), it should echo back "
                "'confirmed: key held by agent_X' in its next message. This closes the loop "
                "and prevents silent belief divergence between partners."
            ),
            expected_impact=(
                f"Reduces coordination gap turns from {gaps} toward 0. "
                "Agents operating on consistent state make non-conflicting plans."
            ),
            evidence_turns=[],
        ))

    if mode == FailureMode.EXPLORATION_INEFFICIENCY:
        avg = diagnosis.avg_divergences_per_turn
        recs.append(Recommendation(
            priority="medium",
            category="exploration",
            finding=(
                f"Average {avg:.1f} belief divergences per turn. "
                "High divergence rate indicates agents are frequently reasoning about areas "
                "they haven't recently observed — map knowledge is sparse."
            ),
            recommendation=(
                "Add quadrant assignment at game start: in the first message exchange, "
                "agents should agree to explore different halves of the grid. "
                "Also add 'I have explored rows 0-3, now moving to rows 4-7' status updates "
                "so partner doesn't re-explore covered ground."
            ),
            expected_impact=(
                "Faster key/door discovery reduces turns spent on stale beliefs. "
                f"Target: reduce avg divergences from {avg:.1f} below 1.0."
            ),
            evidence_turns=[],
        ))

    if mode == FailureMode.STALE_BELIEFS and diagnosis.bottleneck_turn:
        bt = diagnosis.bottleneck_turn
        ba_label = "A" if diagnosis.bottleneck_agent == "agent_a" else "B"
        recs.append(Recommendation(
            priority="high",
            category="prompt",
            finding=(
                f"Stale-belief failures began at T{bt} (Agent {ba_label}). "
                f"{diagnosis.stale_decision_rate * 100:.0f}% of turns involved acting on "
                "information older than 3 turns with mismatched outcomes."
            ),
            recommendation=(
                "Add a staleness guard to the system prompt: before taking any action that "
                "depends on key_location, door_status, or partner position, check if that "
                "belief is more than 3 turns old. If so, add 'STALE_WARNING: re-verify before "
                "acting' to the BELIEFS block and prioritize observation over action."
            ),
            expected_impact=(
                "Forces agents to surface stale-belief uncertainty before it causes failed "
                f"actions. Target: stale decision rate below 5% (currently "
                f"{diagnosis.stale_decision_rate * 100:.0f}%)."
            ),
            evidence_turns=[bt, min(bt + 1, total_turns), min(bt + 2, total_turns)],
        ))

    return recs


# ---------------------------------------------------------------------------
# Structural / architectural signals
# ---------------------------------------------------------------------------

def _recommend_structural(
    events: list[TurnEvent],
    total_turns: int,
) -> list[Recommendation]:
    recs: list[Recommendation] = []

    # Token growth: compare first-turn and last-turn prompt tokens
    if len(events) >= 4:
        first_tokens = events[0].prompt_tokens
        last_tokens = events[-1].prompt_tokens
        if first_tokens > 0 and last_tokens > first_tokens * 2.0:
            ratio = last_tokens / first_tokens
            recs.append(Recommendation(
                priority="medium",
                category="architecture",
                finding=(
                    f"Context window grew {ratio:.1f}x over the run "
                    f"({first_tokens} → {last_tokens} tokens). "
                    f"Latency scaled with context: first turn {events[0].llm_latency_ms}ms, "
                    f"last turn {events[-1].llm_latency_ms}ms."
                ),
                recommendation=(
                    "Implement a sliding context window: keep only the last 10 turns of "
                    "conversation history for the API call. Store full history locally for "
                    "tracing but cap what the LLM sees. Alternatively, inject a periodic "
                    "'SUMMARY: key facts so far' message and truncate before it."
                ),
                expected_impact=(
                    f"Caps token usage at ~{first_tokens * 3} input tokens instead of "
                    f"growing to {last_tokens}+. Stabilizes latency regardless of run length."
                ),
                evidence_turns=[events[-1].turn_number],
            ))

    # High failure rate on move actions
    move_events = [e for e in events if e.tool_name == "move"]
    if move_events:
        failed_moves = [e for e in move_events if not e.tool_success]
        fail_rate = len(failed_moves) / len(move_events)
        if fail_rate > 0.35:
            example_turns = [e.turn_number for e in failed_moves[:3]]
            recs.append(Recommendation(
                priority="high",
                category="prompt",
                finding=(
                    f"{len(failed_moves)} of {len(move_events)} move actions failed "
                    f"({fail_rate * 100:.0f}% failure rate). "
                    "Agents are repeatedly attempting to move into walls or locked doors."
                ),
                recommendation=(
                    "Add movement pre-check to the system prompt: before calling move(), "
                    "explicitly verify the target cell is not 'wall' in the adjacent_cells "
                    "observation. Add a rule: 'never move in a direction showing wall — "
                    "choose a different direction or wait.' Failed moves waste turns with "
                    "zero information gain."
                ),
                expected_impact=(
                    f"Eliminates {len(failed_moves)} wasted turns from wall collisions. "
                    "Agents that don't hit walls make faster progress toward objectives."
                ),
                evidence_turns=example_turns,
            ))

    return recs


# ---------------------------------------------------------------------------
# Prompt quality signals
# ---------------------------------------------------------------------------

def _recommend_prompt(events: list[TurnEvent]) -> list[Recommendation]:
    recs: list[Recommendation] = []

    # Belief parse failures
    parse_failures = [e for e in events if e.belief_state.extraction_failed]
    if parse_failures:
        n = len(parse_failures)
        turns = [e.turn_number for e in parse_failures[:5]]
        recs.append(Recommendation(
            priority="high",
            category="prompt",
            finding=(
                f"BELIEFS block failed to parse on {n} turn{'s' if n > 1 else ''} "
                f"(T{', T'.join(str(t) for t in turns)}). "
                "When parsing fails, belief divergence data is unavailable for those turns."
            ),
            recommendation=(
                "Add a concrete BELIEFS example with all fields filled to the system prompt. "
                "Add a validation rule: 'Your response is invalid if it does not begin with "
                "a BELIEFS block containing valid JSON.' Consider adding a retry loop that "
                "re-prompts with the error if extraction fails."
            ),
            expected_impact=(
                f"Eliminates {n} turns of missing belief data. "
                "Complete belief coverage is required for accurate divergence tracking."
            ),
            evidence_turns=turns,
        ))

    # No messages sent at all (missed coordination opportunity)
    messages_sent = sum(1 for e in events if e.tool_name == "send_message" and e.tool_success)
    if messages_sent == 0 and len(events) > 10:
        recs.append(Recommendation(
            priority="high",
            category="coordination",
            finding=(
                f"No messages were sent between agents across {len(events)} events. "
                "Agents explored independently with no information sharing — every belief "
                "about partner position/key/door had to come from direct observation."
            ),
            recommendation=(
                "Add an explicit coordination instruction to the system prompt: "
                "'If you find the key, door, or exit — send an immediate message to your "
                "partner with the location. If you haven't heard from your partner in 3 turns, "
                "send a status update.' Make messaging an expected behavior, not optional."
            ),
            expected_impact=(
                "Shared discoveries (key, door, exit locations) eliminate the largest class "
                "of stale-belief divergences. One message saves both agents redundant exploration."
            ),
            evidence_turns=[],
        ))

    return recs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_moves_toward(
    agent_id: str,
    start_turn: int,
    end_turn: int,
    events: list[TurnEvent],
) -> int:
    """Count move actions by agent during a turn window."""
    return sum(
        1 for e in events
        if e.agent_id == agent_id
        and e.tool_name == "move"
        and start_turn <= e.turn_number <= end_turn
    )


def _find_stuck_sequence(events: list[TurnEvent], min_length: int = 3) -> list[int]:
    """Find the longest consecutive run of waits/failures and return turn numbers."""
    best: list[int] = []
    current: list[int] = []

    for e in events:
        if e.tool_name == "wait" or not e.tool_success:
            current.append(e.turn_number)
        else:
            if len(current) >= min_length and len(current) > len(best):
                best = current[:]
            current = []

    if len(current) >= min_length and len(current) > len(best):
        best = current[:]

    return best
