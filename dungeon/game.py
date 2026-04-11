"""Game loop: turn-based orchestration of two agents in a dungeon.

Handles turn order, message queuing (1-turn delay), end conditions,
and event collection. Tracing hooks are called but not defined here —
they're injected via callbacks to keep the game loop clean.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from langfuse import observe

from .agent import DungeonAgent
from .schemas import (
    AgentConfig,
    Message,
    RunLog,
    RunManifest,
    RunOutcome,
    RunStats,
    TurnEvent,
    WorldSnapshot,
)
from .tracing import compute_divergences
from .world import DungeonWorld


class GameRunner:
    """Orchestrates a full dungeon game between two agents."""

    def __init__(
        self,
        world: DungeonWorld,
        agents: dict[str, DungeonAgent],
        max_turns: int = 50,
    ):
        self.world = world
        self.agents = agents
        self.max_turns = max_turns
        self.run_id = str(uuid.uuid4())[:8]

        # Message queue: messages sent this turn, pending delivery next turn
        # Keyed by recipient agent_id
        self._outbox: list[Message] = []  # sent this turn, deliver next turn
        self._inbox: dict[str, list[Message]] = {aid: [] for aid in agents}

        # Event collection
        self.events: list[TurnEvent] = []

        # Track stuck detection: consecutive no-op turns per agent
        self._stuck_counter: dict[str, int] = {aid: 0 for aid in agents}
        self._stuck_threshold = 5  # 5 consecutive waits or failed moves = stuck

    @observe(name="dungeon_game")
    def run(self) -> RunLog:
        """Execute the full game. Returns a RunLog with manifest + events."""
        started_at = datetime.now(timezone.utc).isoformat()
        outcome = RunOutcome.FAILURE_TURN_LIMIT
        turn_order = list(self.agents.keys())  # agent_a, agent_b

        for turn in range(1, self.max_turns + 1):
            # Deliver messages from last turn
            self._deliver_messages()

            for agent_id in turn_order:
                agent = self.agents[agent_id]

                # Get observable state
                observable = self.world.get_observable_state(agent_id)

                # Get pending messages for this agent
                pending = list(self._inbox[agent_id])
                self._inbox[agent_id] = []

                # Agent takes a turn
                tool_call = agent.take_turn(observable, pending, turn)

                # Execute the tool in the world
                result, success, failure_reason = self.world.execute_tool(
                    agent_id, tool_call.name, tool_call.input
                )

                # Feed result back to agent's conversation
                agent.receive_tool_result(tool_call.name, result, success)

                # Handle message sending
                message_sent = None
                if tool_call.name == "send_message" and success:
                    msg = Message(
                        from_agent=agent_id,
                        to_agent=tool_call.input.get("agent", ""),
                        content=tool_call.input.get("message", ""),
                        sent_turn=turn,
                        delivered_turn=turn + 1,
                    )
                    self._outbox.append(msg)
                    message_sent = msg

                # Get world snapshot for ground truth
                snapshot = self.world.get_snapshot()

                # Belief comes directly from the agent's mandatory BELIEFS block
                belief = tool_call.belief
                staleness = agent.get_staleness(turn)
                belief.information_staleness = staleness
                divergences = compute_divergences(agent_id, belief, snapshot, staleness)

                event = TurnEvent(
                    run_id=self.run_id,
                    turn_number=turn,
                    agent_id=agent_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    observable_state=observable,
                    pending_messages=pending,
                    tool_name=tool_call.name,
                    tool_input=tool_call.input,
                    tool_output=result,
                    tool_success=success,
                    tool_failure_reason=failure_reason,
                    llm_reasoning=tool_call.reasoning,
                    llm_latency_ms=tool_call.latency_ms,
                    prompt_tokens=tool_call.prompt_tokens,
                    completion_tokens=tool_call.completion_tokens,
                    belief_state=belief,
                    actual_world_state=snapshot,
                    divergences=divergences,
                    message_sent=message_sent,
                )
                self.events.append(event)

                # Stuck detection
                if tool_call.name == "wait" or not success:
                    self._stuck_counter[agent_id] += 1
                else:
                    self._stuck_counter[agent_id] = 0

                # Check end conditions
                game_over, reason = self.world.check_end_conditions()
                if game_over:
                    outcome = RunOutcome.SUCCESS
                    break

            # Check if game ended this turn
            if outcome == RunOutcome.SUCCESS:
                break

            # Check if both agents are stuck
            if all(
                c >= self._stuck_threshold
                for c in self._stuck_counter.values()
            ):
                outcome = RunOutcome.FAILURE_STUCK
                break

        ended_at = datetime.now(timezone.utc).isoformat()
        total_turns = self.events[-1].turn_number if self.events else 0

        # Build manifest
        manifest = RunManifest(
            run_id=self.run_id,
            started_at=started_at,
            ended_at=ended_at,
            outcome=outcome,
            total_turns=total_turns,
            seed=self.world.seed,
            grid_size=(self.world.size, self.world.size),
            world_config=self.world.get_world_config(),
            agent_configs={
                aid: AgentConfig(
                    agent_id=aid,
                    model=agent.model,
                )
                for aid, agent in self.agents.items()
            },
            summary_stats=self._compute_stats(),
        )

        return RunLog(manifest=manifest, events=self.events)

    def _deliver_messages(self) -> None:
        """Move outbox messages to recipients' inboxes."""
        for msg in self._outbox:
            if msg.to_agent in self._inbox:
                self._inbox[msg.to_agent].append(msg)
        self._outbox = []

    def _compute_stats(self) -> RunStats:
        """Aggregate events into summary stats."""
        stats = RunStats()
        stats.total_llm_calls = len(self.events)
        stats.total_tool_calls = len(self.events)

        tool_counts: dict[str, int] = {}
        total_tokens = 0

        for event in self.events:
            tool_counts[event.tool_name] = tool_counts.get(event.tool_name, 0) + 1
            total_tokens += event.prompt_tokens + event.completion_tokens

            # Track key events
            if event.tool_name == "pick_up" and event.tool_success:
                if event.tool_input.get("item") == "key":
                    if stats.key_found_turn is None:
                        stats.key_found_turn = event.turn_number

            if event.tool_name == "use_item" and event.tool_success:
                if event.tool_input.get("item") == "key":
                    if stats.door_unlocked_turn is None:
                        stats.door_unlocked_turn = event.turn_number

            if event.tool_name == "send_message" and event.tool_success:
                stats.messages_sent += 1

            # Divergence stats
            stats.belief_divergence_count += len(event.divergences)
            for div in event.divergences:
                if div.staleness_turns > stats.peak_belief_staleness:
                    stats.peak_belief_staleness = div.staleness_turns

        stats.tool_call_counts = tool_counts
        stats.total_tokens_used = total_tokens

        # Check which agents reached exit
        for aid in self.agents:
            pos = self.world.agent_positions[aid]
            if pos == self.world.exit_position:
                stats.agents_reached_exit.append(aid)

        return stats
