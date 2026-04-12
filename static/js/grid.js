/** Canvas-based cave grid renderer — Pokemon GBA cave style (RSE inspired). */

const GridRenderer = {
    canvas: null,
    ctx: null,
    gridSize: 8,
    cellSize: 50,

    // Cave color palette — warm brown earth tones
    colors: {
        bg:              '#140e06',   // deep cave earth
        floor:           '#4a3420',   // warm brown stone floor
        floorAlt:        '#422e1a',   // slightly darker variation
        wall:            '#1e1408',   // near-black brown stone
        wallHighlight:   '#6a4828',   // lighter brown catch-light
        wallShadow:      '#100804',   // deep shadow
        doorLocked:      '#6b2800',   // dark iron-brown
        doorLockedBar:   '#3a1400',   // darker bar
        doorUnlocked:    '#286820',   // earthy green open
        doorUnlockedGlow:'#50c840',   // bright open glow
        exit:            '#184898',   // deep staircase blue
        exitGlow:        '#4888f0',   // stair glow
        key:             '#f0c820',   // treasure gold
        keyShine:        '#fff8a0',   // gold shine
        item:            '#888060',   // dusty item
        agentA:          '#3888f8',   // blue trainer
        agentAHair:      '#1848b8',   // dark blue hair
        agentB:          '#e05030',   // red rival
        agentBHair:      '#901808',   // dark red hair
        fog:             'rgba(16, 8, 2, 0.76)',   // dark earth fog
        fogTint:         'rgba(60, 30, 8, 0.18)',  // warm brown atmospheric
        pathA:           'rgba(56, 136, 248, 0.14)',
        pathB:           'rgba(224, 80, 48, 0.14)',
        SKIN:            '#f0c8a0',
        EYE:             '#1a1008',
        PANTS:           '#3a2810',
    },

    init(canvasId, gridSize) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.ctx.imageSmoothingEnabled = false;
        this.gridSize = gridSize || 8;
        this.cellSize = Math.floor(this.canvas.width / this.gridSize);
    },

    render(worldState, config, visibleCells, pathHistory) {
        const ctx = this.ctx;
        const cs = this.cellSize;
        const gs = this.gridSize;

        // Background fill
        ctx.fillStyle = this.colors.bg;
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        const grid = worldState.grid;

        // Draw base cells
        for (let r = 0; r < gs; r++) {
            for (let c = 0; c < gs; c++) {
                const x = c * cs;
                const y = r * cs;
                const cellType = grid[r][c];

                if (cellType === 'wall') {
                    this._drawWall(ctx, x, y, cs);
                } else if (cellType === 'door') {
                    this._drawDoor(ctx, x, y, cs, worldState.door_locked);
                } else if (cellType === 'exit') {
                    this._drawExit(ctx, x, y, cs);
                } else {
                    this._drawFloor(ctx, x, y, cs, (r + c) % 2 === 0);
                }
            }
        }

        // Draw path history (faint tinted tiles)
        if (pathHistory) {
            for (const [agentId, path] of Object.entries(pathHistory)) {
                ctx.fillStyle = agentId === 'agent_a' ? this.colors.pathA : this.colors.pathB;
                for (const [pr, pc] of path) {
                    ctx.fillRect(pc * cs, pr * cs, cs, cs);
                }
            }
        }

        // Draw items on the ground
        for (const [itemName, pos] of Object.entries(worldState.items || {})) {
            const [r, c] = pos;
            const cx = c * cs + cs / 2;
            const cy = r * cs + cs / 2;
            if (itemName === 'key') {
                this._drawKey(ctx, cx, cy);
            } else {
                this._drawItem(ctx, cx, cy);
            }
        }

        // Draw agents as pixel characters
        for (const [agentId, pos] of Object.entries(worldState.agent_positions || {})) {
            const [r, c] = pos;
            const cx = c * cs + cs / 2;
            const cy = r * cs + cs / 2;
            this._drawAgent(ctx, cx, cy, agentId);
        }

        // Fog of war overlay
        if (visibleCells) {
            for (let r = 0; r < gs; r++) {
                for (let c = 0; c < gs; c++) {
                    if (!visibleCells.has(`${r},${c}`)) {
                        ctx.fillStyle = this.colors.fog;
                        ctx.fillRect(c * cs, r * cs, cs, cs);
                        // Add slight purple atmospheric tint to fog
                        ctx.fillStyle = this.colors.fogTint;
                        ctx.fillRect(c * cs, r * cs, cs, cs);
                    }
                }
            }
        }
    },

    // ── Tile drawers ──

    _drawFloor(ctx, x, y, cs, alt) {
        // Base floor fill
        ctx.fillStyle = alt ? this.colors.floorAlt : this.colors.floor;
        ctx.fillRect(x, y, cs, cs);

        // Subtle stone joint lines (1px)
        ctx.fillStyle = 'rgba(0,0,0,0.35)';
        // top edge
        ctx.fillRect(x, y, cs, 1);
        // left edge
        ctx.fillRect(x, y, 1, cs);
        // Faint inner highlight
        ctx.fillStyle = 'rgba(255,255,255,0.03)';
        ctx.fillRect(x + 1, y + 1, cs - 2, cs - 2);
    },

    _drawWall(ctx, x, y, cs) {
        // Base dark fill
        ctx.fillStyle = this.colors.wall;
        ctx.fillRect(x, y, cs, cs);

        // Stone block inner rect (slightly lighter = carved out)
        ctx.fillStyle = 'rgba(255,255,255,0.04)';
        ctx.fillRect(x + 3, y + 3, cs - 6, cs - 6);

        // Highlight: top edge (light source from top)
        ctx.fillStyle = this.colors.wallHighlight;
        ctx.fillRect(x, y, cs, 2);
        ctx.fillRect(x, y, 2, cs);

        // Shadow: bottom and right
        ctx.fillStyle = this.colors.wallShadow;
        ctx.fillRect(x, y + cs - 2, cs, 2);
        ctx.fillRect(x + cs - 2, y, 2, cs);

        // Occasional crack (deterministic based on position)
        const hash = (x * 7 + y * 13) % 17;
        if (hash < 4) {
            ctx.fillStyle = 'rgba(0,0,0,0.5)';
            const cx2 = x + cs / 2 + (hash - 2) * 4;
            ctx.fillRect(cx2, y + 8, 1, cs / 2);
        }
    },

    _drawDoor(ctx, x, y, cs, locked) {
        if (locked) {
            // Locked door: dark iron bars look
            ctx.fillStyle = this.colors.doorLocked;
            ctx.fillRect(x, y, cs, cs);

            // Horizontal bar pattern
            ctx.fillStyle = this.colors.doorLockedBar;
            for (let i = 0; i < 4; i++) {
                const barY = y + 8 + i * (cs - 16) / 3;
                ctx.fillRect(x + 4, barY, cs - 8, 4);
            }

            // Keyhole in center
            ctx.fillStyle = 'rgba(0,0,0,0.8)';
            ctx.beginPath();
            ctx.arc(x + cs / 2, y + cs / 2 - 4, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillRect(x + cs / 2 - 3, y + cs / 2 - 2, 6, 10);
        } else {
            // Unlocked door: open passage with green glow
            ctx.fillStyle = this.colors.doorUnlocked;
            ctx.fillRect(x, y, cs, cs);

            // Glow effect at edges
            const gradient = ctx.createRadialGradient(
                x + cs / 2, y + cs / 2, 4,
                x + cs / 2, y + cs / 2, cs / 2
            );
            gradient.addColorStop(0, 'rgba(0, 184, 104, 0.4)');
            gradient.addColorStop(1, 'rgba(0, 104, 64, 0)');
            ctx.fillStyle = gradient;
            ctx.fillRect(x, y, cs, cs);
        }
    },

    _drawExit(ctx, x, y, cs) {
        // Exit: deep blue portal (like pokemon cave stairs)
        ctx.fillStyle = this.colors.exit;
        ctx.fillRect(x, y, cs, cs);

        // Staircase chevrons
        ctx.fillStyle = this.colors.exitGlow;
        const steps = 3;
        const sw = cs - 12; // stair width
        const sx = x + 6;
        for (let i = 0; i < steps; i++) {
            const sy = y + 8 + i * ((cs - 16) / steps);
            const sh = (cs - 16) / steps - 2;
            const indent = i * 4;
            ctx.fillRect(sx + indent, sy, sw - indent * 2, 3);
        }

        // Glow
        const gradient = ctx.createRadialGradient(
            x + cs / 2, y + cs / 2, 4,
            x + cs / 2, y + cs / 2, cs / 2
        );
        gradient.addColorStop(0, 'rgba(72, 104, 248, 0.5)');
        gradient.addColorStop(1, 'rgba(24, 48, 168, 0)');
        ctx.fillStyle = gradient;
        ctx.fillRect(x, y, cs, cs);
    },

    // ── Item drawers ──

    _drawKey(ctx, cx, cy) {
        // Gold key shape
        const r = 7;

        // Key ring
        ctx.strokeStyle = this.colors.key;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(cx - 4, cy - 3, r / 2 + 1, 0, Math.PI * 2);
        ctx.stroke();

        // Key stem
        ctx.fillStyle = this.colors.key;
        ctx.fillRect(cx - 1, cy - 3, 3, 11);

        // Key teeth
        ctx.fillRect(cx + 2, cy + 3, 4, 3);
        ctx.fillRect(cx + 2, cy + 7, 3, 2);

        // Shine dot
        ctx.fillStyle = this.colors.keyShine;
        ctx.fillRect(cx - 6, cy - 5, 2, 2);
    },

    _drawItem(ctx, cx, cy) {
        // Generic item: small gem
        ctx.fillStyle = this.colors.item;
        ctx.fillRect(cx - 3, cy - 2, 6, 5);
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.fillRect(cx - 2, cy - 1, 2, 2);
    },

    // ── Agent pixel-art character sprites ──

    _drawAgent(ctx, cx, cy, agentId) {
        const isA = agentId === 'agent_a';
        const mainColor = isA ? this.colors.agentA : this.colors.agentB;
        const hairColor = isA ? this.colors.agentAHair : this.colors.agentBHair;
        const SKIN  = this.colors.SKIN;
        const EYE   = this.colors.EYE;
        const PANTS = this.colors.PANTS;
        const SHOES = isA ? '#182858' : '#501008';

        const s = 4;  // 4 screen px per game pixel
        // Sprite is 10 cols × 12 rows = 40×48 px, centered in cell
        const ox = Math.floor(cx - 5 * s);
        const oy = Math.floor(cy - 6 * s);

        const draw = (col, row, color) => {
            ctx.fillStyle = color;
            ctx.fillRect(ox + col * s, oy + row * s, s, s);
        };

        // Row 0: hair top (cols 2-7)
        [2,3,4,5,6,7].forEach(c => draw(c, 0, hairColor));
        // Row 1: hair + face sides
        [1,2,7,8].forEach(c => draw(c, 1, hairColor));
        [3,4,5,6].forEach(c => draw(c, 1, SKIN));
        // Row 2: face with eyes
        [1,8].forEach(c => draw(c, 2, hairColor));
        [2,3,4,5,6,7].forEach(c => draw(c, 2, SKIN));
        draw(3, 2, EYE); // left eye
        draw(6, 2, EYE); // right eye
        // Row 3: face lower (mouth)
        [2,3,4,5,6,7].forEach(c => draw(c, 3, SKIN));
        draw(4, 3, '#a06040'); // tiny mouth
        // Row 4: neck
        [4,5].forEach(c => draw(c, 4, SKIN));
        // Row 5: shoulders/body top
        [2,3,4,5,6,7].forEach(c => draw(c, 5, mainColor));
        // Row 6: arms + body
        [1,2,3,4,5,6,7,8].forEach(c => draw(c, 6, mainColor));
        // Row 7: body
        [1,2,3,4,5,6,7,8].forEach(c => draw(c, 7, mainColor));
        // Row 8: body lower
        [2,3,4,5,6,7].forEach(c => draw(c, 8, mainColor));
        // Row 9: hips
        [2,3,4,5,6,7].forEach(c => draw(c, 9, PANTS));
        // Row 10: upper legs
        [2,3].forEach(c => draw(c, 10, PANTS));
        [6,7].forEach(c => draw(c, 10, PANTS));
        // Row 11: lower legs/shoes
        [2,3].forEach(c => draw(c, 11, SHOES));
        [6,7].forEach(c => draw(c, 11, SHOES));

        // Agent label (tiny letter above head)
        ctx.fillStyle = 'rgba(255,255,255,0.9)';
        ctx.font = `bold ${s + 2}px 'Courier New', monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(isA ? 'A' : 'B', cx, oy - 2);
    },

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
