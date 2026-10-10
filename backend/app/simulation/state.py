from ..models import Aircraft, SimulationState

def create_initial_state():
    return SimulationState(
        aircraft=[
            Aircraft(
                id="AC001",
                callsign="DAL123",
                position="GATE_A1",
                destination="RWY_4"
            ),
            Aircraft(
                id="AC002",
                callsign="JBU456",
                position="GATE_B2",
                destination="RWY_13"
            )
        ]
    )

state = create_initial_state()