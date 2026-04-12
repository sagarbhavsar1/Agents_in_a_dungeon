"""Mock simulation runner — full end-to-end without Anthropic API calls.

Two scripted agents solve the dungeon using BFS navigation over their
observed map. The full game loop, divergence engine, and Langfuse
tracing all run exactly as they would in production.

Usage:
    python mock_run.py [--seed 42] [--runs 1]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

# Load .env before Langfuse initializes so credentials are available
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from langfuse import get_client, observe

from dungeon.agent import ToolCall
from dungeon.game import GameRunner
from dungeon.schemas import BeliefState, Message, ObservableState
from dungeon.tracing import save_run_log
from dungeon.world import DungeonWorld

langfuse = get_client()

# ── Direction helpers ────────────────────────────────────────────────────────

DELTAS: dict[str, tuple[int, int]] = {
    "north": (-1, 0),
    "south": (1, 0),
    "east": (0, 1),
    "west": (0, -1),
}


# ── Mock agent ───────────────────────────────────────────────────────────────

class MockAgent:
    """
    Scripted dungeon agent — no LLM, same interface as DungeonAgent.

    Strategy (in priority order each turn):
      1. Pick up key if standing on it
      2. Use key on adjacent locked door
      3. Navigate to key (if known and don't have it)
      4. Navigate to door (if key in hand and door known)
      5. Navigate to exit (if door unlocked and exit known)
      6. Explore: prefer unvisited cells, use BFS to reach them
      7. Send one message when finding the key (cooperation signal)
    """

    def __init__(self, agent_id: str, model: str = "mock-agent-v1"):
        self.agent_id = agent_id
        self.other_agent_id = "agent_b" if agent_id == "agent_a" else "agent_a"
        self.model = model

        # Memory built from observations (fog-of-war compliant)
        self.known_cells: dict[tuple[int, int], str] = {}  # pos -> cell_type
        self.visited: set[tuple[int, int]] = set()
        self.known_key_pos: tuple[int, int] | None = None
        self.known_door_pos: tuple[int, int] | None = None
        self.known_exit_pos: tuple[int, int] | None = None
        self.door_unlocked: bool = False
        self.key_message_sent: bool = False

        # Staleness tracking — same as DungeonAgent
        self.last_observed: dict[str, int | None] = {
            "my_position": None,
            "other_agent_position": None,
            "key_location": None,
            "door_status": None,
            "exit_location": None,
        }

        # Required by GameRunner (no-op here)
        self.messages: list = []

    # ── Main interface ───────────────────────────────────────────────────────

    @observe(name="agent_turn", as_type="generation")
    def take_turn(
        self,
        observable_state: ObservableState,
        pending_messages: list[Message],
        turn_number: int,
    ) -> ToolCall:
        start = time.monotonic()

        self._update_knowledge(observable_state, turn_number)
        tool_name, tool_input, reasoning = self._decide(observable_state, turn_number)
        belief = self._build_belief(observable_state)

        latency_ms = int((time.monotonic() - start) * 1000)

        # Annotate the Langfuse span with mock agent metadata
        langfuse.update_current_generation(
            name=f"{self.agent_id}_turn_{turn_number}",
            model=self.model,
            input={"turn": turn_number, "position": observable_state.position,
                   "inventory": observable_state.inventory},
            output={"tool": tool_name, "args": tool_input, "reasoning": reasoning},
            usage_details={"input": 0, "output": 0},
            metadata={
                "agent_id": self.agent_id,
                "turn_number": turn_number,
                "tool_called": tool_name,
                "belief_extraction_failed": False,
                "is_mock": True,
            },
        )

        return ToolCall(
            name=tool_name,
            input=tool_input,
            reasoning=reasoning,
            belief=belief,
            belief_parse_failed=False,
            latency_ms=latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
        )

    def receive_tool_result(self, tool_name: str, result: dict, success: bool) -> None:
        """No conversation history to update — mock agents are stateless per turn."""
        # Update door unlock state from tool result
        if tool_name == "use_item" and success:
            if "unlocked" in result.get("result", ""):
                self.door_unlocked = True

    def get_staleness(self, current_turn: int) -> dict[str, int]:
        result = {}
        for fname, last_turn in self.last_observed.items():
            result[fname] = -1 if last_turn is None else current_turn - last_turn
        return result

    # ── Knowledge update ─────────────────────────────────────────────────────

    def _update_knowledge(self, obs: ObservableState, turn: int) -> None:
        pos = obs.position
        self.visited.add(pos)
        self.known_cells[pos] = obs.current_cell.type.value
        self.last_observed["my_position"] = turn

        # Scan current cell
        if "key" in obs.current_cell.items:
            self.known_key_pos = pos
            self.last_observed["key_location"] = turn
        if obs.current_cell.type.value == "door":
            self.known_door_pos = pos
            self.last_observed["door_status"] = turn
            if obs.current_cell.is_locked is False:
                self.door_unlocked = True
        if obs.current_cell.type.value == "exit":
            self.known_exit_pos = pos
            self.last_observed["exit_location"] = turn
        if "key" in obs.inventory:
            self.last_observed["key_location"] = turn

        # Scan adjacent cells
        for direction, cell in obs.adjacent_cells.items():
            dr, dc = DELTAS[direction]
            cell_pos = (pos[0] + dr, pos[1] + dc)
            self.known_cells[cell_pos] = cell.type.value

            if cell.agents:
                self.last_observed["other_agent_position"] = turn
            if "key" in cell.items:
                self.known_key_pos = cell_pos
                self.last_observed["key_location"] = turn
            if cell.type.value == "door":
                self.known_door_pos = cell_pos
                self.last_observed["door_status"] = turn
                if cell.is_locked is False:
                    self.door_unlocked = True
            if cell.type.value == "exit":
                self.known_exit_pos = cell_pos
                self.last_observed["exit_location"] = turn

    # ── Decision logic ───────────────────────────────────────────────────────

    def _decide(
        self, obs: ObservableState, turn: int
    ) -> tuple[str, dict, str]:
        pos = obs.position
        inventory = obs.inventory

        # Find adjacent door and its lock status from current observation
        adj_door_dir = None
        adj_door_locked = True
        for direction, cell in obs.adjacent_cells.items():
            if cell.type.value == "door":
                adj_door_dir = direction
                adj_door_locked = cell.is_locked if cell.is_locked is not None else True
        # Also check if standing on door
        if obs.current_cell.type.value == "door":
            adj_door_locked = obs.current_cell.is_locked if obs.current_cell.is_locked is not None else True

        # 1. Pick up key if standing on it
        if "key" in obs.current_cell.items and "key" not in inventory:
            return "pick_up", {"item": "key"}, (
                f"Key is right here at {pos}! Picking it up."
            )

        # 2. Use key on adjacent locked door
        if "key" in inventory and adj_door_dir and adj_door_locked:
            return "use_item", {"item": "key", "target": "door"}, (
                f"Have key, door is to the {adj_door_dir}. Unlocking it."
            )

        # 2b. Standing on locked door with key
        if "key" in inventory and obs.current_cell.type.value == "door" and adj_door_locked:
            return "use_item", {"item": "key", "target": "door"}, (
                "Standing on locked door with key in hand. Unlocking."
            )

        # 3. Send message when key just picked up (one-time coordination signal)
        if "key" in inventory and not self.key_message_sent:
            self.key_message_sent = True
            return "send_message", {
                "agent": self.other_agent_id,
                "message": f"I found and picked up the key at {pos}. I'll unlock the door. Head toward the exit once I open it.",
            }, "Informing partner that I have the key."

        # 4. Navigate toward door (have key, know door location, door still locked)
        if "key" in inventory and self.known_door_pos and not self.door_unlocked:
            move = self._bfs_move(pos, self.known_door_pos, obs)
            if move:
                return "move", {"direction": move}, (
                    f"Have key. Moving {move} toward door at {self.known_door_pos}."
                )

        # 5. Navigate toward exit (door unlocked, exit known)
        if self.door_unlocked and self.known_exit_pos:
            if pos == self.known_exit_pos:
                return "wait", {}, "Already at exit — waiting for partner."
            move = self._bfs_move(pos, self.known_exit_pos, obs)
            if move:
                return "move", {"direction": move}, (
                    f"Door unlocked! Moving {move} toward exit at {self.known_exit_pos}."
                )

        # 6. Navigate toward key (know where it is, don't have it, door not yet open)
        if self.known_key_pos and "key" not in inventory and not self.door_unlocked:
            move = self._bfs_move(pos, self.known_key_pos, obs)
            if move:
                return "move", {"direction": move}, (
                    f"Moving {move} toward key at {self.known_key_pos}."
                )

        # 7. Explore — find unvisited cells
        return self._explore(pos, obs)

    def _bfs_move(
        self,
        start: tuple[int, int],
        target: tuple[int, int],
        obs: ObservableState,
    ) -> str | None:
        """BFS over known+observable cells. Returns first direction toward target."""
        if start == target:
            return None

        queue: deque[tuple[tuple[int, int], list[str]]] = deque([(start, [])])
        seen: set[tuple[int, int]] = {start}

        while queue:
            (r, c), path = queue.popleft()
            for direction, (dr, dc) in DELTAS.items():
                npos = (r + dr, c + dc)
                if npos in seen:
                    continue
                seen.add(npos)

                cell_type = self.known_cells.get(npos, "unknown")
                if cell_type == "wall":
                    continue  # never passable

                new_path = path + [direction]
                if npos == target:
                    return new_path[0] if new_path else None

                # Traverse floor, door (unlocked or unknown lock), exit, unknown
                if cell_type in ("floor", "exit", "unknown"):
                    queue.append((npos, new_path))
                elif cell_type == "door":
                    # Only traverse if unlocked
                    if self.door_unlocked:
                        queue.append((npos, new_path))

        # No known path — move in direction of target by Manhattan heuristic
        return self._heuristic_move(start, target, obs)

    def _heuristic_move(
        self,
        pos: tuple[int, int],
        target: tuple[int, int],
        obs: ObservableState,
    ) -> str | None:
        """Fallback: move toward target ignoring walls (for unexplored territory)."""
        dr = target[0] - pos[0]
        dc = target[1] - pos[1]

        candidates = []
        if dr < 0:
            candidates.append("north")
        elif dr > 0:
            candidates.append("south")
        if dc > 0:
            candidates.append("east")
        elif dc < 0:
            candidates.append("west")

        # Try candidates in order, skip walls
        for direction in candidates:
            cell = obs.adjacent_cells.get(direction)
            if cell and cell.type.value != "wall":
                return direction

        # Try any non-wall direction
        for direction, cell in obs.adjacent_cells.items():
            if cell.type.value != "wall":
                return direction

        return None

    def _explore(
        self, pos: tuple[int, int], obs: ObservableState
    ) -> tuple[str, dict, str]:
        """BFS across the known map toward the nearest unvisited/unknown cell."""
        # First try adjacent unvisited cells (fast path)
        for direction, cell in obs.adjacent_cells.items():
            dr, dc = DELTAS[direction]
            npos = (pos[0] + dr, pos[1] + dc)
            if cell.type.value in ("wall",):
                continue
            if cell.type.value == "door" and not self.door_unlocked:
                continue
            if npos not in self.visited:
                return "move", {"direction": direction}, (
                    f"Exploring: moving {direction} into unvisited cell."
                )

        # All adjacent cells visited — BFS across known map to nearest frontier
        move = self._bfs_to_frontier(pos)
        if move:
            return "move", {"direction": move}, (
                f"Backtracking {move} toward nearest unexplored region."
            )

        # Frontier exhausted on this side of the dungeon.
        # If we know the door location but think it's locked, navigate there to
        # observe its current state — partner may have unlocked it already.
        if self.known_door_pos and not self.door_unlocked:
            move = self._bfs_move(pos, self.known_door_pos, obs)
            if move:
                return "move", {"direction": move}, (
                    "All reachable cells explored. Checking door — partner may have unlocked it."
                )

        # Everything reachable has been visited
        for direction, cell in obs.adjacent_cells.items():
            if cell.type.value != "wall":
                if cell.type.value != "door" or self.door_unlocked:
                    return "move", {"direction": direction}, "Fully explored — moving to stay active."

        return "wait", {}, "No accessible adjacent cells — waiting."

    def _bfs_to_frontier(self, pos: tuple[int, int]) -> str | None:
        """BFS across fully known cells to find nearest unvisited or unknown cell."""
        queue: deque[tuple[tuple[int, int], list[str]]] = deque([(pos, [])])
        seen: set[tuple[int, int]] = {pos}

        while queue:
            (r, c), path = queue.popleft()
            for direction, (dr, dc) in DELTAS.items():
                npos = (r + dr, c + dc)
                if npos in seen:
                    continue
                seen.add(npos)

                cell_type = self.known_cells.get(npos, "unknown")
                if cell_type == "wall":
                    continue
                if cell_type == "door" and not self.door_unlocked:
                    continue

                new_path = path + [direction]

                # Frontier: unvisited known cell or totally unknown cell
                if npos not in self.visited or cell_type == "unknown":
                    return new_path[0] if new_path else None

                queue.append((npos, new_path))

        return None

    # ── Belief state ─────────────────────────────────────────────────────────

    def _build_belief(self, obs: ObservableState) -> BeliefState:
        inventory = obs.inventory

        if "key" in inventory:
            key_loc = "I have it"
        elif self.known_key_pos:
            key_loc = f"at {list(self.known_key_pos)}"
        elif self.door_unlocked:
            key_loc = "used on door"
        else:
            key_loc = None

        if self.known_door_pos:
            status = "unlocked" if self.door_unlocked else "locked"
            door_status = f"{status} at {list(self.known_door_pos)}"
        else:
            door_status = None

        return BeliefState(
            my_position=obs.position,
            other_agent_position=None,  # fog of war — only visible if adjacent
            key_location=key_loc,
            door_status=door_status,
            exit_location=list(self.known_exit_pos) if self.known_exit_pos else None,
            current_goal=self._goal_description(obs),
            plan=self._plan_description(obs),
            extraction_failed=False,
        )

    def _goal_description(self, obs: ObservableState) -> str:
        inventory = obs.inventory
        if "key" in inventory and not self.door_unlocked:
            return "Unlock the door with the key I'm carrying"
        if self.door_unlocked:
            return "Reach the exit"
        if self.known_key_pos and "key" not in inventory:
            return f"Navigate to and pick up the key at {self.known_key_pos}"
        return "Explore dungeon to find the key"

    def _plan_description(self, obs: ObservableState) -> str:
        inventory = obs.inventory
        if "key" in inventory and self.known_door_pos and not self.door_unlocked:
            return f"BFS toward door at {self.known_door_pos}, then use key"
        if self.door_unlocked and self.known_exit_pos:
            return f"BFS toward exit at {self.known_exit_pos}"
        if self.known_key_pos and "key" not in inventory:
            return f"BFS toward key at {self.known_key_pos}"
        return "Explore adjacent unvisited cells systematically"


# ── Runner ───────────────────────────────────────────────────────────────────

@observe(name="mock_run")
def run_mock(seed: int, output_dir: Path) -> dict:
    """Run one full mock simulation."""
    world = DungeonWorld(size=8, seed=seed)

    print(f"\n{'='*50}")
    print(f"Seed: {seed}")
    print(world.render_ascii())

    agents = {
        "agent_a": MockAgent("agent_a"),
        "agent_b": MockAgent("agent_b"),
    }

    runner = GameRunner(world, agents, max_turns=200)
    run_log = runner.run()

    output_file = save_run_log(run_log, output_dir)

    m = run_log.manifest
    print(f"\nRun {m.run_id}: {m.outcome.value} in {m.total_turns} turns")
    print(f"  Key found:       turn {m.summary_stats.key_found_turn}")
    print(f"  Door opened:     turn {m.summary_stats.door_unlocked_turn}")
    print(f"  Messages sent:   {m.summary_stats.messages_sent}")
    print(f"  Divergences:     {m.summary_stats.belief_divergence_count}")
    print(f"  Stale failures:  {m.summary_stats.stale_decision_failures}")
    if run_log.diagnosis:
        d = run_log.diagnosis
        print(f"  Failure mode:    {d.primary_failure_mode.value}")
        print(f"  Wasted turns:    {d.wasted_turns}")
        for insight in d.key_insights:
            print(f"    • {insight}")
    print(f"  Saved to:        {output_file}")

    langfuse.flush()
    return {"run_id": m.run_id, "outcome": m.outcome.value, "turns": m.total_turns}


def main():
    parser = argparse.ArgumentParser(description="Mock dungeon simulation (no LLM)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    output_dir = Path(__file__).parent / "runs"
    output_dir.mkdir(exist_ok=True)

    results = []
    for i in range(args.runs):
        seed = args.seed + i if args.seed is not None else random.randint(0, 2**31)
        results.append(run_mock(seed, output_dir))

    if len(results) > 1:
        print(f"\n{'='*50}")
        wins = sum(1 for r in results if r["outcome"] == "success")
        print(f"Completed {len(results)} runs: {wins} success, {len(results)-wins} failure")
        for r in results:
            print(f"  {r['run_id']}: {r['outcome']} ({r['turns']} turns)")


if __name__ == "__main__":
    main()
