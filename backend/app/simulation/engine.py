# backend/app/simulation/engine.py

TICK_S = 0.25


def simulation_tick(sim, delta_time=TICK_S):
    """Advance the live simulation (simulation/live.py) by delta_time seconds."""
    return sim.tick(delta_time)
