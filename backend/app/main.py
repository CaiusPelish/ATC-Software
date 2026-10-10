from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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