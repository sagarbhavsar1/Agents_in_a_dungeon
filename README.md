# Dungeon Agents

Two LLM agents cooperate inside an 8×8 dungeon: find the key, unlock the door, get both agents to the exit. The dungeon is intentionally simple — the real product is the **structured trace layer** and the **legibility UI** that let a human answer the three questions that matter:

1. **What happened?** — turn-by-turn replay with live agent chat and TTS voices
2. **Why did it happen?** — failure-mode diagnosis + causal chain of stale-belief windows
3. **What should change next?** — evidence-backed, priority-sorted recommendations

**Stack:** Python · Anthropic SDK (no LangChain) · Langfuse · FastAPI · vanilla HTML/JS/CSS · Groq TTS (canopylabs/orpheus-v1-english)

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pydantic anthropic langfuse fastapi uvicorn python-dotenv
```

Create a `.env` file in the project root:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional — Langfuse tracing (traces render in the UI's LANGFUSE TRACE tab)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com

# Optional — Groq TTS for in-browser agent voices
GROQ_API_KEY=gsk_...
```

---

## Run a simulation

```bash
python run_simulation.py --seed 42 --max-turns 50
```

Options:

| Flag | Default | Purpose |
|---|---|---|
| `--seed N` | random | Reproducible grid + agent placement |
| `--grid-size N` | 8 | Dungeon dimensions |
| `--max-turns N` | 50 | Turn limit before timeout |
| `--runs N` | 1 | Batch multiple simulations |
| `--model MODEL` | `claude-opus-4-6` | Claude model ID for agents |

Each run writes one self-contained JSON file to `runs/<run_id>.json` containing the manifest, all turn events, belief states, divergences, the post-hoc diagnosis, causal chain, and recommendations.

---

## View the UI

```bash
python -m uvicorn server.app:app --port 8000
```

Open http://localhost:8000.

### Run list (`index.html`)
Dense table: run ID, outcome, turn count, divergences, seed. Click a run to open the detail page.

### Run detail (`run.html`)
The page is explicitly organized around the three spec questions:

**① WHAT HAPPENED** — a two-column replay:
- **Left:** canvas world with fog of war, path traces, agent sprites; transport controls (first / prev / play / next / last) sit directly under the world, followed by a clickable divergence timeline, a legend strip, and compact per-agent belief panels.
- **Right:** a WhatsApp-style chat frame. Every `send_message` call becomes a colored bubble (Agent A left/blue, Agent B right/red). Each bubble has a ▶ play button that streams **Groq TTS audio** for that line, and an → button to jump the replay to the turn the message was sent. Toggle **AUTO-PLAY** to have new messages speak as the replay steps forward, or hit **PLAY ALL TO HERE** to play every message up to the current turn in order.

**② WHY IT HAPPENED** — two panels side-by-side:
- **Diagnosis** — primary failure mode badge (stale_beliefs, stuck_loop, coordination_gap, exploration_failure, none), wasted-turn count, stale-decision rate, divergences per turn, coordination-gap count, and bullet-point key insights.
- **Causal chain** — ranked "staleness windows": each row shows an agent, a field (e.g. `key_location`), what they *believed* vs. what was *actually* true, and a proportional bar marking when the belief was last correct, when ground truth changed, and when (or if) the agent recovered. Click the jump button to scrub the replay to that window.

**③ WHAT TO CHANGE NEXT** — priority-sorted recommendation cards (critical → high → medium) across four categories (coordination, prompt, architecture, exploration). Each card carries the finding, the concrete change, the expected impact, and clickable evidence turns.

A second tab — **LANGFUSE TRACE** — proxies the run's live Langfuse trace through the FastAPI server (keys stay server-side), rendering scores and the full observation tree.

**Keyboard shortcuts:** ← / → step turns, `Space` toggles play, `Home` / `End` jump to first/last turn.

---

## Architecture

```
dungeon/
  schemas.py          — Pydantic models (TurnEvent, BeliefState, BeliefDivergence,
                        CausalChain, RunDiagnosis, Recommendation, ...)
  world.py            — Grid generation (BFS-validated connectivity), fog of war,
                        tool execution, ground-truth snapshots
  tools.py            — 8 tools in Anthropic API format (move, pick_up, use_item,
                        send_message, look, wait, ...)
  agent.py            — LLM agent: tool-calling loop, conversation history trimming
                        (tool-result-safe), staleness tracking per belief field
  game.py             — Turn-based two-agent loop, 1-turn-delay message queue,
                        end conditions (success / timeout / stuck)
  tracing.py          — Langfuse @observe wiring, Haiku-based belief extraction,
                        divergence computation & severity classification
  causal.py           — Post-hoc causal chain builder: stitches per-field staleness
                        windows across turns, ranks them by blast radius
  recommendations.py  — Turns the diagnosis + causal chain into priority-sorted,
                        evidence-backed recommendations

server/
  app.py              — FastAPI app (static mount + API router)
  routes.py           — Read-only API over run JSON files + Groq TTS proxy with
                        on-disk audio caching + Langfuse trace proxy

static/
  index.html          — Run list
  run.html            — Run detail (three q-sections: what / why / change)
  css/style.css       — Dark Pokemon-GBA cave theme
  js/
    api.js            — Fetch wrapper
    grid.js           — Canvas grid renderer (walls, fog, agents, path traces)
    timeline.js       — Clickable divergence timeline
    replay.js         — Replay controller + chat log + TTS playback
    langfuse_trace.js — Langfuse tab renderer

runs/                 — One JSON file per run (self-contained)
  tts_cache/          — Generated WAV files, keyed by sha256(voice::text)
```

---

## Key design decisions

**Belief extraction is a separate Haiku call.** The agent reasons naturally; after each turn a cheap Haiku pass extracts a structured `BeliefState` from the reasoning text. The agent's conversation is never contaminated by the observability layer — the same pattern you'd use to instrument real production agents.

**Staleness as a first-class field.** Every belief field carries `turns_since_last_observed`. An agent acting on 8-turn-old `key_location` data is exactly the class of bug the UI is designed to surface, and it's also what the causal-chain builder uses to rank failures.

**Divergence categories.** Each mismatch is classified as `stale_information`, `incorrect_inference`, `missed_observation`, `communication_gap`, or `never_observed`. This enables cross-run filtering ("show me all failures caused by stale info vs. bad inference") and drives the diagnosis-engine's failure-mode classifier.

**Causal chain over flat divergence list.** A raw list of per-turn divergences is noisy. The causal builder stitches consecutive divergences on the same field into a **staleness window** — `(agent, field, last_correct_turn, ground_truth_changed_turn, stale_end_turn)` — and ranks windows by duration. This is what makes "why" answerable in one glance.

**Recommendations are evidence-backed.** Every recommendation card links to specific turn numbers in the trace. No generic advice — if it says "agents failed to announce pickups", it points you at the exact turns where it happened.

**Conversation-history trimming is tool-result-safe.** Anthropic rejects histories that start with an orphan `tool_result`. The agent's `_trim_history` method advances the trim boundary forward until it lands on a fresh user-prompt turn, and parallel tool use is disabled so we never get multiple `tool_use` blocks in one assistant message.

**TTS via server-side Groq proxy.** The Groq API key never reaches the browser. Generated audio is cached on disk keyed by `sha256(voice::text)` — identical lines on replay are instant and free. Each agent has a distinct voice (austin / hannah) so the listener can tell who's speaking without looking.

**Single JSON file per run.** No database. One run = one portable, diffable, submittable file containing everything: manifest, events, beliefs, divergences, diagnosis, causal chain, recommendations, and the Langfuse trace ID.

---

## Event schema

Each turn produces one `TurnEvent` per agent, containing:

- **Observable state** — what the game engine showed the agent this turn
- **Pending messages** — messages received (1-turn delay from the sender)
- **Action** — `tool_name`, `tool_input`, `tool_output`, `tool_success`, `tool_failure_reason`
- **Reasoning** — raw LLM reasoning text, latency, prompt/completion tokens
- **Decision quality** — `expected_tool_outcome`, `outcome_matched_expectation`, `decision_info_age`
- **Beliefs** — extracted `BeliefState` with per-field `information_staleness`
- **Ground truth** — full `actual_world_state` snapshot
- **Divergences** — list of `BeliefDivergence` records (field, believed, actual, staleness, severity, category)
- **Message sent** — outbound message metadata (sent turn → delivered turn)

The `RunManifest` aggregates: outcome, total turns, tool-call counts, peak belief staleness, key-found turn, door-unlocked turn, coordination-failure count, Langfuse trace ID.

---

## Verifying it works end-to-end

1. `python run_simulation.py --seed 42 --runs 3` → three JSON files in `runs/`
2. Each file contains complete `TurnEvent`s with belief states, divergences, diagnosis, causal chain, and recommendations
3. (If Langfuse configured) the Langfuse dashboard shows traces with full LLM I/O and tool calls
4. `python -m uvicorn server.app:app --port 8000` serves the UI at http://localhost:8000
5. Run list loads; clicking a run shows the three-section detail page
6. Replay steps through turns with working transport + keyboard shortcuts
7. Chat bubbles appear on the right, highlighted in sync with the current turn
8. (If Groq configured) clicking ▶ on a bubble streams TTS audio
9. Diagnosis + causal chain + recommendations render below with working jump-to-turn buttons
10. LANGFUSE TRACE tab loads the live trace through the server-side proxy
