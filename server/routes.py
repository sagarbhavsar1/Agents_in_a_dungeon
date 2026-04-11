"""API routes: read-only access to run logs."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api")

RUNS_DIR = Path(__file__).parent.parent / "runs"


def _load_run(run_id: str) -> dict:
    """Load a run log by ID."""
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    with open(path) as f:
        return json.load(f)


@router.get("/runs")
def list_runs():
    """List all runs with their manifests."""
    runs = []
    if not RUNS_DIR.exists():
        return runs

    for path in sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path) as f:
                data = json.load(f)
            runs.append(data["manifest"])
        except (json.JSONDecodeError, KeyError):
            continue
    return runs


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """Get full run data: manifest + all events."""
    return _load_run(run_id)


@router.get("/runs/{run_id}/events")
def get_events(run_id: str, agent: str | None = None, turn: int | None = None):
    """Get filtered events for a run."""
    data = _load_run(run_id)
    events = data.get("events", [])

    if agent:
        events = [e for e in events if e["agent_id"] == agent]
    if turn is not None:
        events = [e for e in events if e["turn_number"] == turn]

    return events


@router.get("/runs/{run_id}/divergences")
def get_divergences(run_id: str):
    """Get all divergence records for a run, sorted by severity."""
    data = _load_run(run_id)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    divergences = []
    for event in data.get("events", []):
        for div in event.get("divergences", []):
            divergences.append({
                **div,
                "turn_number": event["turn_number"],
                "agent_id": event["agent_id"],
                "tool_name": event["tool_name"],
            })

    divergences.sort(key=lambda d: severity_order.get(d.get("severity", "low"), 4))
    return divergences


@router.get("/runs/{run_id}/timeline")
def get_timeline(run_id: str):
    """Turn-by-turn summary for the timeline view."""
    data = _load_run(run_id)
    timeline = []

    for event in data.get("events", []):
        entry = {
            "turn_number": event["turn_number"],
            "agent_id": event["agent_id"],
            "tool_name": event["tool_name"],
            "tool_success": event["tool_success"],
            "divergence_count": len(event.get("divergences", [])),
            "divergence_severities": [
                d["severity"] for d in event.get("divergences", [])
            ],
            "message_sent": event.get("message_sent") is not None,
            "has_messages": len(event.get("pending_messages", [])) > 0,
        }

        # Key milestones
        if event["tool_name"] == "pick_up" and event["tool_success"]:
            if event["tool_input"].get("item") == "key":
                entry["milestone"] = "key_found"
        elif event["tool_name"] == "use_item" and event["tool_success"]:
            if event["tool_input"].get("item") == "key":
                entry["milestone"] = "door_unlocked"

        timeline.append(entry)

    return timeline
