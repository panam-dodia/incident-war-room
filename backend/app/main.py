"""FastAPI app: REST endpoints for the incident catalog and batch eval, plus a
WebSocket that streams a live incident run (bids -> allocation -> negotiation
-> resolution -> baseline) to the dashboard. Also serves the static frontend."""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=ROOT_DIR / ".env")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.evaluator import run_full_eval
from app.incidents import INCIDENTS, get_incident
from app.orchestrator import run_baseline_run, run_multi_agent
from app.qwen_client import qwen_client
from app.validation_history import historical_summary

app = FastAPI(title="Incident War Room")

FRONTEND_DIR = ROOT_DIR / "frontend"


@app.get("/api/health")
def health():
    return {"status": "ok", "mock_mode": qwen_client.mock_mode}


@app.get("/api/incidents")
def list_incidents():
    return [
        {
            "id": i.id,
            "title": i.title,
            "alert": i.alert,
            "tools": {s.value: t.description for s, t in i.tools.items()},
            "cross_cutting": i.cross_cutting,
            "ground_truth_specialist": i.ground_truth_specialist.value,
            "ground_truth_root_cause": i.ground_truth_root_cause,
        }
        for i in INCIDENTS
    ]


@app.post("/api/eval/run")
def eval_run():
    return run_full_eval()


@app.get("/api/eval/history")
def eval_history():
    """Static snapshots of past real Qwen Cloud full-batch runs -- shows the
    accuracy/cost gap was checked repeatedly, not just on whatever run happens
    to be live right now."""
    return historical_summary()


@app.websocket("/ws/run/{incident_id}")
async def ws_run(websocket: WebSocket, incident_id: str):
    await websocket.accept()
    try:
        incident = get_incident(incident_id)
    except KeyError:
        await websocket.send_json({"type": "error", "payload": {"message": f"Unknown incident id: {incident_id}"}})
        await websocket.close()
        return

    loop = asyncio.get_event_loop()

    def send(type_: str, payload: dict) -> None:
        asyncio.run_coroutine_threadsafe(websocket.send_json({"type": type_, "payload": payload}), loop)

    try:
        await websocket.send_json({"type": "incident", "payload": incident.model_dump()})
        ma_result = await loop.run_in_executor(None, lambda: run_multi_agent(incident, on_event=send))
        bl_result = await loop.run_in_executor(None, lambda: run_baseline_run(incident, on_event=send))
        await websocket.send_json(
            {
                "type": "run_complete",
                "payload": {
                    "multi_agent": ma_result.model_dump(),
                    "baseline": bl_result.model_dump(),
                },
            }
        )
    except WebSocketDisconnect:
        return
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
