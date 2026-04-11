"""Dungeon world: grid generation, fog of war, and tool execution.

The world is the single source of truth. Agents interact only through
get_observable_state() and execute_tool(). This clean separation is what
makes belief tracking possible.
"""

from __future__ import annotations

import random
from collections import deque

from .schemas import (
    CellInfo,
    CellType,
    ObservableState,
    WorldConfig,
    WorldSnapshot,
)

# Direction vectors
DIRECTIONS: dict[str, tuple[int, int]] = {
    "north": (-1, 0),
    "south": (1, 0),
    "east": (0, 1),
    "west": (0, -1),
}


class DungeonWorld:
    """An 8x8+ dungeon grid with fog of war.

    Contains: walls, a key, a locked door, an exit, optional decoy items,
    and two agents. Agents can only see adjacent cells.
    """

    def __init__(self, size: int = 8, seed: int | None = None):
        self.size = size
        self.rng = random.Random(seed)
        self.seed = seed if seed is not None else random.randint(0, 2**31)

        # Grid: 2D list of CellType
        self.grid: list[list[CellType]] = []

        # Positions
        self.key_position: tuple[int, int] | None = None
        self.door_position: tuple[int, int] = (0, 0)
        self.exit_position: tuple[int, int] = (0, 0)
        self.wall_positions: list[tuple[int, int]] = []

        # Items on the ground: name -> position
        self.items: dict[str, tuple[int, int]] = {}

        # Agent state
        self.agent_positions: dict[str, tuple[int, int]] = {}
        self.agent_inventories: dict[str, list[str]] = {}

        # Door state
        self.door_locked: bool = True

        # Track which agents have reached the exit
        self.agents_at_exit: set[str] = set()

        self._generate()

    # ------------------------------------------------------------------
    # Grid generation
    # ------------------------------------------------------------------

    def _generate(self) -> None:
        """Generate a random but solvable dungeon."""
        rng = self.rng
        size = self.size

        # Start with all floor
        self.grid = [[CellType.FLOOR for _ in range(size)] for _ in range(size)]

        # Place walls (~15% of cells), ensuring connectivity
        floor_cells = [(r, c) for r in range(size) for c in range(size)]
        num_walls = int(len(floor_cells) * 0.15)

        # Try placing walls one at a time, checking connectivity each time
        placed_walls: list[tuple[int, int]] = []
        candidates = list(floor_cells)
        rng.shuffle(candidates)

        for pos in candidates:
            if len(placed_walls) >= num_walls:
                break
            self.grid[pos[0]][pos[1]] = CellType.WALL
            if self._is_connected():
                placed_walls.append(pos)
            else:
                # Undo — would break connectivity
                self.grid[pos[0]][pos[1]] = CellType.FLOOR

        self.wall_positions = placed_walls

        # Collect remaining floor cells for placement
        free = [
            (r, c)
            for r in range(size)
            for c in range(size)
            if self.grid[r][c] == CellType.FLOOR
        ]
        rng.shuffle(free)

        # Place key, door, exit, and agents on distinct floor cells
        # Need at least 6 free cells: key, door, exit, 2 agents, + buffer
        assert len(free) >= 6, "Not enough free cells after wall placement"

        key_pos = free.pop()
        door_pos = free.pop()
        exit_pos = free.pop()
        agent_a_pos = free.pop()
        agent_b_pos = free.pop()

        # Set door and exit cell types
        self.grid[door_pos[0]][door_pos[1]] = CellType.DOOR
        self.grid[exit_pos[0]][exit_pos[1]] = CellType.EXIT

        self.key_position = key_pos
        self.door_position = door_pos
        self.exit_position = exit_pos

        # Place the key as an item on the ground
        self.items["key"] = key_pos

        # Add 2-3 decoy items
        decoys = ["torch", "old_map", "rusty_compass"]
        num_decoys = min(rng.randint(2, 3), len(free))
        for i in range(num_decoys):
            self.items[decoys[i]] = free.pop()

        # Place agents
        self.agent_positions = {
            "agent_a": agent_a_pos,
            "agent_b": agent_b_pos,
        }
        self.agent_inventories = {
            "agent_a": [],
            "agent_b": [],
        }

    def _is_connected(self) -> bool:
        """BFS check that all floor/door/exit cells are reachable from each other."""
        size = self.size
        # Find the first non-wall cell
        start = None
        for r in range(size):
            for c in range(size):
                if self.grid[r][c] != CellType.WALL:
                    start = (r, c)
                    break
            if start:
                break

        if start is None:
            return False

        visited: set[tuple[int, int]] = set()
        queue: deque[tuple[int, int]] = deque([start])
        visited.add(start)

        while queue:
            r, c = queue.popleft()
            for dr, dc in DIRECTIONS.values():
                nr, nc = r + dr, c + dc
                if 0 <= nr < size and 0 <= nc < size and (nr, nc) not in visited:
                    if self.grid[nr][nc] != CellType.WALL:
                        visited.add((nr, nc))
                        queue.append((nr, nc))

        # Count total non-wall cells
        total = sum(
            1 for r in range(size) for c in range(size)
            if self.grid[r][c] != CellType.WALL
        )
        return len(visited) == total

    # ------------------------------------------------------------------
    # Observable state (fog of war)
    # ------------------------------------------------------------------

    def get_observable_state(self, agent_id: str) -> ObservableState:
        """Return only what the agent can see: current cell + 4 adjacent."""
        pos = self.agent_positions[agent_id]

        return ObservableState(
            position=pos,
            adjacent_cells=self._get_adjacent_cells(pos, agent_id),
            current_cell=self._cell_info(pos, agent_id),
            inventory=list(self.agent_inventories[agent_id]),
        )

    def _cell_info(self, pos: tuple[int, int], observer_id: str) -> CellInfo:
        """Build CellInfo for a single cell."""
        r, c = pos
        cell_type = self.grid[r][c]

        # Items at this cell
        items_here = [name for name, ipos in self.items.items() if ipos == pos]

        # Other agents at this cell
        agents_here = [
            aid for aid, apos in self.agent_positions.items()
            if apos == pos and aid != observer_id
        ]

        is_locked = self.door_locked if cell_type == CellType.DOOR else None

        return CellInfo(
            type=cell_type,
            items=items_here,
            agents=agents_here,
            is_locked=is_locked,
        )

    def _get_adjacent_cells(
        self, pos: tuple[int, int], observer_id: str
    ) -> dict[str, CellInfo]:
        """Get info about the 4 adjacent cells."""
        r, c = pos
        adjacent: dict[str, CellInfo] = {}

        for direction, (dr, dc) in DIRECTIONS.items():
            nr, nc = r + dr, c + dc
            if 0 <= nr < self.size and 0 <= nc < self.size:
                adjacent[direction] = self._cell_info((nr, nc), observer_id)
            else:
                # Edge of the grid — treat as wall
                adjacent[direction] = CellInfo(type=CellType.WALL)

        return adjacent

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def execute_tool(
        self, agent_id: str, tool_name: str, tool_input: dict
    ) -> tuple[dict, bool, str | None]:
        """Execute a tool call. Returns (result, success, failure_reason)."""
        handlers = {
            "move": self._exec_move,
            "look": self._exec_look,
            "pick_up": self._exec_pick_up,
            "check_coordinates": self._exec_check_coordinates,
            "check_inventory": self._exec_check_inventory,
            "use_item": self._exec_use_item,
            "send_message": self._exec_send_message,
            "wait": self._exec_wait,
        }

        handler = handlers.get(tool_name)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}, False, "unknown_tool"

        return handler(agent_id, tool_input)

    def _exec_move(
        self, agent_id: str, inp: dict
    ) -> tuple[dict, bool, str | None]:
        direction = inp.get("direction", "")
        if direction not in DIRECTIONS:
            return (
                {"error": f"Invalid direction: {direction}"},
                False,
                "invalid_direction",
            )

        r, c = self.agent_positions[agent_id]
        dr, dc = DIRECTIONS[direction]
        nr, nc = r + dr, c + dc

        # Bounds check
        if not (0 <= nr < self.size and 0 <= nc < self.size):
            return (
                {"error": "Cannot move: edge of the grid"},
                False,
                "out_of_bounds",
            )

        # Wall check
        if self.grid[nr][nc] == CellType.WALL:
            return (
                {"error": "Cannot move: wall in the way"},
                False,
                "wall_blocked",
            )

        # Locked door check
        if self.grid[nr][nc] == CellType.DOOR and self.door_locked:
            return (
                {"error": "Cannot move: the door is locked"},
                False,
                "door_locked",
            )

        # Move the agent
        self.agent_positions[agent_id] = (nr, nc)

        # Check if agent reached the exit
        if self.grid[nr][nc] == CellType.EXIT:
            self.agents_at_exit.add(agent_id)

        return (
            {"result": f"Moved {direction} to ({nr}, {nc})"},
            True,
            None,
        )

    def _exec_look(
        self, agent_id: str, _inp: dict
    ) -> tuple[dict, bool, str | None]:
        obs = self.get_observable_state(agent_id)
        result = {
            "position": list(obs.position),
            "current_cell": obs.current_cell.model_dump(),
            "adjacent": {
                d: c.model_dump() for d, c in obs.adjacent_cells.items()
            },
        }
        return result, True, None

    def _exec_pick_up(
        self, agent_id: str, inp: dict
    ) -> tuple[dict, bool, str | None]:
        item_name = inp.get("item", "")
        pos = self.agent_positions[agent_id]

        # Check if item is at the agent's current cell
        if item_name not in self.items or self.items[item_name] != pos:
            return (
                {"error": f"No '{item_name}' here to pick up"},
                False,
                "item_not_present",
            )

        # Pick up the item
        del self.items[item_name]
        self.agent_inventories[agent_id].append(item_name)

        # Special: if it's the key, clear key_position
        if item_name == "key":
            self.key_position = None

        return (
            {"result": f"Picked up {item_name}"},
            True,
            None,
        )

    def _exec_check_coordinates(
        self, agent_id: str, _inp: dict
    ) -> tuple[dict, bool, str | None]:
        pos = self.agent_positions[agent_id]
        return {"position": list(pos)}, True, None

    def _exec_check_inventory(
        self, agent_id: str, _inp: dict
    ) -> tuple[dict, bool, str | None]:
        inv = self.agent_inventories[agent_id]
        return {"inventory": list(inv)}, True, None

    def _exec_use_item(
        self, agent_id: str, inp: dict
    ) -> tuple[dict, bool, str | None]:
        item = inp.get("item", "")
        target = inp.get("target", "")

        # Check agent has the item
        if item not in self.agent_inventories[agent_id]:
            return (
                {"error": f"You don't have '{item}' in your inventory"},
                False,
                "item_not_in_inventory",
            )

        # Key on door
        if item == "key" and target == "door":
            # Check agent is adjacent to or on the door
            pos = self.agent_positions[agent_id]
            if not self._is_adjacent_or_on(pos, self.door_position):
                return (
                    {"error": "You are not near the door"},
                    False,
                    "not_near_target",
                )

            if not self.door_locked:
                return (
                    {"error": "The door is already unlocked"},
                    False,
                    "already_unlocked",
                )

            # Unlock the door
            self.door_locked = False
            self.agent_inventories[agent_id].remove("key")
            return (
                {"result": "Used key on door. The door is now unlocked!"},
                True,
                None,
            )

        return (
            {"error": f"Cannot use '{item}' on '{target}'"},
            False,
            "invalid_use",
        )

    def _exec_send_message(
        self, agent_id: str, inp: dict
    ) -> tuple[dict, bool, str | None]:
        # Message sending is handled by the game loop (message queue),
        # but the tool still "succeeds" from the agent's perspective.
        message = inp.get("message", "")
        target_agent = inp.get("agent", "")

        if target_agent == agent_id:
            return (
                {"error": "Cannot send a message to yourself"},
                False,
                "self_message",
            )

        if target_agent not in self.agent_positions:
            return (
                {"error": f"Unknown agent: {target_agent}"},
                False,
                "unknown_agent",
            )

        # The actual message delivery is handled by game.py
        return (
            {"result": f"Message sent to {target_agent}"},
            True,
            None,
        )

    def _exec_wait(
        self, agent_id: str, _inp: dict
    ) -> tuple[dict, bool, str | None]:
        return {"result": "You waited."}, True, None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_adjacent_or_on(
        self, pos: tuple[int, int], target: tuple[int, int]
    ) -> bool:
        if pos == target:
            return True
        r, c = pos
        for dr, dc in DIRECTIONS.values():
            if (r + dr, c + dc) == target:
                return True
        return False

    def get_snapshot(self) -> WorldSnapshot:
        """Full ground truth dump for logging."""
        return WorldSnapshot(
            grid=[[cell.value for cell in row] for row in self.grid],
            agent_positions=dict(self.agent_positions),
            agent_inventories={
                k: list(v) for k, v in self.agent_inventories.items()
            },
            key_position=self.key_position,
            key_holder=next(
                (
                    aid
                    for aid, inv in self.agent_inventories.items()
                    if "key" in inv
                ),
                None,
            ),
            door_locked=self.door_locked,
            door_position=self.door_position,
            exit_position=self.exit_position,
            items=dict(self.items),
        )

    def get_world_config(self) -> WorldConfig:
        """Return the initial configuration for the run manifest."""
        return WorldConfig(
            grid_size=(self.size, self.size),
            seed=self.seed,
            wall_positions=list(self.wall_positions),
            key_position=self.items.get("key", self.key_position or (0, 0)),
            door_position=self.door_position,
            exit_position=self.exit_position,
            item_positions=dict(self.items),
            agent_start_positions=dict(self.agent_positions),
        )

    def check_end_conditions(self) -> tuple[bool, str]:
        """Check if the game is over. Returns (is_over, reason)."""
        # Both agents at exit = success
        if len(self.agents_at_exit) == 2:
            return True, "success"
        return False, ""

    def render_ascii(self) -> str:
        """Debug rendering of the grid."""
        lines = []
        for r in range(self.size):
            row = []
            for c in range(self.size):
                pos = (r, c)
                # Check for agents first
                agent_here = [
                    aid for aid, apos in self.agent_positions.items()
                    if apos == pos
                ]
                if agent_here:
                    row.append("A" if "agent_a" in agent_here else "B")
                elif pos == self.key_position:
                    row.append("K")
                elif self.grid[r][c] == CellType.WALL:
                    row.append("#")
                elif self.grid[r][c] == CellType.DOOR:
                    row.append("D" if self.door_locked else "d")
                elif self.grid[r][c] == CellType.EXIT:
                    row.append("X")
                elif pos in self.items.values():
                    row.append("i")
                else:
                    row.append(".")
            lines.append(" ".join(row))
        return "\n".join(lines)
