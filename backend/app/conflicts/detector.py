# backend/app/conflicts/detector.py

from ..models import Conflict, SimulationState


def detect_conflicts(state: SimulationState):
    conflicts = []

    aircraft = state.aircraft

    for i in range(len(aircraft)):
        for j in range(i + 1, len(aircraft)):

            a = aircraft[i]
            b = aircraft[j]

            if (
                a.position == b.position
                and a.status == "taxiing"
                and b.status == "taxiing"
            ):
                conflicts.append(
                    Conflict(
                        aircraft_ids=[a.id, b.id],
                        location=a.position,
                        severity="critical"
                    )
                )

    state.conflicts = conflicts

    return conflicts