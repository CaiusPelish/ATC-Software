# backend/app/alerts/manager.py

def calculate_alert_severity(
    conflict_severity: str,
    fatigue_score: float
):
    fatigue_score = max(
        0.0, min(fatigue_score, 1.0)
    )

    if conflict_severity == "critical":
        return "critical"

    if (
        conflict_severity == "warning"
        and fatigue_score >= 0.7
    ):
        return "high"

    if conflict_severity == "warning":
        return "medium"

    return "low"

# --- Alerts shown to (and spoken for) the controller ---------------------------------

from ..models import Alert  # noqa: E402
from ..routing.graph import ground_nodes  # noqa: E402


def _name(sim, mid):
    """Callsign for an aircraft, id for a ground vehicle, a generic word for anything else."""
    for a in sim.state.aircraft:
        if a.id == mid:
            return a.callsign
    if any(v.id == mid for v in sim.state.vehicles):
        return mid
    # An arrival still on final isn't in the state yet.
    return "landing traffic"


def _place(location):
    if "/" in location and location not in ground_nodes:
        return f"runway {location}"
    node = ground_nodes.get(location)
    return f"taxiway {node.name}" if node and node.name else location


def _seconds(s):
    return f"{round(s)} s"


def solution_phrase(vid, via):
    """What the controller says when sending a solution path."""
    if not via:
        return f"{vid}, hold position, you are clear of traffic."
    return f"{vid}, solution path via taxiway {' and '.join(via)}, then hold position."


def _resolution(sim, mid, vehicles):
    """A ground vehicle gets a solution path; an aircraft is told to hold."""
    return f"{_name(sim, mid)}, solution path available." if mid in vehicles else f"{_name(sim, mid)}, hold position."


def build_alerts(sim, fatigue_score: float) -> list[Alert]:
    """One alert per conflict (happening now) and per predicted collision.

    Severity is the conflict's own, raised by fatigue (calculate_alert_severity):
    a tired controller gets "high" instead of "medium" for the same warning.
    """
    seen = sim.__dict__.setdefault("_alert_first_seen", {})
    fatigue = max(0.0, min(fatigue_score, 100.0))
    vehicles = {v.id for v in sim.state.vehicles}
    alerts, live_ids = [], set()

    def add(kind, ids, location, base_severity, text, phrase, seconds=None):
        aid = f"{kind}:{'+'.join(sorted(ids))}:{location}"
        if aid in live_ids:
            return
        live_ids.add(aid)
        created = seen.setdefault(aid, round(sim.now, 2))
        alerts.append(Alert(
            id=aid, kind=kind, severity=calculate_alert_severity(base_severity, fatigue / 100),
            text=text, atc_phrase=phrase, vehicle_ids=list(ids), location=location,
            seconds=seconds, fatigue=round(fatigue, 1), created_s=created,
        ))

    for c in sim.state.conflicts:
        a, b = (c.aircraft_ids + ["traffic"])[:2]
        # Whoever has to stop: the ground vehicle if there is one, else the first party.
        stop = next((m for m in c.aircraft_ids if m in vehicles), a)
        if "/" in c.location:
            soon = c.seconds_to_conflict or 0
            text = (f"{_name(sim, a)} is on runway {c.location} with {_name(sim, b)} "
                    + (f"reaching it in {_seconds(soon)}." if soon > 0 else "on it."))
            add("runway", c.aircraft_ids, c.location, c.severity, text,
                _resolution(sim, stop, vehicles), c.seconds_to_conflict)
        else:
            gap = "20" if c.severity == "critical" else "40"
            add("separation", c.aircraft_ids, c.location, c.severity,
                f"{_name(sim, a)} and {_name(sim, b)} are less than {gap} m apart on {_place(c.location)}.",
                _resolution(sim, stop, vehicles))

    for w in sim.state.warnings:
        a, b = (w.aircraft_ids + ["traffic"])[:2]
        add("predicted", w.aircraft_ids, w.location, "warning",
            f"{_name(sim, a)} will meet {_name(sim, b)} on {_place(w.location)} in {_seconds(w.eta)}.",
            _resolution(sim, a, vehicles), round(w.eta, 1))

    # Forget alerts that have ended, so the same pair later counts as new.
    for aid in [k for k in seen if k not in live_ids]:
        del seen[aid]
    return alerts
