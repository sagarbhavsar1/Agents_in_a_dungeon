# Dungeon Agents

Two AI agents explore a dungeon together. The dungeon is simple — the real product is the **structured traces** and the **legibility layer** that helps a human diagnose what happened and why.

**Model:** Claude Sonnet (agents) + Claude Haiku (belief extraction)
**Framework:** Custom-built, no LangChain
**Tracing:** Langfuse + custom structured event logs
**UI:** FastAPI + vanilla HTML/JS/CSS

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pydantic anthropic langfuse fastapi uvicorn
```

Create a `.env` file:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export LANGFUSE_PUBLIC_KEY=pk-lf-...    # optional
export LANGFUSE_SECRET_KEY=sk-lf-...    # optional
export LANGFUSE_HOST=https://cloud.langfuse.com  # optional
```

Then source it:

```bash
source .env
```

## Run a simulation

```bash
python run_simulation.py --seed 42 --max-turns 50
```

Options:
- `--seed N` — reproducible grid generation (default: random)
- `--grid-size N` — grid dimensions (default: 8)
- `--max-turns N` — turn limit (default: 50)
- `--runs N` — number of simulations (default: 1)
- `--model MODEL` — Claude model ID (default: claude-sonnet-4-20250514)

Each run saves a structured JSON file to `runs/`.

## View the UI

```bash
python -m uvicorn server.app:app --port 8000
```

Open http://localhost:8000. The run list shows all completed simulations. Click a run to see:

- **Grid replay** — turn-by-turn with fog of war, path traces, and agent positions
- **Beliefs vs Reality** — per-agent table showing where beliefs diverge from ground truth, with staleness tracking
- **Timeline** — divergence markers (height = severity), milestone events, clickable turn navigation
- **Transport controls** — first/prev/play/next/last, keyboard shortcuts (arrows, space)

## Architecture

```
dungeon/
  schemas.py    — Pydantic models (TurnEvent, BeliefState, BeliefDivergence, ...)
  world.py      — 8x8 grid, fog of war, tool execution
  tools.py      — 8 tools in Anthropic API format
  agent.py      — LLM agent with tool calling + staleness tracking
  game.py       — Turn-based game loop, message queue
  tracing.py    — Langfuse integration, belief extraction, divergence computation
server/
  app.py        — FastAPI app
  routes.py     — Read-only API over run JSON files
static/         — Vanilla frontend (dark theme, canvas grid, no frameworks)
```

## Key design decisions

**Belief extraction via separate Haiku call.** After each agent turn, a cheap Haiku call extracts structured beliefs from the agent's reasoning text. This is completely isolated from the agent's conversation — the agent's behavior is never contaminated by the observability layer.

**Staleness as a first-class field.** Each belief field tracks how many turns since the agent last directly observed it. An agent acting on 8-turn-old key location info is exactly the class of bug the legibility layer surfaces.

**Divergence categories.** Each belief mismatch is classified as: stale_information, incorrect_inference, missed_observation, communication_gap, or never_observed. This enables cross-run filtering ("show me all failures caused by stale info").

**`wait()` tool added.** Without it, stuck agents make noisy moves. A wait call is a clean diagnostic signal.

## Event schema

Each turn produces a `TurnEvent` containing:
- What the agent saw (observable state, pending messages)
- What it did (tool call, result, success/failure)
- What it was thinking (LLM reasoning, latency, tokens)
- What it believed (extracted BeliefState)
- What was actually true (WorldSnapshot)
- Where beliefs diverged (list of BeliefDivergence records)
