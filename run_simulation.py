"""CLI entrypoint: run dungeon simulations and save structured logs.

Usage:
    python run_simulation.py [--seed 42] [--grid-size 8] [--max-turns 50] [--runs 1] [--model claude-sonnet-4-6]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

# Load .env before any SDK imports so all credentials are available
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

import anthropic

from dungeon.agent import DungeonAgent
from dungeon.game import GameRunner
from dungeon.tracing import save_run_log
from dungeon.world import DungeonWorld


def run_one(
    seed: int,
    grid_size: int,
    max_turns: int,
    model: str,
    output_dir: Path,
) -> dict:
    """Run a single simulation and save the log."""
    client = anthropic.Anthropic()

    # Create world
    world = DungeonWorld(size=grid_size, seed=seed)
    print(f"\n{'='*50}")
    print(f"Seed: {seed}")
    print(world.render_ascii())
    print()

    # Create agents
    agents = {
        "agent_a": DungeonAgent("agent_a", client, model=model),
        "agent_b": DungeonAgent("agent_b", client, model=model),
    }

    # Run the game
    runner = GameRunner(world, agents, max_turns=max_turns)
    run_log = runner.run()

    # Save to file
    output_file = save_run_log(run_log, output_dir)

    # Print summary
    m = run_log.manifest
    print(f"Run {m.run_id}: {m.outcome.value} in {m.total_turns} turns")
    print(f"  Tool calls: {m.summary_stats.tool_call_counts}")
    print(f"  Tokens used: {m.summary_stats.total_tokens_used}")
    print(f"  Key found: turn {m.summary_stats.key_found_turn}")
    print(f"  Door unlocked: turn {m.summary_stats.door_unlocked_turn}")
    print(f"  Messages sent: {m.summary_stats.messages_sent}")
    print(f"  Divergences: {m.summary_stats.belief_divergence_count}")
    print(f"  Saved to: {output_file}")

    return {"run_id": m.run_id, "outcome": m.outcome.value, "turns": m.total_turns}


def main():
    parser = argparse.ArgumentParser(description="Run dungeon agent simulations")
    parser.add_argument("--seed", type=int, default=None, help="Random seed (default: random)")
    parser.add_argument("--grid-size", type=int, default=8, help="Grid size (default: 8)")
    parser.add_argument("--max-turns", type=int, default=50, help="Max turns per run (default: 50)")
    parser.add_argument("--runs", type=int, default=1, help="Number of runs (default: 1)")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-6", help="Model to use")
    args = parser.parse_args()

    # Ensure output directory exists
    output_dir = Path(__file__).parent / "runs"
    output_dir.mkdir(exist_ok=True)

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable not set")
        print("Copy .env.example to .env and fill in your key, then: source .env")
        sys.exit(1)

    results = []
    for i in range(args.runs):
        seed = args.seed if args.seed is not None else random.randint(0, 2**31)
        if args.runs > 1 and args.seed is not None:
            seed = args.seed + i  # Different seed per run when doing multiple

        result = run_one(seed, args.grid_size, args.max_turns, args.model, output_dir)
        results.append(result)

    # Summary
    if len(results) > 1:
        print(f"\n{'='*50}")
        print(f"Completed {len(results)} runs:")
        for r in results:
            print(f"  {r['run_id']}: {r['outcome']} ({r['turns']} turns)")


if __name__ == "__main__":
    main()
