/** Langfuse trace tab: fetches live trace data via our server proxy and renders it. */

const LangfuseTrace = {
    runId: null,
    traceUrl: null,   // direct Langfuse dashboard URL
    pollTimer: null,
    isLive: false,    // true if run has no ended_at (still running)

    init(runId, manifest) {
        this.runId = runId;
        this.traceUrl = manifest.langfuse_trace_url || null;
        this.isLive = !manifest.ended_at;

        if (this.traceUrl) {
            const link = document.getElementById('lf-external-link');
            link.href = this.traceUrl;
            link.style.display = '';
        }

        document.getElementById('lf-refresh').onclick = () => this.fetch();
    },

    async activate() {
        // Called when user clicks the LANGFUSE TRACE tab
        await this.fetch();
        if (this.isLive) {
            this.pollTimer = setInterval(() => this.fetch(), 5000);
        }
    },

    deactivate() {
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
        }
    },

    async fetch() {
        this._setStatus('loading…', false);
        try {
            const resp = await fetch(`/api/runs/${this.runId}/langfuse`);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ detail: resp.statusText }));
                this._setStatus(`Error: ${err.detail}`, true);
                return;
            }
            const trace = await resp.json();
            this._render(trace);
            const now = new Date().toLocaleTimeString();
            this._setStatus(this.isLive ? `Live · refreshed ${now}` : `Fetched ${now}`, false);
        } catch (e) {
            this._setStatus(`Network error: ${e.message}`, true);
        }
    },

    _render(trace) {
        this._renderScores(trace.scores || []);
        this._renderTree(trace.observations || []);
    },

    _renderScores(scores) {
        const el = document.getElementById('lf-scores');
        if (!scores.length) { el.innerHTML = ''; return; }

        // Show scores as stat chips — same pattern as the diagnosis panel
        const scoreOrder = ['success', 'divergence_count', 'belief_accuracy_rate', 'peak_staleness', 'messages_sent'];
        const byName = Object.fromEntries(scores.map(s => [s.name, s]));
        const ordered = [
            ...scoreOrder.filter(n => byName[n]).map(n => byName[n]),
            ...scores.filter(s => !scoreOrder.includes(s.name)),
        ];

        el.innerHTML = `
            <div class="lf-scores-label">SCORES</div>
            <div class="lf-scores-chips">
                ${ordered.map(s => {
                    let cls = '';
                    if (s.name === 'success') cls = s.value === 1 ? 'score-success' : 'score-failure';
                    const val = typeof s.value === 'number'
                        ? (s.value % 1 === 0 ? s.value : s.value.toFixed(3))
                        : s.value;
                    return `<div class="lf-score-chip ${cls}">
                        <span class="score-val">${val}</span>
                        <span class="score-name">${s.name.replace(/_/g, ' ')}</span>
                        ${s.comment ? `<span class="score-comment" title="${this._esc(s.comment)}">ℹ</span>` : ''}
                    </div>`;
                }).join('')}
            </div>`;
    },

    _renderTree(observations) {
        const el = document.getElementById('lf-tree');
        if (!observations.length) {
            el.innerHTML = '<p class="lf-empty">No observations in this trace yet.</p>';
            return;
        }

        // Sort by startTime
        observations.sort((a, b) => new Date(a.startTime) - new Date(b.startTime));

        // Build parent→children map
        const children = {};
        const roots = [];
        for (const obs of observations) {
            const pid = obs.parentObservationId;
            if (!pid) { roots.push(obs); continue; }
            if (!children[pid]) children[pid] = [];
            children[pid].push(obs);
        }

        el.innerHTML = `
            <div class="lf-tree-label">TRACE TREE <span class="lf-tree-count">${observations.length} observations</span></div>
            <div class="lf-tree-body">
                ${roots.map(r => this._renderNode(r, children, 0)).join('')}
            </div>`;
    },

    _renderNode(obs, children, depth) {
        const kids = children[obs.id] || [];
        const hasKids = kids.length > 0;
        const isGen = obs.type === 'GENERATION';
        const isTool = obs.name?.startsWith('tool:');
        const isWarn = obs.level === 'WARNING';

        const durationMs = obs.endTime
            ? Math.round(new Date(obs.endTime) - new Date(obs.startTime))
            : null;

        const tokens = isGen && obs.usage
            ? `${(obs.usage.input || 0) + (obs.usage.output || 0)}tok`
            : null;

        const typeLabel = isGen ? 'GEN' : 'SPAN';
        const typeCls   = isGen ? 'obs-gen' : (isTool ? 'obs-tool' : 'obs-span');
        const warnCls   = isWarn ? 'obs-warn' : '';
        const meta = obs.metadata || {};
        const turn  = meta.turn  ?? obs.input?.turn  ?? null;
        const agent = meta.agent ?? obs.input?.agent ?? null;

        const detailId = `obs-${obs.id.replace(/[^a-z0-9]/gi, '')}`;

        // Build the summary line
        let summary = `<span class="obs-name">${this._esc(obs.name || '(unnamed)')}</span>`;
        summary += ` <span class="obs-type ${typeCls}">${typeLabel}</span>`;
        if (isWarn) summary += ` <span class="obs-warn-badge">WARN</span>`;
        if (turn  != null) summary += ` <span class="obs-meta">turn ${turn}</span>`;
        if (agent != null) summary += ` <span class="obs-meta ${agent === 'agent_a' ? 'agent-a-color' : 'agent-b-color'}">${agent}</span>`;
        if (durationMs != null) summary += ` <span class="obs-meta">${durationMs}ms</span>`;
        if (tokens) summary += ` <span class="obs-meta">${tokens}</span>`;
        if (obs.model) summary += ` <span class="obs-meta obs-model">${this._esc(obs.model)}</span>`;

        // Detail block: input/output for generations and tools
        const hasDetail = obs.input || obs.output;
        const detailHtml = hasDetail ? `
            <div class="obs-detail" id="${detailId}" style="display:none">
                ${obs.input  ? `<div class="obs-io-label">INPUT</div><pre class="obs-io">${this._esc(this._fmt(obs.input))}</pre>` : ''}
                ${obs.output ? `<div class="obs-io-label">OUTPUT</div><pre class="obs-io">${this._esc(this._fmt(obs.output))}</pre>` : ''}
                ${obs.statusMessage ? `<div class="obs-io-label">STATUS</div><pre class="obs-io obs-io-warn">${this._esc(obs.statusMessage)}</pre>` : ''}
            </div>` : '';

        const toggleAttr = hasDetail ? `onclick="LangfuseTrace._toggleDetail('${detailId}')"` : '';
        const clickCls   = hasDetail ? 'obs-clickable' : '';

        return `
        <div class="obs-node" style="margin-left:${depth * 16}px">
            <div class="obs-row ${warnCls} ${clickCls}" ${toggleAttr}>
                ${hasKids ? `<span class="obs-arrow" onclick="LangfuseTrace._toggleKids(this, '${detailId}-kids'); event.stopPropagation()">&#9660;</span>` : '<span class="obs-arrow-spacer"></span>'}
                ${summary}
            </div>
            ${detailHtml}
            <div id="${detailId}-kids">
                ${kids.map(k => this._renderNode(k, children, 0)).join('')}
            </div>
        </div>`;
    },

    _toggleDetail(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
    },

    _toggleKids(arrow, kidsId) {
        const el = document.getElementById(kidsId);
        if (!el) return;
        const collapsed = el.style.display === 'none';
        el.style.display = collapsed ? '' : 'none';
        arrow.innerHTML = collapsed ? '&#9660;' : '&#9658;';
    },

    _fmt(val) {
        if (val === null || val === undefined) return '';
        if (typeof val === 'string') return val;
        return JSON.stringify(val, null, 2);
    },

    _esc(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    _setStatus(msg, isError) {
        const el = document.getElementById('lf-status');
        el.textContent = msg;
        el.className = `lf-status ${isError ? 'lf-status-error' : ''}`;
    },
};

// ── Tab switching ──────────────────────────────────────────────────────────────

const REPLAY_SECTIONS = () => [
    document.querySelector('.run-detail'),
    document.getElementById('diagnosis-panel'),
    document.getElementById('causal-panel'),
    document.querySelector('.timeline-section'),
];

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        const langfusePanel = document.getElementById('tab-langfuse-panel');

        if (tab === 'replay') {
            REPLAY_SECTIONS().forEach(el => el && (el.style.display = ''));
            langfusePanel.style.display = 'none';
            LangfuseTrace.deactivate();
        } else {
            REPLAY_SECTIONS().forEach(el => el && (el.style.display = 'none'));
            langfusePanel.style.display = 'block';
            LangfuseTrace.activate();
        }
    });
});
