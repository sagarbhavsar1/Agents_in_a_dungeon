"""API routes: read-only access to run logs."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
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
            # Decision quality
            "expected_tool_outcome": event.get("expected_tool_outcome"),
            "outcome_matched_expectation": event.get("outcome_matched_expectation"),
            "decision_info_age": event.get("decision_info_age", 0),
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


@router.get("/runs/{run_id}/report")
def get_report(run_id: str):
    """Post-hoc diagnosis: failure mode, bottlenecks, key insights."""
    data = _load_run(run_id)
    return {
        "manifest": data["manifest"],
        "diagnosis": data.get("diagnosis"),
    }


@router.get("/runs/{run_id}/langfuse")
def get_langfuse_trace(run_id: str):
    """Proxy: fetch live Langfuse trace for this run.

    Reads the trace ID stored in the run manifest, calls the Langfuse REST API
    server-side (keys never reach the browser), and returns the full trace with
    all observations and scores.
    """
    data = _load_run(run_id)
    trace_id = data.get("manifest", {}).get("langfuse_trace_id")
    if not trace_id:
        raise HTTPException(status_code=404, detail="No Langfuse trace ID stored for this run. Re-run the simulation to capture it.")

    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    pub  = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sec  = os.getenv("LANGFUSE_SECRET_KEY", "")

    if not pub or not sec:
        raise HTTPException(status_code=503, detail="LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not configured in environment.")

    creds = base64.b64encode(f"{pub}:{sec}".encode()).decode()
    url   = f"{host}/api/public/traces/{trace_id}"

    req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=e.code, detail=f"Langfuse API returned {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Langfuse: {e.reason}")
