# backend/app/simulation/scenarios.py

from ..models import (
    Aircraft,
    Vehicle,
    SimulationState
)


def load_scenario(scenario_id: str):

    if scenario_id == "challenge_01":
        return SimulationState(
            aircraft=[
                Aircraft(
                    id="AC001",
                    callsign="DAL123",
                    position="RWY_4",
                    destination="GATE_A1",
                    status="waiting"
                )
            ],
            vehicles=[
                Vehicle(
                    id="TRUCK01",
                    position="T3"
                )
            ]
        )

    if scenario_id == "default":
        from .state import create_initial_state
        return create_initial_state()

    raise ValueError("Scenario not found")