from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .drowsiness.detector import (
    DrowsinessDetector,
    InvalidFrameError,
    ModelUnavailableError,
)
from .models import SimulationState
from .routing.astar import astar
from .routing.graph import airport_graph
from .simulation import state as simulation
from .simulation.scenarios import load_scenario


class ScenarioRequest(BaseModel):
    scenario_id: str


app = FastAPI(title="Hold Short Backend")
drowsiness_detector = DrowsinessDetector()

MAX_FRAME_BYTES = 5 * 1024 * 1024

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "Hold Short API Running"}


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/state", response_model=SimulationState)
def get_state():
    return simulation.state.model_copy(deep=True)


@app.post("/api/reset", response_model=SimulationState)
def reset_simulation():
    simulation.state = simulation.create_initial_state()
    return simulation.state.model_copy(deep=True)


@app.get("/api/graph")
def get_graph():
    return airport_graph


@app.get("/api/route")
def get_route(
    start: str = Query(...),
    goal: str = Query(...),
):
    try:
        result = astar(airport_graph, start, goal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="No route found")

    return result


@app.post("/api/scenario/load")
def load_game_scenario(request: ScenarioRequest):
    try:
        simulation.state = load_scenario(request.scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return simulation.state.model_copy(deep=True)


@app.post("/api/drowsiness/frame")
def analyze_drowsiness_frame(
    session_id: str = Form(min_length=1, max_length=128),
    frame: UploadFile = File(...),
):
    try:
        if frame.content_type not in {"image/jpeg", "image/png"}:
            raise HTTPException(
                status_code=415,
                detail="Upload a JPEG or PNG camera frame.",
            )

        image_bytes = frame.file.read(MAX_FRAME_BYTES + 1)
        if len(image_bytes) > MAX_FRAME_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Camera frames must be 5 MB or smaller.",
            )

        return drowsiness_detector.analyze(session_id, image_bytes)
    except InvalidFrameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        frame.file.close()


@app.delete("/api/drowsiness/session/{session_id}")
def reset_drowsiness_session(session_id: str):
    drowsiness_detector.reset(session_id)
    return {"status": "reset"}