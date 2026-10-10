from uuid import uuid4

from ..models import SpawnAircraftRequest
from ..routing.mapf import WEIGHT_CLASSES


def spawn_aircraft(sim, request: SpawnAircraftRequest):
    """Put an aircraft at a taxiway node, waiting for the controller to route it."""

    # The planner only knows taxiway nodes, so aircraft can't appear on a runway.
    if request.position not in sim.ap.graph:
        raise ValueError("Invalid spawn position")

    # Check the destination node (a taxiway node, or a runway end to take off from).
    try:
        sim.destination_goal(request.destination)
    except ValueError:
        raise ValueError("Invalid destination")

    # Prevent duplicate active callsigns.
    if any(
        a.callsign == request.callsign
        and a.status != "arrived"
        for a in sim.state.aircraft
    ):
        raise ValueError("Callsign already active")

    # Prevent spawning onto an occupied node, or one someone is due to pass.
    if not sim.node_free(request.position):
        raise ValueError("Spawn position occupied")

    if any(
        v.position == request.position
        for v in sim.state.vehicles
    ):
        raise ValueError("Spawn position occupied by vehicle")

    weight = request.weight_class if request.weight_class in WEIGHT_CLASSES else "large"
    return sim.add_waiting(
        f"AC-{uuid4().hex[:8]}",
        request.callsign,
        request.aircraft_type,
        weight,
        request.position,
        request.destination,
    )
