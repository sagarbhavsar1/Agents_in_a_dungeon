/** Fetch wrapper for the dungeon agents API. */
const api = {
    async listRuns() {
        const res = await fetch('/api/runs');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    },

    async getRun(runId) {
        const res = await fetch(`/api/runs/${runId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    },

    async getEvents(runId, { agent, turn } = {}) {
        const params = new URLSearchParams();
        if (agent) params.set('agent', agent);
        if (turn != null) params.set('turn', turn);
        const qs = params.toString();
        const res = await fetch(`/api/runs/${runId}/events${qs ? '?' + qs : ''}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    },

    async getDivergences(runId) {
        const res = await fetch(`/api/runs/${runId}/divergences`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    },

    async getTimeline(runId) {
        const res = await fetch(`/api/runs/${runId}/timeline`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    },
};
