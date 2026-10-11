import asyncio
import contextlib

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .drowsiness.detector import (
    DrowsinessDetector,
    InvalidFrameError,
    ModelUnavailableError,
)
from .routing.graph import airport_graph
from .routing.astar import astar

from .models import SimulationState
from .simulation import state as world
from .simulation.engine import TICK_S, simulation_tick

from pydantic import BaseModel
from .simulation.scenarios import load_scenario

from .models import (
    HoldRequest,
    RouteRequest,
    SpawnAircraftRequest,
    SpawnAircraftResponse,
    SpawnVehicleRequest,
)
from .simulation.spawner import spawn_aircraft

class ScenarioRequest(BaseModel):
    scenario_id: str

# Open /api/ws connections; each gets the full state after every tick.
clients: set[WebSocket] = set()


async def broadcast(payload: str):
    async def send(ws):
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=1)
        except Exception:
            clients.discard(ws)

    await asyncio.gather(*(send(ws) for ws in list(clients)))


async def tick_loop():
    loop = asyncio.get_running_loop()
    last = loop.time()
    while True:
        await asyncio.sleep(TICK_S)
        now = loop.time()
        # Ticks that come late (a slow plan) catch up, so sim time tracks wall time.
        simulation_tick(world.sim, min(now - last, 4 * TICK_S))
        last = now
        if clients:
            await broadcast(world.sim.state.model_dump_json())


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    world.sim.state.running = True
    task = asyncio.create_task(tick_loop())
    yield
    task.cancel()


app = FastAPI(title="Hold Short Backend", lifespan=lifespan)
drowsiness_detector = DrowsinessDetector()

MAX_FRAME_BYTES = 5 * 1024 * 1024

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every endpoint that changes the simulation is async, so it runs on the event
# loop between ticks instead of in a worker thread in the middle of one.


@app.get("/")
def home():
    return {"message": "Hold Short API Running"}


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/state", response_model=SimulationState)
async def get_state():
    return world.sim.state.model_copy(deep=True)


@app.websocket("/api/ws")
async def state_socket(ws: WebSocket):
    """Pushes the same JSON as GET /api/state after every tick (every 250 ms)."""
    await ws.accept()
    await ws.send_text(world.sim.state.model_dump_json())
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # nothing to read; this just notices the close
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


@app.post("/api/reset", response_model=SimulationState)
async def reset_simulation():
    world.sim = load_scenario("default")
    return world.sim.state.model_copy(deep=True)


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

@app.post("/api/scenario/load", response_model=SimulationState)
async def load_game_scenario(request: ScenarioRequest):
    try:
        world.sim = load_scenario(
            request.scenario_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        )

    return world.sim.state.model_copy(deep=True)

@app.post(
    "/api/aircraft/spawn",
    response_model=SpawnAircraftResponse,
    status_code=201
)
async def create_aircraft(request: SpawnAircraftRequest):

    try:
        aircraft = spawn_aircraft(
            world.sim,
            request
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    return SpawnAircraftResponse(
        aircraft=aircraft,
        message="Aircraft spawned successfully"
    )

def _act(action, aircraft_id, *args):
    try:
        return action(aircraft_id, *args).model_copy(deep=True)
    except KeyError:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    except ValueError as exc:
        # No conflict-free route, already gone, on a landing roll, ...
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/aircraft/{aircraft_id}/route")
async def route_aircraft(aircraft_id: str, request: RouteRequest):
    try:
        world.sim.destination_goal(request.destination)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _act(world.sim.route, aircraft_id, request.destination)


@app.post("/api/aircraft/{aircraft_id}/hold")
async def hold_aircraft(aircraft_id: str, request: HoldRequest):
    return _act(world.sim.hold, aircraft_id, request.hold)


@app.post("/api/aircraft/{aircraft_id}/assign-route")
async def assign_aircraft_route(aircraft_id: str):
    try:
        aircraft = world.sim.aircraft(aircraft_id)
        aircraft = world.sim.route(aircraft_id, aircraft.destination)
    except KeyError:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    track = world.sim.tracks[aircraft_id]
    return {
        "aircraft_id": aircraft.id,
        "route": aircraft.route,
        "estimated_time": max(0.0, track.end_t - world.sim.now),
        "status": aircraft.status
    }


def _act_vehicle(action, vehicle_id, *args):
    try:
        return action(vehicle_id, *args).model_copy(deep=True)
    except KeyError:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/vehicles", status_code=201)
async def spawn_vehicle(request: SpawnVehicleRequest):
    try:
        vehicle = world.sim.add_vehicle(request.id, [request.position])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return vehicle.model_copy(deep=True)


@app.post("/api/vehicles/{vehicle_id}/route")
async def route_vehicle(vehicle_id: str, request: RouteRequest):
    """Fastest ground path from where the vehicle is that keeps clear of aircraft and runways in use."""
    return _act_vehicle(world.sim.route_vehicle, vehicle_id, request.destination)


@app.post("/api/vehicles/{vehicle_id}/hold")
async def hold_vehicle(vehicle_id: str, request: HoldRequest):
    return _act_vehicle(world.sim.hold_vehicle, vehicle_id, request.hold)


@app.post("/api/simulation/start")
async def start_simulation():
    world.sim.state.running = True

    return {
        "message": "Simulation started",
        "time": world.sim.state.time
    }


@app.post("/api/simulation/pause")
async def pause_simulation():
    world.sim.state.running = False

    return {
        "message": "Simulation paused",
        "time": world.sim.state.time
    }


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
