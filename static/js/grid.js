/** Canvas-based grid renderer for the dungeon. */

const GridRenderer = {
    canvas: null,
    ctx: null,
    gridSize: 8,
    cellSize: 48,
    padding: 1,

    // Colors
    colors: {
        bg: '#0a0a0a',
        floor: '#1a1a1a',
        wall: '#3a3a3a',
        doorLocked: '#8b4513',
        doorUnlocked: '#2d6b2d',
        exit: '#22c55e',
        gridLine: '#1f1f1f',
        key: '#eab308',
        item: '#6b7280',
        agentA: '#3b82f6',
        agentB: '#a855f7',
        fog: 'rgba(0, 0, 0, 0.65)',
        pathA: 'rgba(59, 130, 246, 0.2)',
        pathB: 'rgba(168, 85, 247, 0.2)',
    },

    init(canvasId, gridSize) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.gridSize = gridSize || 8;
        this.cellSize = Math.floor(this.canvas.width / this.gridSize);
    },

    /**
     * Render the grid for a given turn.
     * @param {object} worldState - actual_world_state from the event
     * @param {object} config - world_config from the manifest
     * @param {Set} visibleCells - cells visible to any agent this turn
     * @param {object} pathHistory - { agent_a: [[r,c],...], agent_b: [[r,c],...] }
     */
    render(worldState, config, visibleCells, pathHistory) {
        const ctx = this.ctx;
        const cs = this.cellSize;
        const gs = this.gridSize;

        // Clear
        ctx.fillStyle = this.colors.bg;
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        const grid = worldState.grid;

        // Draw cells
        for (let r = 0; r < gs; r++) {
            for (let c = 0; c < gs; c++) {
                const x = c * cs;
                const y = r * cs;
                const cellType = grid[r][c];

                // Base cell color
                if (cellType === 'wall') {
                    ctx.fillStyle = this.colors.wall;
                } else if (cellType === 'door') {
                    ctx.fillStyle = worldState.door_locked
                        ? this.colors.doorLocked
                        : this.colors.doorUnlocked;
                } else if (cellType === 'exit') {
                    ctx.fillStyle = this.colors.exit;
                } else {
                    ctx.fillStyle = this.colors.floor;
                }
                ctx.fillRect(x + this.padding, y + this.padding,
                    cs - this.padding * 2, cs - this.padding * 2);
            }
        }

        // Draw path history (faint colored cells where agents have been)
        if (pathHistory) {
            for (const [agentId, path] of Object.entries(pathHistory)) {
                ctx.fillStyle = agentId === 'agent_a'
                    ? this.colors.pathA : this.colors.pathB;
                for (const [r, c] of path) {
                    ctx.fillRect(c * cs + this.padding, r * cs + this.padding,
                        cs - this.padding * 2, cs - this.padding * 2);
                }
            }
        }

        // Draw items
        for (const [itemName, pos] of Object.entries(worldState.items || {})) {
            const [r, c] = pos;
            const cx = c * cs + cs / 2;
            const cy = r * cs + cs / 2;

            if (itemName === 'key') {
                // Key: yellow diamond
                ctx.fillStyle = this.colors.key;
                ctx.save();
                ctx.translate(cx, cy);
                ctx.rotate(Math.PI / 4);
                ctx.fillRect(-6, -6, 12, 12);
                ctx.restore();
            } else {
                // Other items: gray dot
                ctx.fillStyle = this.colors.item;
                ctx.beginPath();
                ctx.arc(cx, cy, 4, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        // Draw agents
        for (const [agentId, pos] of Object.entries(worldState.agent_positions || {})) {
            const [r, c] = pos;
            const cx = c * cs + cs / 2;
            const cy = r * cs + cs / 2;
            const color = agentId === 'agent_a'
                ? this.colors.agentA : this.colors.agentB;

            // Circle
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(cx, cy, cs / 3, 0, Math.PI * 2);
            ctx.fill();

            // Label
            ctx.fillStyle = '#fff';
            ctx.font = `bold ${Math.floor(cs / 3)}px monospace`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(agentId === 'agent_a' ? 'A' : 'B', cx, cy);
        }

        // Fog of war overlay
        if (visibleCells) {
            for (let r = 0; r < gs; r++) {
                for (let c = 0; c < gs; c++) {
                    if (!visibleCells.has(`${r},${c}`)) {
                        ctx.fillStyle = this.colors.fog;
                        ctx.fillRect(c * cs, r * cs, cs, cs);
                    }
                }
            }
        }
    },

    /**
     * Compute which cells are visible to agents this turn.
     * Visible = agent's cell + 4 adjacent cells for each agent.
     */
    computeVisible(agentPositions) {
        const visible = new Set();
        const dirs = [[0, 0], [-1, 0], [1, 0], [0, -1], [0, 1]];
        for (const pos of Object.values(agentPositions)) {
            const [r, c] = pos;
            for (const [dr, dc] of dirs) {
                const nr = r + dr;
                const nc = c + dc;
                if (nr >= 0 && nr < this.gridSize && nc >= 0 && nc < this.gridSize) {
                    visible.add(`${nr},${nc}`);
                }
            }
        }
        return visible;
    },
};
