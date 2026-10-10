from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .models import SimulationState
from .routing.astar import astar
from .routing.graph import airport_graph
from .simulation import state as simulation
from .simulation.scenarios import load_scenario


class ScenarioRequest(BaseModel):
    scenario_id: str


app = FastAPI(title="Hold Short Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
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