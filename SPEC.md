# Prove Engineering Take-Home: Dungeon Agents — Full Spec

## The Simulation
- 8×8 grid minimum. Items, locked door, key, random walls, exit.
- Fog of war: agents see only adjacent cells.
- Shared objective: both agents reach exit (one needs key to unlock door).
- Random starting positions.
- Agents shouldn't have more info than observable state provides.

## Tools (minimum set)
move(direction), look(), pick_up(item), check_coordinates(), check_inventory(),
use_item(item, target), send_message(agent, message)

## Game Loop
- Agents take turns. Each turn: agent gets observable state → picks a tool call.
- Messages delivered on FOLLOWING turn (not instant).
- Game ends: objective met, turn limit hit, or both agents stuck.
- "Think about how tool calls can mimic failures in real world system behaviors.
  Instrumentation should surface when tool outputs don't match expectations —
  but NOT by just logging a tool error."
- "The hardest bugs aren't crashes — they're agents making reasonable decisions
  based on information that's no longer true."

## The Traces (35% weight)
- Integrate Langfuse (or OpenTelemetry) for all agent traces.
- Capture: tool calls, LLM inputs/outputs, latency, full picture.
- ALSO log a structured event record at each agent step.
- Schema design is part of the exercise.
- "Consider what fields would actually help someone diagnose a failure after
  the fact across ALL runs, not just replay what happened in one trace manually."

## The Legibility Layer (30% weight)
Help a human answer THREE questions about a run:
1. **What happened?**
2. **Why did it happen?**
3. **What should change next?**

"Could be a turn-by-turn replay with belief state annotations, a causal incident report,
a timeline showing where agent beliefs diverged from reality, or something else entirely.
What you build and WHY is a major part of what we're evaluating."

"Keep it simple and lightweight. We are looking for intentionality — evidence that someone
made deliberate choices about what to show and how. Default AI styling is easy to spot.
Think 'someone built this with care on a deadline' not 'an AI generated a dashboard.'"

## Evaluation (priority order)
1. **Judgment** — smart scoping decisions, focus on right things
2. **Trace quality** — structured data clean, complete, well designed
3. **Legibility** — viewer actually helps a human understand what happened
4. **Taste** — output feels intentional, not generated
5. **AI collaboration** — conversation history shows driving AI toward good outcomes

## NOT evaluated
- Raw coding ability
- Polish level
- How fancy the dungeon is

## Submission
1. GitHub repo (commit history matters — don't squash)
2. Multiple run JSONs (mix of success and failure)
3. Short Loom (1-3 min) — decisions, not feature tour
4. Full AI conversation history (required)
