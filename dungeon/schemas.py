"""Structured data models for the dungeon simulation, tracing, and legibility layer."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# World primitives
# ---------------------------------------------------------------------------

class CellType(str, Enum):
    FLOOR = "floor"
    WALL = "wall"
    DOOR = "door"
    EXIT = "exit"


class CellInfo(BaseModel):
    """What an agent can see about a single cell."""
    type: CellType
    items: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    is_locked: bool | None = None  # only relevant for DOOR cells


class ObservableState(BaseModel):
    """The slice of the world visible to one agent on one turn."""
    position: tuple[int, int]
    adjacent_cells: dict[str, CellInfo]  # direction -> cell info
    current_cell: CellInfo
    inventory: list[str]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class Message(BaseModel):
    from_agent: str
    to_agent: str
    content: str
    sent_turn: int
    delivered_turn: int  # always sent_turn + 1


class MessageContext(BaseModel):
    responding_to: Message | None = None
    turns_since_received: int = 0
    agent_acted_on_it: bool = False


# ---------------------------------------------------------------------------
# World snapshot (ground truth)
# ---------------------------------------------------------------------------

class WorldSnapshot(BaseModel):
    """Complete ground truth at a point in time."""
    grid: list[list[str]]  # cell types as strings
    agent_positions: dict[str, tuple[int, int]]
    agent_inventories: dict[str, list[str]]
    key_position: tuple[int, int] | None  # None if picked up
    key_holder: str | None
    door_locked: bool
    door_position: tuple[int, int]
    exit_position: tuple[int, int]
    items: dict[str, tuple[int, int]]  # remaining items on the ground


# ---------------------------------------------------------------------------
# Belief state (explicitly reported by the agent each turn)
# ---------------------------------------------------------------------------

class BeliefState(BaseModel):
    """What the agent explicitly reported believing this turn.

    Parsed from the mandatory BELIEFS block the agent outputs every response.
    extraction_failed=True means the agent didn't produce a parseable block —
    that itself is a diagnostic signal worth logging.
    """
    my_position: tuple[int, int] | None = None
    other_agent_position: tuple[int, int] | None = None
    key_location: str | None = None  # "at (3,4)", "agent_a has it", "I have it", "used on door"
    door_status: str | None = None   # "locked at (5,6)", "unlocked at (5,6)"
    exit_location: tuple[int, int] | None = None
    current_goal: str = "unknown"
    plan: str = "unknown"
    information_staleness: dict[str, int] = Field(default_factory=dict)
    extraction_failed: bool = False  # True = agent didn't output a parseable BELIEFS block


# ---------------------------------------------------------------------------
# Belief divergence
# ---------------------------------------------------------------------------

class DivergenceSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DivergenceCategory(str, Enum):
    STALE_INFORMATION = "stale_information"
    INCORRECT_INFERENCE = "incorrect_inference"
    MISSED_OBSERVATION = "missed_observation"
    COMMUNICATION_GAP = "communication_gap"
    NEVER_OBSERVED = "never_observed"


class BeliefDivergence(BaseModel):
    field: str
    believed_value: str
    actual_value: str
    staleness_turns: int = 0
    severity: DivergenceSeverity
    category: DivergenceCategory


# ---------------------------------------------------------------------------
# Turn event (the core trace record — one per agent per turn)
# ---------------------------------------------------------------------------

class TurnEvent(BaseModel):
    # Identity
    run_id: str
    turn_number: int
    agent_id: str
    timestamp: str  # ISO format

    # Inputs
    observable_state: ObservableState
    pending_messages: list[Message] = Field(default_factory=list)

    # Action
    tool_name: str
    tool_input: dict
    tool_output: dict
    tool_success: bool
    tool_failure_reason: str | None = None

    # LLM internals
    llm_reasoning: str = ""
    llm_latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # Belief vs reality
    belief_state: BeliefState = Field(default_factory=BeliefState)
    actual_world_state: WorldSnapshot
    divergences: list[BeliefDivergence] = Field(default_factory=list)

    # Decision quality — surfaces "reasonable decision on stale info"
    expected_tool_outcome: str | None = None   # "success" or "failure" given actual world
    outcome_matched_expectation: bool | None = None  # did reality match what should have happened?
    decision_info_age: int = 0  # max staleness (turns) of fields relevant to this action

    # Coordination
    message_sent: Message | None = None
    message_context: MessageContext | None = None


# ---------------------------------------------------------------------------
# Run-level models
# ---------------------------------------------------------------------------

class RunOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE_TURN_LIMIT = "failure_turn_limit"
    FAILURE_STUCK = "failure_stuck"
    FAILURE_ERROR = "failure_error"


class AgentConfig(BaseModel):
    agent_id: str
    model: str
    system_prompt_hash: str = ""  # short hash so we can detect prompt changes across runs


class WorldConfig(BaseModel):
    grid_size: tuple[int, int]
    seed: int
    wall_positions: list[tuple[int, int]]
    key_position: tuple[int, int]
    door_position: tuple[int, int]
    exit_position: tuple[int, int]
    item_positions: dict[str, tuple[int, int]]
    agent_start_positions: dict[str, tuple[int, int]]


class RunStats(BaseModel):
    total_llm_calls: int = 0
    total_tokens_used: int = 0
    total_tool_calls: int = 0
    tool_call_counts: dict[str, int] = Field(default_factory=dict)
    messages_sent: int = 0
    messages_received: int = 0
    key_found_turn: int | None = None
    door_unlocked_turn: int | None = None
    agents_reached_exit: list[str] = Field(default_factory=list)
    belief_divergence_count: int = 0
    peak_belief_staleness: int = 0
    coordination_failures: int = 0  # turns where agent acted on stale info and failed
    stale_decision_failures: int = 0  # outcome_matched_expectation=False AND decision_info_age > 2


# ---------------------------------------------------------------------------
# Run diagnosis (post-hoc analysis of what went wrong)
# ---------------------------------------------------------------------------

class FailureMode(str, Enum):
    STALE_BELIEFS = "stale_beliefs"
    POOR_COORDINATION = "poor_coordination"
    EXPLORATION_INEFFICIENCY = "exploration_inefficiency"
    STUCK_LOOP = "stuck_loop"
    NONE = "none"


class RunDiagnosis(BaseModel):
    """Post-hoc analysis of a run. Answers: what went wrong and why."""
    primary_failure_mode: FailureMode
    bottleneck_turn: int | None = None  # the turn where the run diverged from optimal
    bottleneck_agent: str | None = None
    stale_decision_rate: float = 0.0  # fraction of turns with decision_info_age > threshold
    avg_divergences_per_turn: float = 0.0
    coordination_gap_turns: int = 0  # turns where agents had contradictory beliefs about key/door
    wasted_turns: int = 0  # wait + failed moves
    key_insights: list[str] = Field(default_factory=list)  # human-readable bullets


class RunManifest(BaseModel):
    run_id: str
    started_at: str
    ended_at: str | None = None
    outcome: RunOutcome
    total_turns: int = 0
    seed: int
    grid_size: tuple[int, int]
    world_config: WorldConfig
    agent_configs: dict[str, AgentConfig] = Field(default_factory=dict)
    summary_stats: RunStats = Field(default_factory=RunStats)
    langfuse_trace_id: str | None = None   # for proxy endpoint + deep-link
    langfuse_trace_url: str | None = None  # direct URL to Langfuse dashboard


# ---------------------------------------------------------------------------
# Causal chain (simplified: when did a belief go stale and how long did it last)
# ---------------------------------------------------------------------------

class FieldStalenessWindow(BaseModel):
    """One continuous period where an agent's belief about a field was wrong.

    Answers three questions:
    - When was the belief last correct? (last_correct_turn)
    - When did ground truth change? (ground_truth_changed_turn)
    - How long did the agent act on wrong info? (duration_turns)
    """
    field: str           # which belief field: key_location, door_status, other_agent_position
    agent_id: str
    believed_value: str  # what the agent believed during the window
    actual_value: str    # what was actually true

    last_correct_turn: int | None    # last turn before stale window when belief matched reality
    ground_truth_changed_turn: int   # turn when actual world state changed (causing divergence)
    stale_start_turn: int            # first turn with active divergence
    stale_end_turn: int | None       # turn divergence resolved (None = never resolved by end of run)
    duration_turns: int              # stale_end - stale_start (or run_end - stale_start if unresolved)


class CausalChain(BaseModel):
    """Simplified causal chain for a run: all stale windows across all fields and agents."""
    run_id: str
    windows: list[FieldStalenessWindow] = Field(default_factory=list)
    total_stale_agent_turns: int = 0  # sum of all window durations (can double-count across fields)
    worst_window: FieldStalenessWindow | None = None  # longest single stale window
    summary: str = ""  # one human-readable line for the run detail header


# ---------------------------------------------------------------------------
# Recommendations (what should change next)
# ---------------------------------------------------------------------------

class Recommendation(BaseModel):
    """One concrete, evidence-backed change recommendation.

    priority: critical = root cause of failure | high = major contributor | medium = improvement
    category: what kind of change is needed
    finding: what the data showed, with specific turn numbers
    recommendation: the concrete change to make
    expected_impact: what metric/behavior improves
    evidence_turns: turn numbers to jump to in the replay
    """
    priority: str  # "critical" | "high" | "medium"
    category: str  # "coordination" | "prompt" | "architecture" | "exploration"
    finding: str
    recommendation: str
    expected_impact: str
    evidence_turns: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Full run log (what gets serialized to runs/{run_id}.json)
# ---------------------------------------------------------------------------

class RunLog(BaseModel):
    manifest: RunManifest
    events: list[TurnEvent] = Field(default_factory=list)
    diagnosis: RunDiagnosis | None = None
    causal_chain: CausalChain | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
