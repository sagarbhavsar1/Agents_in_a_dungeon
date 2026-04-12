/** Turn-by-turn replay controller. Ties grid, agent panels, and timeline together. */

const Replay = {
    runData: null,
    manifest: null,
    events: [],
    eventsByTurn: {},   // turn -> [event_a, event_b]
    totalTurns: 0,
    currentTurn: 1,
    pathHistory: { agent_a: [], agent_b: [] },
    playInterval: null,

    async init() {
        const params = new URLSearchParams(window.location.search);
        const runId = params.get('id');
        if (!runId) {
            document.getElementById('run-title').textContent = 'No run ID specified';
            return;
        }

        // Load data
        this.runData = await api.getRun(runId);
        this.manifest = this.runData.manifest;
        this.events = this.runData.events || [];

        // Group events by turn
        for (const evt of this.events) {
            if (!this.eventsByTurn[evt.turn_number]) {
                this.eventsByTurn[evt.turn_number] = [];
            }
            this.eventsByTurn[evt.turn_number].push(evt);
        }

        this.totalTurns = this.manifest.total_turns;

        // Set header
        const outcomeClass = this.manifest.outcome === 'success' ? 'outcome-success' : 'outcome-failure';
        document.getElementById('run-title').textContent = `Run ${this.manifest.run_id}`;
        const s = this.manifest.summary_stats;
        document.getElementById('run-meta').innerHTML =
            `<span class="${outcomeClass}">${this.manifest.outcome}</span>` +
            ` &middot; ${this.totalTurns} turns` +
            ` &middot; ${s.belief_divergence_count} divergences` +
            ` &middot; seed ${this.manifest.seed}`;

        // Init grid
        const gridSize = this.manifest.grid_size[0];
        GridRenderer.init('grid-canvas', gridSize);

        // Init timeline
        const timelineEvents = await api.getTimeline(runId);
        TimelineRenderer.init('timeline-canvas', timelineEvents, this.totalTurns);
        TimelineRenderer.onTurnClick = (turn) => this.goToTurn(turn);

        // Transport buttons
        document.getElementById('btn-first').onclick = () => this.goToTurn(1);
        document.getElementById('btn-prev').onclick = () => this.prevTurn();
        document.getElementById('btn-next').onclick = () => this.nextTurn();
        document.getElementById('btn-last').onclick = () => this.goToTurn(this.totalTurns);
        document.getElementById('btn-play').onclick = () => this.togglePlay();

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') this.prevTurn();
            else if (e.key === 'ArrowRight') this.nextTurn();
            else if (e.key === ' ') { e.preventDefault(); this.togglePlay(); }
            else if (e.key === 'Home') this.goToTurn(1);
            else if (e.key === 'End') this.goToTurn(this.totalTurns);
        });

        // Initial render
        this.goToTurn(1);
    },

    goToTurn(turn) {
        if (turn < 1 || turn > this.totalTurns) return;
        this.currentTurn = turn;

        // Build path history up to this turn
        this.pathHistory = { agent_a: [], agent_b: [] };
        for (let t = 1; t <= turn; t++) {
            const turnEvents = this.eventsByTurn[t] || [];
            for (const evt of turnEvents) {
                const pos = evt.actual_world_state?.agent_positions?.[evt.agent_id];
                if (pos) {
                    this.pathHistory[evt.agent_id].push(pos);
                }
            }
        }

        // Get events for this turn
        const turnEvents = this.eventsByTurn[turn] || [];

        // Find the world state from the last event of this turn
        const lastEvent = turnEvents[turnEvents.length - 1];
        if (lastEvent) {
            const ws = lastEvent.actual_world_state;
            const visible = GridRenderer.computeVisible(ws.agent_positions);
            GridRenderer.render(ws, this.manifest.world_config, visible, this.pathHistory);
        }

        // Update agent panels
        this.renderAgentPanel('agent_a', turnEvents.find(e => e.agent_id === 'agent_a'));
        this.renderAgentPanel('agent_b', turnEvents.find(e => e.agent_id === 'agent_b'));

        // Update timeline
        TimelineRenderer.setTurn(turn);

        // Update turn display
        document.getElementById('turn-display').textContent = `Turn ${turn} / ${this.totalTurns}`;
    },

    renderAgentPanel(agentId, event) {
        const panelId = agentId === 'agent_a' ? 'agent-a-panel' : 'agent-b-panel';
        const body = document.querySelector(`#${panelId} .agent-card-body`);

        if (!event) {
            body.innerHTML = '<p class="text-muted">No action this turn</p>';
            return;
        }

        let html = '';

        // Position and inventory
        const pos = event.observable_state.position;
        html += `<div class="agent-info">`;
        html += `<span class="label">Position:</span> <span class="mono">(${pos[0]}, ${pos[1]})</span>`;
        html += ` &middot; `;
        html += `<span class="label">Inventory:</span> <span class="mono">${event.observable_state.inventory.length ? event.observable_state.inventory.join(', ') : '(empty)'}</span>`;
        html += `</div>`;

        // Action
        const successClass = event.tool_success ? 'action-success' : 'action-failure';
        html += `<div class="agent-action ${successClass}">`;
        html += `<span class="label">Action:</span> <span class="mono">${event.tool_name}(${this.formatArgs(event.tool_input)})</span>`;
        if (!event.tool_success && event.tool_failure_reason) {
            html += ` <span class="failure-reason">${event.tool_failure_reason}</span>`;
        }
        html += `</div>`;

        // Messages received
        if (event.pending_messages && event.pending_messages.length > 0) {
            html += `<div class="agent-messages">`;
            html += `<span class="label">Received:</span>`;
            for (const msg of event.pending_messages) {
                html += ` <span class="message-bubble">"${this.escapeHtml(msg.content)}"</span>`;
            }
            html += `</div>`;
        }

        // Message sent
        if (event.message_sent) {
            html += `<div class="agent-messages">`;
            html += `<span class="label">Sent:</span> <span class="message-bubble sent">"${this.escapeHtml(event.message_sent.content)}"</span>`;
            html += `</div>`;
        }

        // Reasoning (collapsible)
        if (event.llm_reasoning) {
            const short = event.llm_reasoning.length > 200
                ? event.llm_reasoning.substring(0, 200) + '...'
                : event.llm_reasoning;
            html += `<details class="reasoning">`;
            html += `<summary>Reasoning <span class="text-muted">(${event.llm_latency_ms}ms, ${event.prompt_tokens + event.completion_tokens} tokens)</span></summary>`;
            html += `<pre>${this.escapeHtml(event.llm_reasoning)}</pre>`;
            html += `</details>`;
        }

        // Beliefs vs Reality — the core diagnostic feature
        html += this.renderBeliefTable(event);

        body.innerHTML = html;
    },

    renderBeliefTable(event) {
        const belief = event.belief_state;
        const actual = event.actual_world_state;
        const divs = event.divergences || [];

        // Build divergence lookup
        const divByField = {};
        for (const d of divs) {
            divByField[d.field] = d;
        }

        let html = `<div class="belief-table">`;
        html += `<div class="belief-header">BELIEFS vs REALITY</div>`;
        html += `<table>`;

        const fields = [
            { key: 'my_position', label: 'Position', believed: belief.my_position ? `(${belief.my_position})` : '?', actual: `(${actual.agent_positions[event.agent_id]})` },
            { key: 'other_agent_position', label: 'Other agent', believed: belief.other_agent_position ? `(${belief.other_agent_position})` : '?',
              actual: (() => { const other = event.agent_id === 'agent_a' ? 'agent_b' : 'agent_a'; return `(${actual.agent_positions[other]})`; })() },
            { key: 'key_location', label: 'Key', believed: belief.key_location || '?', actual: this.describeKey(actual) },
            { key: 'door_status', label: 'Door', believed: belief.door_status || '?', actual: actual.door_locked ? `locked at (${actual.door_position})` : `unlocked at (${actual.door_position})` },
            { key: 'exit_location', label: 'Exit', believed: belief.exit_location ? `(${belief.exit_location})` : '?', actual: `(${actual.exit_position})` },
        ];

        for (const f of fields) {
            const div = divByField[f.key];
            let icon, rowClass;
            if (div) {
                icon = '&#10007;'; // ✗
                rowClass = `div-${div.severity}`;
            } else if (f.believed === '?' || f.believed === 'unknown') {
                icon = '?';
                rowClass = 'div-unknown';
            } else {
                icon = '&#10003;'; // ✓
                rowClass = 'div-correct';
            }

            html += `<tr class="${rowClass}">`;
            html += `<td class="belief-icon">${icon}</td>`;
            html += `<td class="belief-label">${f.label}</td>`;
            html += `<td class="belief-value mono">${this.escapeHtml(f.believed)}</td>`;

            if (div) {
                html += `<td class="belief-actual">actual: <span class="mono">${this.escapeHtml(f.actual)}</span></td>`;
                html += `<td class="belief-stale">${div.staleness_turns > 0 ? div.staleness_turns + 't stale' : div.category.replace('_', ' ')}</td>`;
            } else {
                html += `<td></td><td></td>`;
            }
            html += `</tr>`;
        }

        html += `</table>`;

        // Goal
        if (belief.current_goal && belief.current_goal !== 'unknown') {
            html += `<div class="belief-goal"><span class="label">Goal:</span> ${this.escapeHtml(belief.current_goal)}</div>`;
        }

        html += `</div>`;
        return html;
    },

    describeKey(actual) {
        if (actual.key_holder) return `${actual.key_holder} has it`;
        if (actual.key_position) return `at (${actual.key_position})`;
        return 'used';
    },

    formatArgs(input) {
        if (!input || Object.keys(input).length === 0) return '';
        return Object.entries(input).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ');
    },

    escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    prevTurn() {
        if (this.currentTurn > 1) this.goToTurn(this.currentTurn - 1);
    },

    nextTurn() {
        if (this.currentTurn < this.totalTurns) this.goToTurn(this.currentTurn + 1);
    },

    togglePlay() {
        const btn = document.getElementById('btn-play');
        if (this.playInterval) {
            clearInterval(this.playInterval);
            this.playInterval = null;
            btn.innerHTML = '&#9654;'; // play
        } else {
            btn.innerHTML = '&#9646;&#9646;'; // pause
            this.playInterval = setInterval(() => {
                if (this.currentTurn >= this.totalTurns) {
                    this.togglePlay(); // stop at end
                    return;
                }
                this.nextTurn();
            }, 1000);
        }
    },
};

// Boot
Replay.init();
