from uuid import uuid4

from ..models import Aircraft, SpawnAircraftRequest
from ..routing.graph import airport_graph
from .state import create_initial_state


def spawn_aircraft(state, request: SpawnAircraftRequest):

    # Check the requested spawn node.
    if request.position not in airport_graph:
        raise ValueError("Invalid spawn position")

    # Check the destination node.
    if request.destination not in airport_graph:
        raise ValueError("Invalid destination")

    # Prevent duplicate active callsigns.
    if any(
        a.callsign == request.callsign
        and a.status != "arrived"
        for a in state.aircraft
    ):
        raise ValueError("Callsign already active")

    # Prevent spawning onto an occupied node.
    if any(
        a.position == request.position
        and a.status != "arrived"
        for a in state.aircraft
    ):
        raise ValueError("Spawn position occupied")

    if any(
        v.position == request.position
        for v in state.vehicles
    ):
        raise ValueError("Spawn position occupied by vehicle")

    aircraft = Aircraft(
        id=f"AC-{uuid4().hex[:8]}",
        callsign=request.callsign,
        aircraft_type=request.aircraft_type,
        position=request.position,
        destination=request.destination,
        status="waiting",
        route=[],
        speed=0.0,
        progress=0.0
    )

    state.aircraft.append(aircraft)

    return aircraft