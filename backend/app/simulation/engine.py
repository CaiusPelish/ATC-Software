# backend/app/simulation/engine.py

from ..models import SimulationState

from ..conflicts.detector import detect_conflicts


def simulation_tick(
    state: SimulationState,
    delta_time: float
):
    if not state.running:
        return state

    state.time += delta_time

    for aircraft in state.aircraft:
        if aircraft.status == "taxiing":
            aircraft.progress += (
                aircraft.speed * delta_time
            )

            if aircraft.progress >= 1.0:
                aircraft.progress = 1.0

    return state