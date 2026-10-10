from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .routing.graph import airport_graph
from .routing.astar import astar

from .models import SimulationState
from .simulation import state as simulation

app = FastAPI(title="Hold Short Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "Hold Short API Running"}


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
    goal: str = Query(...)
):
    try:
        result = astar(
            airport_graph,
            start,
            goal
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No route found"
        )

    return result