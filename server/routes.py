"""API routes: read-only access to run logs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api")

RUNS_DIR = Path(__file__).parent.parent / "runs"

# TTS cache: generated wav files are small and identical text produces
# identical audio, so cache on disk. Keyed by sha256(voice::text).
TTS_CACHE_DIR = RUNS_DIR / "tts_cache"
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Per-agent voice assignment. Chosen to be clearly distinguishable on
# playback so a listener can tell who's speaking without reading labels.
# canopylabs/orpheus-v1-english voices: austin, hannah, troy, etc.
AGENT_VOICES = {
    "agent_a": "austin",
    "agent_b": "hannah",
}
DEFAULT_VOICE = "austin"
TTS_MODEL = "canopylabs/orpheus-v1-english"


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


# ---------------------------------------------------------------------------
# Text-to-speech (Groq + canopylabs/orpheus-v1-english)
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    text: str
    agent_id: str | None = None   # used to pick a default voice
    voice: str | None = None      # explicit voice override


@router.post("/tts")
def tts(req: TTSRequest):
    """Generate speech from text via Groq's Orpheus TTS.

    Why a server-side proxy: the Groq API key never reaches the browser,
    identical text/voice pairs reuse cached audio (cheap + instant on replay),
    and the browser can just play the returned wav file directly.
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    voice = req.voice or AGENT_VOICES.get(req.agent_id or "", DEFAULT_VOICE)

    # Cache key: identical (voice, text) → identical audio, reuse forever
    key = hashlib.sha256(f"{voice}::{text}".encode()).hexdigest()[:16]
    cache_file = TTS_CACHE_DIR / f"{key}.wav"

    if not cache_file.exists():
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="GROQ_API_KEY not configured in environment.",
            )

        payload = json.dumps({
            "model": TTS_MODEL,
            "voice": voice,
            "input": text,
            "response_format": "wav",
        }).encode()

        groq_req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/speech",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # Groq is fronted by Cloudflare which 403s (error 1010) on
                # urllib's default "Python-urllib/3.x" signature. A generic
                # UA unblocks it.
                "User-Agent": "dungeon-agents/0.1 (+https://anthropic.com)",
                "Accept": "audio/wav, application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(groq_req, timeout=30) as resp:
                audio_bytes = resp.read()
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            raise HTTPException(
                status_code=e.code,
                detail=f"Groq TTS returned {e.code}: {e.reason}. {body[:200]}",
            )
        except urllib.error.URLError as e:
            raise HTTPException(status_code=502, detail=f"Could not reach Groq: {e.reason}")

        cache_file.write_bytes(audio_bytes)

    return FileResponse(
        cache_file,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )
