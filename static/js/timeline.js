/** Timeline renderer: horizontal bar with divergence markers and milestones. */

const TimelineRenderer = {
    canvas: null,
    ctx: null,
    totalTurns: 0,
    currentTurn: 1,
    events: [],
    onTurnClick: null, // callback

    colors: {
        bg: '#2a1e0e',
        track: '#3a2a14',
        trackActive: '#6b4c24',
        cursor: '#f8e8c0',
        divCritical: '#e83028',
        divHigh: '#f0b830',
        divMedium: '#8898c0',
        divLow: '#5a3c1c',
        milestone: '#48c870',
        messageMarker: '#3888f8',
        text: '#a08858',
        textBright: '#f8e8c0',
    },

    init(canvasId, events, totalTurns) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.events = events;
        this.totalTurns = totalTurns;

        // Make canvas full width
        this.canvas.width = this.canvas.parentElement.clientWidth - 32;

        // Click handler
        this.canvas.addEventListener('click', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const turn = this._xToTurn(x);
            if (turn >= 1 && turn <= this.totalTurns && this.onTurnClick) {
                this.onTurnClick(turn);
            }
        });
    },

    setTurn(turn) {
        this.currentTurn = turn;
        this.render();
    },

    render() {
        const ctx = this.ctx;
        const w = this.canvas.width;
        const h = this.canvas.height;
        const marginLeft = 40;
        const marginRight = 20;
        const trackY = 35;
        const trackH = 6;
        const trackW = w - marginLeft - marginRight;

        // Clear
        ctx.fillStyle = this.colors.bg;
        ctx.fillRect(0, 0, w, h);

        // Track background
        ctx.fillStyle = this.colors.track;
        ctx.fillRect(marginLeft, trackY, trackW, trackH);

        // Progress fill
        if (this.totalTurns > 0) {
            const progress = (this.currentTurn / this.totalTurns) * trackW;
            ctx.fillStyle = this.colors.trackActive;
            ctx.fillRect(marginLeft, trackY, progress, trackH);
        }

        // Group events by turn for markers
        const turnData = {};
        for (const evt of this.events) {
            const t = evt.turn_number;
            if (!turnData[t]) {
                turnData[t] = { divCount: 0, maxSeverity: 'low', milestones: [], hasMessage: false };
            }
            turnData[t].divCount += evt.divergence_count || 0;
            // Track max severity
            for (const sev of (evt.divergence_severities || [])) {
                const order = { critical: 3, high: 2, medium: 1, low: 0 };
                if ((order[sev] || 0) > (order[turnData[t].maxSeverity] || 0)) {
                    turnData[t].maxSeverity = sev;
                }
            }
            if (evt.milestone) turnData[t].milestones.push(evt.milestone);
            if (evt.message_sent) turnData[t].hasMessage = true;
        }

        // Draw divergence markers (triangles above track)
        for (const [turnStr, data] of Object.entries(turnData)) {
            const turn = parseInt(turnStr);
            const x = this._turnToX(turn);

            if (data.divCount > 0) {
                const sevColor = {
                    critical: this.colors.divCritical,
                    high: this.colors.divHigh,
                    medium: this.colors.divMedium,
                    low: this.colors.divLow,
                }[data.maxSeverity] || this.colors.divLow;

                const markerH = Math.min(4 + data.divCount * 3, 20);
                ctx.fillStyle = sevColor;
                ctx.beginPath();
                ctx.moveTo(x - 3, trackY - 2);
                ctx.lineTo(x + 3, trackY - 2);
                ctx.lineTo(x, trackY - 2 - markerH);
                ctx.closePath();
                ctx.fill();
            }

            // Milestone labels below track
            if (data.milestones.length > 0) {
                ctx.fillStyle = this.colors.milestone;
                ctx.font = '9px monospace';
                ctx.textAlign = 'center';
                const label = data.milestones.map(m =>
                    m === 'key_found' ? 'KEY' : m === 'door_unlocked' ? 'DOOR' : m
                ).join(' ');
                ctx.fillText(label, x, trackY + trackH + 14);

                // Small dot on track
                ctx.beginPath();
                ctx.arc(x, trackY + trackH / 2, 3, 0, Math.PI * 2);
                ctx.fill();
            }

            // Message indicator (small blue dot below track)
            if (data.hasMessage && data.milestones.length === 0) {
                ctx.fillStyle = this.colors.messageMarker;
                ctx.beginPath();
                ctx.arc(x, trackY + trackH + 8, 2, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        // Current turn cursor (vertical line)
        const cursorX = this._turnToX(this.currentTurn);
        ctx.strokeStyle = this.colors.cursor;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cursorX, trackY - 22);
        ctx.lineTo(cursorX, trackY + trackH + 22);
        ctx.stroke();

        // Turn labels at edges
        ctx.fillStyle = this.colors.text;
        ctx.font = '10px monospace';
        ctx.textAlign = 'left';
        ctx.fillText('T1', marginLeft, h - 4);
        ctx.textAlign = 'right';
        ctx.fillText(`T${this.totalTurns}`, marginLeft + trackW, h - 4);
    },

    _turnToX(turn) {
        const marginLeft = 40;
        const marginRight = 20;
        const trackW = this.canvas.width - marginLeft - marginRight;
        return marginLeft + ((turn - 0.5) / this.totalTurns) * trackW;
    },

    _xToTurn(x) {
        const marginLeft = 40;
        const marginRight = 20;
        const trackW = this.canvas.width - marginLeft - marginRight;
        const ratio = (x - marginLeft) / trackW;
        return Math.max(1, Math.min(this.totalTurns, Math.round(ratio * this.totalTurns + 0.5)));
    },
};
