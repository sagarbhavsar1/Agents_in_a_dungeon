"""Tool definitions in Anthropic API format.

These are passed to the Claude API as the `tools` parameter.
Execution logic lives in world.py.
"""

AGENT_TOOLS = [
    {
        "name": "move",
        "description": "Move one cell in a cardinal direction. You cannot move into walls or through a locked door.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["north", "south", "east", "west"],
                    "description": "The direction to move",
                }
            },
            "required": ["direction"],
        },
    },
    {
        "name": "look",
        "description": "Observe your current cell and all adjacent cells. Returns cell types, items, agents, and door status for each visible cell.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "pick_up",
        "description": "Pick up an item in your current cell and add it to your inventory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": "The name of the item to pick up",
                }
            },
            "required": ["item"],
        },
    },
    {
        "name": "check_coordinates",
        "description": "Get your current (row, column) position on the grid.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "check_inventory",
        "description": "Check what items you are currently carrying.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "use_item",
        "description": "Use an item from your inventory on a target. For example, use the key on the door to unlock it. You must be adjacent to or on the target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": "The item to use from your inventory",
                },
                "target": {
                    "type": "string",
                    "description": "What to use the item on (e.g., 'door')",
                },
            },
            "required": ["item", "target"],
        },
    },
    {
        "name": "send_message",
        "description": "Send a message to the other agent. The message will be delivered at the start of their next turn, not instantly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "The agent to send the message to (agent_a or agent_b)",
                },
                "message": {
                    "type": "string",
                    "description": "The message content",
                },
            },
            "required": ["agent", "message"],
        },
    },
    {
        "name": "wait",
        "description": "Skip this turn and do nothing. Use when you have no useful action to take.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]
