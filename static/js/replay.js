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

    // Chat / TTS state
    messages: [],            // all send_message events sorted by sent_turn
    _audioCache: {},         // msg_id -> object URL (browser-side cache)
    _currentAudio: null,     // currently playing HTMLAudioElement
    _ttsAbort: null,         // abort controller for chained "play to here"
    _lastPlayedTurn: 0,      // highest turn whose messages we've auto-played

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

        // Render diagnosis panel
        this.renderDiagnosis(this.runData.diagnosis);

        // Render causal chain
        this.renderCausalChain(this.runData.causal_chain);

        // Collect and render chat log (with TTS playback)
        this.messages = this.collectMessages();
        this.renderChatLog();

        // Render recommendations
        this.renderRecommendations(this.runData.recommendations);

        // Init Langfuse trace tab (loads lazily when tab is clicked)
        LangfuseTrace.init(runId, this.manifest);

        // Initial render
        this.goToTurn(1);
    },

    renderDiagnosis(diagnosis) {
        if (!diagnosis) return;
        const panel = document.getElementById('diagnosis-panel');
        panel.style.display = 'block';

        const badge = document.getElementById('diagnosis-mode');
        badge.textContent = diagnosis.primary_failure_mode.replace(/_/g, ' ');
        badge.className = `diagnosis-mode-badge mode-${diagnosis.primary_failure_mode}`;

        const statsEl = document.getElementById('diagnosis-stats');
        statsEl.innerHTML = [
            { value: diagnosis.wasted_turns, label: 'wasted turns' },
            { value: (diagnosis.stale_decision_rate * 100).toFixed(0) + '%', label: 'stale rate' },
            { value: diagnosis.avg_divergences_per_turn.toFixed(1), label: 'divs/turn' },
            { value: diagnosis.coordination_gap_turns, label: 'coord gaps' },
        ].map(s =>
            `<div class="diag-stat"><span class="diag-value">${s.value}</span><span class="diag-key">${s.label}</span></div>`
        ).join('');

        const insightsEl = document.getElementById('diagnosis-insights');
        insightsEl.innerHTML = diagnosis.key_insights.length
            ? diagnosis.key_insights.map(i => `<li>${this.escapeHtml(i)}</li>`).join('')
            : '<li>No issues detected — clean run</li>';
    },

    renderCausalChain(chain) {
        if (!chain || !chain.windows || chain.windows.length === 0) return;
        document.getElementById('causal-panel').style.display = 'block';
        document.getElementById('causal-summary').textContent = chain.summary || '';

        const total = this.totalTurns;
        const container = document.getElementById('causal-windows');

        container.innerHTML = chain.windows.map((w, i) => {
            const isWorst = i === 0;
            const agentLabel = w.agent_id === 'agent_a' ? 'A' : 'B';
            const agentColor = w.agent_id === 'agent_a' ? 'agent-a-color' : 'agent-b-color';
            const fieldLabel = w.field.replace(/_/g, ' ');
            const fieldClass = `causal-field-${w.field}`;
            const statusClass = w.stale_end_turn ? 'cw-resolved' : 'cw-unresolved';
            const statusText = w.stale_end_turn ? 'resolved' : 'never resolved';

            // Bar segment percentages
            const pct = t => (t / total * 100).toFixed(1);
            const correctEnd = w.last_correct_turn || 0;
            const staleStart = w.stale_start_turn;
            const staleEnd = w.stale_end_turn || total;
            const gtMarker = w.ground_truth_changed_turn;

            const correctW = pct(correctEnd);
            const staleL   = pct(staleStart);
            const staleW   = pct(staleEnd - staleStart);
            const markerL  = pct(gtMarker);

            // Narrative: what the agent believed vs what was true
            const believed = this.escapeHtml(w.believed_value || '?');
            const actual   = this.escapeHtml(w.actual_value   || '?');

            return `
            <div class="cw ${isWorst ? 'cw-worst' : ''}">
                <div class="cw-head">
                    <span class="cw-agent ${agentColor}">AGENT ${agentLabel}</span>
                    <span class="cw-field ${fieldClass}">${fieldLabel}</span>
                    <span class="cw-duration">${w.duration_turns}t stale</span>
                    <span class="${statusClass}">${statusText}</span>
                    <button class="cw-jump" onclick="Replay.goToTurn(${staleStart})">&#8594; T${staleStart}</button>
                </div>
                <div class="cw-narrative">
                    believed <span class="cw-believed">${believed}</span>
                    &rarr; actually <span class="cw-actual">${actual}</span>
                </div>
                <div class="cw-bar-wrap">
                    <div class="cw-bar">
                        <div class="cw-seg-correct"  style="width:${correctW}%"></div>
                        <div class="cw-seg-stale"    style="left:${staleL}%; width:${staleW}%"></div>
                        <div class="cw-bar-marker"   style="left:${markerL}%" title="truth changed T${gtMarker}"></div>
                    </div>
                    <div class="cw-bar-labels">
                        <span style="left:0%">T1</span>
                        ${w.last_correct_turn ? `<span style="left:${correctW}%" class="cw-lbl-correct">T${w.last_correct_turn}</span>` : ''}
                        <span style="left:${markerL}%" class="cw-lbl-marker">T${gtMarker}</span>
                        ${w.stale_end_turn ? `<span style="left:${pct(staleEnd)}%" class="cw-lbl-resolved">T${w.stale_end_turn}</span>` : ''}
                        <span style="left:100%" class="cw-lbl-end">T${total}</span>
                    </div>
                </div>
            </div>`;
        }).join('');
    },

    renderRecommendations(recs) {
        if (!recs || recs.length === 0) return;
        const panel = document.getElementById('rec-panel');
        panel.style.display = 'block';

        const critCount = recs.filter(r => r.priority === 'critical').length;
        const highCount = recs.filter(r => r.priority === 'high').length;
        const parts = [];
        if (critCount) parts.push(`${critCount} critical`);
        if (highCount) parts.push(`${highCount} high`);
        const rest = recs.length - critCount - highCount;
        if (rest) parts.push(`${rest} medium`);
        document.getElementById('rec-summary').textContent = parts.join(' · ');

        const CATEGORY_LABELS = {
            coordination: 'COORDINATION',
            prompt: 'PROMPT',
            architecture: 'ARCHITECTURE',
            exploration: 'EXPLORATION',
        };

        document.getElementById('rec-list').innerHTML = recs.map((r) => {
            const catLabel = CATEGORY_LABELS[r.category] || r.category.toUpperCase();
            const jumpLinks = r.evidence_turns.length
                ? r.evidence_turns.map(t =>
                    `<button class="rec-jump" onclick="Replay.goToTurn(${t})">T${t}</button>`
                  ).join('')
                : '';

            return `
            <div class="rec-card rec-${r.priority}">
                <div class="rec-card-head">
                    <span class="rec-priority rec-priority-${r.priority}">${r.priority.toUpperCase()}</span>
                    <span class="rec-category">${catLabel}</span>
                    ${jumpLinks ? `<span class="rec-evidence">evidence: ${jumpLinks}</span>` : ''}
                </div>
                <div class="rec-finding">${this.escapeHtml(r.finding)}</div>
                <div class="rec-change">
                    <span class="rec-change-label">&#8594; Change:</span>
                    ${this.escapeHtml(r.recommendation)}
                </div>
                <div class="rec-impact">
                    <span class="rec-impact-label">Impact:</span>
                    ${this.escapeHtml(r.expected_impact)}
                </div>
            </div>`;
        }).join('');
    },

    _truncate(str, n) {
        if (!str) return '—';
        return str.length > n ? str.slice(0, n) + '…' : str;
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

        // Sync chat bubble highlighting to the current turn
        this.syncChatHighlight(turn);

        // Auto-play any new messages that appeared on this turn
        this.maybeAutoPlay(turn);
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
        // Decision quality badges
        if (event.decision_info_age > 2) {
            html += ` <span class="dq-badge dq-stale">${event.decision_info_age}t stale</span>`;
        }
        if (event.outcome_matched_expectation === false) {
            html += ` <span class="dq-badge dq-mismatch">unexpected ${event.expected_tool_outcome === 'success' ? 'fail' : 'pass'}</span>`;
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

    // ------------------------------------------------------------------
    // Chat log + TTS playback
    // ------------------------------------------------------------------

    collectMessages() {
        const msgs = [];
        for (const evt of this.events) {
            if (evt.message_sent) {
                msgs.push({
                    id: `m${msgs.length}`,
                    from: evt.message_sent.from_agent,
                    to: evt.message_sent.to_agent,
                    content: evt.message_sent.content,
                    sent_turn: evt.message_sent.sent_turn,
                    delivered_turn: evt.message_sent.delivered_turn,
                });
            }
        }
        msgs.sort((a, b) => a.sent_turn - b.sent_turn);
        return msgs;
    },

    renderChatLog() {
        const container = document.getElementById('chat-log');
        if (!container) return;

        if (this.messages.length === 0) {
            container.innerHTML = '<p class="chat-empty">— No messages sent this run —</p>';
            const cc = document.getElementById('chat-count');
            if (cc) cc.textContent = '0 messages';
            return;
        }

        const cc = document.getElementById('chat-count');
        if (cc) cc.textContent = `${this.messages.length} messages`;

        container.innerHTML = this.messages.map((m) => {
            const agentClass = m.from === 'agent_a' ? 'chat-agent-a' : 'chat-agent-b';
            const fromLabel = m.from === 'agent_a' ? 'AGENT A' : 'AGENT B';
            return `
            <div class="chat-msg ${agentClass}" data-msg-id="${m.id}" data-sent-turn="${m.sent_turn}">
                <div class="chat-bubble">${this.escapeHtml(m.content)}</div>
                <div class="chat-meta">
                    <button class="chat-play" data-msg-id="${m.id}" title="Play this message">&#9654;</button>
                    <span class="chat-sender">${fromLabel}</span>
                    <span class="chat-turn" title="Sent turn → delivered turn">T${m.sent_turn} &rarr; T${m.delivered_turn}</span>
                    <button class="chat-jump" data-turn="${m.sent_turn}" title="Jump replay to this turn">&#8594;</button>
                </div>
            </div>`;
        }).join('');

        // Wire up per-bubble buttons (avoids inline onclick escaping hazards)
        container.querySelectorAll('.chat-play').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.playMessage(btn.dataset.msgId);
            });
        });
        container.querySelectorAll('.chat-jump').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.goToTurn(parseInt(btn.dataset.turn, 10));
            });
        });

        // Toolbar buttons
        document.getElementById('chat-play-all').addEventListener('click', () => {
            this.playAllUpToCurrent();
        });
        document.getElementById('chat-stop').addEventListener('click', () => {
            this.stopPlayback();
        });
    },

    syncChatHighlight(turn) {
        const bubbles = document.querySelectorAll('.chat-msg');
        let activeEl = null;
        bubbles.forEach((el) => {
            const sent = parseInt(el.dataset.sentTurn, 10);
            el.classList.remove('chat-msg-active', 'chat-msg-past', 'chat-msg-future');
            if (sent === turn) {
                el.classList.add('chat-msg-active');
                activeEl = el;
            } else if (sent < turn) {
                el.classList.add('chat-msg-past');
            } else {
                el.classList.add('chat-msg-future');
            }
        });
        if (activeEl) {
            activeEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    },

    maybeAutoPlay(turn) {
        const autoplay = document.getElementById('chat-autoplay');
        if (!autoplay || !autoplay.checked) {
            // Reset marker so toggling it on later starts fresh from current turn
            this._lastPlayedTurn = turn;
            return;
        }
        // Only advance forward — don't replay when user scrubs backwards
        if (turn <= this._lastPlayedTurn) return;

        const newMessages = this.messages.filter(
            (m) => m.sent_turn > this._lastPlayedTurn && m.sent_turn <= turn,
        );
        this._lastPlayedTurn = turn;

        if (newMessages.length === 0) return;
        // Fire and forget — sequential playback
        this._playQueue(newMessages);
    },

    async playAllUpToCurrent() {
        const upTo = this.messages.filter((m) => m.sent_turn <= this.currentTurn);
        if (upTo.length === 0) return;
        await this._playQueue(upTo);
    },

    async _playQueue(msgs) {
        // Cancel any existing playback chain
        this.stopPlayback();
        const token = Symbol('playback');
        this._ttsAbort = token;

        for (const m of msgs) {
            if (this._ttsAbort !== token) return; // cancelled
            await this.playMessage(m.id, { jumpTo: true });
        }
        if (this._ttsAbort === token) this._ttsAbort = null;
    },

    stopPlayback() {
        this._ttsAbort = null;
        if (this._currentAudio) {
            this._currentAudio.pause();
            this._currentAudio.currentTime = 0;
            this._currentAudio = null;
        }
        // Reset any play buttons still showing "loading" / "playing"
        document.querySelectorAll('.chat-play').forEach((btn) => {
            btn.innerHTML = '&#9654;';
            btn.classList.remove('chat-play-loading', 'chat-play-active');
        });
    },

    playMessage(msgId, { jumpTo = false } = {}) {
        return new Promise(async (resolve) => {
            const msg = this.messages.find((m) => m.id === msgId);
            if (!msg) return resolve();

            // Stop any currently-playing audio (one voice at a time)
            if (this._currentAudio) {
                this._currentAudio.pause();
                this._currentAudio = null;
            }

            const bubble = document.querySelector(`[data-msg-id="${msgId}"]`);
            const btn = bubble ? bubble.querySelector('.chat-play') : null;
            const setBtn = (icon, cls) => {
                if (!btn) return;
                btn.innerHTML = icon;
                btn.classList.remove('chat-play-loading', 'chat-play-active', 'chat-play-error');
                if (cls) btn.classList.add(cls);
            };

            if (jumpTo) this.goToTurn(msg.sent_turn);

            // Check browser-side cache first — avoids a network round trip
            // on every replay of the same message.
            let url = this._audioCache[msgId];
            if (!url) {
                setBtn('&#8943;', 'chat-play-loading'); // ⋯
                try {
                    const resp = await fetch('/api/tts', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            text: msg.content,
                            agent_id: msg.from,
                        }),
                    });
                    if (!resp.ok) {
                        const errText = await resp.text();
                        console.error('TTS request failed:', resp.status, errText);
                        setBtn('&#10007;', 'chat-play-error'); // ✗
                        return resolve();
                    }
                    const blob = await resp.blob();
                    url = URL.createObjectURL(blob);
                    this._audioCache[msgId] = url;
                } catch (err) {
                    console.error('TTS network error:', err);
                    setBtn('&#10007;', 'chat-play-error');
                    return resolve();
                }
            }

            const audio = new Audio(url);
            this._currentAudio = audio;
            setBtn('&#9646;&#9646;', 'chat-play-active'); // ‖‖

            audio.onended = () => {
                setBtn('&#9654;');
                if (this._currentAudio === audio) this._currentAudio = null;
                resolve();
            };
            audio.onerror = () => {
                setBtn('&#10007;', 'chat-play-error');
                if (this._currentAudio === audio) this._currentAudio = null;
                resolve();
            };
            audio.play().catch((err) => {
                console.error('Audio play() rejected:', err);
                setBtn('&#10007;', 'chat-play-error');
                resolve();
            });
        });
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
