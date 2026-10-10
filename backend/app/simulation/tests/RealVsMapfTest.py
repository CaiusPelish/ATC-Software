# backend/app/simulation/tests/RealVsMapfTest.py

"""Real KEWR taxiing (Amelia42-Mini on Hugging Face) against our MAPF planner.

For every aircraft in the chosen hours with a taxi on the ground:
- departures: first seen on the ground, taxi ends at the last stop before the takeoff roll;
- arrivals: first seen airborne, taxi starts once the landing roll is over.
Walk along the taxi's points until the first one within NODE_SNAP_M of a
graph node: that node is the start, and the real taxi is measured from there.
The final point is snapped to its nearest node: that's the goal.

All aircraft are then planned together with prioritized space-time A*
(plan_batch), each released at the moment it really reached its start node.
To hold both sides to the same rules (skip all of this with --simple):
1. Runway schedule: every real landing (2 nm final until vacated) and takeoff
   roll closes its runway, so nobody crosses or lines up in front of it, and
   each departure stays on its last node until it really left it.
2. Taxi speed: the real aircraft's average moving speed, not a fixed 8 m/s.
3. Spacing: FOLLOW_M between aircraft on the same taxiway, NODE_CLEAR_M
   around every node, converted to steps at that speed.
4. Ramp hand-off: arrivals keep their last node for ARRIVAL_HOLD_S. Real
   arrivals stop transmitting as soon as they stop moving, so how long the
   gate was occupied isn't in the data.

Metrics, real vs MAPF:
- total taxi length (km): real = GPS track, MAPF = graph edge lengths;
- total taxi time (min): start node to goal (a departure: until it leaves its line-up node);
- avg wait per aircraft (s): real = time at or below STOPPED_SPEED,
  MAPF = time not moving (start delay, waits on the way, holding for the slot).

Not modelled on the MAPF side: pushback, gates, and speed changes along the way.

Writes data/plans/real_vs_mapf_test.csv (one row per aircraft) and
data/plans/real_vs_mapf_test.png (real tracks next to MAPF routes).

Run:  python3 RealVsMapfTest.py [start_hour] [n_hours] [--simple]      (default 194 2)
"""

import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from huggingface_hub import hf_hub_download  # noqa: E402

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from app.routing.airport import DT, M_PER_DEG_LAT, get_airport  # noqa: E402
from app.routing.mapf import AircraftRequest, plan_batch  # noqa: E402
from app.routing.osm_taxiways import edge_points, haversine_m  # noqa: E402
from app.routing.reservations import ReservationTable, check_paths  # noqa: E402
from app.routing.runways import RunwayGate, load_runways, runway_conflicts  # noqa: E402
from app.simulation.hgfacetest import REPO, list_kewr_hours  # noqa: E402
from app.simulation.runway_schedule import real_runway_uses  # noqa: E402
from app.simulation.traffic import (  # noqa: E402
    DEPARTURE_HOLD_S,
    GROUND_ALTITUDE_FT,
    STOPPED_SPEED,
    TAKEOFF_ROLL_SPEED,
    NotEnoughHistory,
    arrival_taxi_rows,
    measured_taxi_speed,
    segment_metrics,
    taxi_rows,
)

# A taxi point this close to a graph node counts as being on it.
NODE_SNAP_M = 20
# A final point further than this from every node (e.g. deep in a gate area) isn't routable.
GOAL_SNAP_M = 150
OUT_DIR = BACKEND / "app" / "simulation" / "data" / "plans"

# Realistic mode.
# Distance kept behind an aircraft ahead on the same taxiway.
FOLLOW_M = 150
# Nobody else on a node until the aircraft there is this far clear (about one airliner length).
NODE_CLEAR_M = 60
# Arrivals hold their last node this long while ramp control takes over.
ARRIVAL_HOLD_S = 20
# The runway closes this long before a real takeoff roll starts.
TAKEOFF_CLOSE_BEFORE_S = 10

# Simple mode (the first version of this test): fixed speed and spacing, no runways.
SIMPLE_ARRIVAL_HOLD_S = DT


def load_hours(start_hour, n_hours):
    """n consecutive hours of KEWR surface data from Hugging Face, with a UnixTime column."""
    hours = list_kewr_hours()
    frames = []
    for h in range(start_hour, start_hour + n_hours):
        if h not in hours:
            raise ValueError(f"No KEWR file for hour {h}")
        ts = int(hours[h].split("_")[-1].replace(".csv", ""))
        df = pd.read_csv(hf_hub_download(REPO, hours[h], repo_type="dataset"))
        df["UnixTime"] = ts + df["Frame"]
        frames.append(df)
    df = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["UnixTime", "ID"])
        .sort_values(["UnixTime", "ID"])
        .reset_index(drop=True)
    )
    # Each hour numbers its frames from 0; traffic.py sorts by Frame, so make it continuous.
    df["Frame"] = df["UnixTime"] - df["UnixTime"].min()
    return df


class NodeSnapper:
    """Nearest graph node to lat/lon points, vectorized over the airport's flat x/y."""

    def __init__(self, ap):
        self.ids = list(ap.xy)
        self.xy = np.array([ap.xy[k] for k in self.ids])
        self._lat0, self._lon0 = ap.origin
        self._m_lon = M_PER_DEG_LAT * math.cos(math.radians(self._lat0))

    def nearest(self, lat, lon):
        """(node ids, distances in m) for arrays of points."""
        px = (np.asarray(lon) - self._lon0) * self._m_lon
        py = (np.asarray(lat) - self._lat0) * M_PER_DEG_LAT
        d = np.hypot(px[:, None] - self.xy[None, :, 0], py[:, None] - self.xy[None, :, 1])
        best = d.argmin(axis=1)
        return [self.ids[i] for i in best], d[np.arange(len(best)), best]


def taxi_of(flight):
    """('departure' | 'arrival', taxi rows) or raises NotEnoughHistory."""
    flight = flight.sort_values("UnixTime").reset_index(drop=True)
    if (flight["Type"] != 0).all():
        raise NotEnoughHistory("is not an aircraft")
    first = flight.iloc[0]
    if first["Altitude"] > GROUND_ALTITUDE_FT:
        return "arrival", arrival_taxi_rows(flight)
    if first["Speed"] > TAKEOFF_ROLL_SPEED or not (flight["Speed"] > TAKEOFF_ROLL_SPEED).any():
        raise NotEnoughHistory("no complete taxi to a takeoff roll")
    return "departure", taxi_rows(flight)


def snap_taxi(taxi, snapper):
    """Cut the taxi at the first point on a node; returns (rows from there, start node, goal node)."""
    ids, dist = snapper.nearest(taxi["Lat"].to_numpy(), taxi["Lon"].to_numpy())
    on_node = np.flatnonzero(dist <= NODE_SNAP_M)
    if not len(on_node):
        raise NotEnoughHistory(f"never comes within {NODE_SNAP_M} m of a node")
    first = on_node[0]
    if dist[-1] > GOAL_SNAP_M:
        raise NotEnoughHistory(f"ends {dist[-1]:.0f} m from the nearest node")
    if ids[first] == ids[-1]:
        raise NotEnoughHistory("starts and ends at the same node")
    return taxi.iloc[first:].reset_index(drop=True), ids[first], ids[-1]


def collect(df, ap):
    """[{id, kind, rows, start, goal}] for every routable aircraft, plus skip reasons."""
    snapper = NodeSnapper(ap)
    flights, skipped = [], {}
    for fid, flight in df.groupby("ID"):
        try:
            kind, taxi = taxi_of(flight)
            rows, start, goal = snap_taxi(taxi, snapper)
        except NotEnoughHistory as err:
            skipped[str(err)] = skipped.get(str(err), 0) + 1
            continue
        flights.append({"id": str(fid), "kind": kind, "rows": rows, "start": start, "goal": goal})
    return flights, skipped


def real_metrics(rows):
    dist, dt, stopped = segment_metrics(rows)
    t = rows["UnixTime"].to_numpy()
    return {"length_m": dist.sum(), "time_s": float(t[-1] - t[0]), "wait_s": float(dt[stopped].sum())}


def mapf_metrics(ap, request, path):
    """Taxi from ready_t until the aircraft leaves its goal for good, minus any hold past that.

    A departure's taxi runs until it leaves its line-up node (its slot, or
    when it got there if that's later); an arrival's ends when it reaches its goal.
    """
    reach = len(path) - 1
    while reach > 0 and path[reach - 1][0] == request.goal:
        reach -= 1
    t_reach = path[reach][1]
    t_end = max(t_reach, request.hold_until) if request.hold_until is not None else t_reach
    moves = [(u, v, t0, t1) for (u, t0), (v, t1) in zip(path[: reach + 1], path[1: reach + 1]) if u != v]
    total = t_end - request.ready_t
    moving = sum(t1 - t0 for _, _, t0, t1 in moves)
    return {
        "length_m": sum(ap.graph[u][v] for u, v, _, _ in moves),
        "time_s": total * DT,
        "wait_s": (total - moving) * DT,
        "late_s": max(0, t_reach - request.hold_until) * DT if request.hold_until is not None else 0,
    }


def build_gate(ap, uses):
    """RunwayGate closed for every real landing (2 nm final to vacated) and takeoff roll."""
    runways = load_runways()
    gate = RunwayGate([runways["4L"], runways["4R"]], airport=ap)
    for u in uses:
        start = u.busy_from if u.kind == "landing" else u.roll_start - TAKEOFF_CLOSE_BEFORE_S
        gate.add_window(u.pair, int(start // DT), math.ceil(u.busy_until / DT), u.id, u.kind)
    return gate


def make_requests(flights, t0, realistic):
    requests = []
    for f in flights:
        rows = f["rows"]
        ready = (int(rows["UnixTime"].iloc[0]) - t0) // DT
        if f["kind"] == "arrival":
            hold = (ARRIVAL_HOLD_S if realistic else SIMPLE_ARRIVAL_HOLD_S) // DT
            requests.append(AircraftRequest(f["id"], f["start"], f["goal"], ready, goal_hold=hold))
        elif realistic:
            # Leaves its last node when the real aircraft did; the runway gate covers the roll.
            left = math.ceil((int(rows["UnixTime"].iloc[-1]) - t0) / DT)
            requests.append(AircraftRequest(f["id"], f["start"], f["goal"], ready, goal_hold=1, hold_until=left))
        else:
            requests.append(AircraftRequest(f["id"], f["start"], f["goal"], ready,
                                            goal_hold=DEPARTURE_HOLD_S // DT))
    return requests


def slot_order(airport, requests):
    """Departures by when they really left their line-up node, arrivals by ready time, interleaved."""
    return [r.id for r in sorted(requests, key=lambda r: (
        r.hold_until if r.hold_until is not None else r.ready_t, r.id))]


def plan(df, flights, realistic):
    """(airport, requests, BatchResult, conflict counts, settings) for one planning mode."""
    t0 = int(df["UnixTime"].min())
    requests = make_requests(flights, t0, realistic)
    if not realistic:
        ap = get_airport()
        result = plan_batch(ap, requests)
        conflicts = check_paths(result.paths, park_at_goal=False)
        return ap, requests, result, {"taxiway": len(conflicts)}, {"speed m/s": ap.taxi_speed}

    speed = measured_taxi_speed(flights)
    ap = get_airport(taxi_speed=round(speed, 2))
    step_m = ap.taxi_speed * DT
    node_buffer, follow_gap = math.ceil(NODE_CLEAR_M / step_m), math.ceil(FOLLOW_M / step_m)

    uses = real_runway_uses(df, t0)
    pair_of = {u.id: u.pair for u in uses if u.kind == "takeoff"}
    gate = build_gate(ap, uses)
    departs_from = {f["id"]: pair_of.get(f["id"]) for f in flights if f["kind"] == "departure"}
    # Arrivals that start their taxi still on the runway they landed on.
    rolling_out = {f["id"]: next((p for p, nodes in gate.on.items() if f["start"] in nodes), None)
                   for f in flights if f["kind"] == "arrival"}

    result = plan_batch(
        ap, requests, ReservationTable(node_buffer, follow_gap),
        edge_allowed_for=lambda r: gate.for_aircraft(r.id, departs_from.get(r.id), rolling_out.get(r.id)),
        order=slot_order, hold_starts=2,
    )
    conflicts = check_paths(result.paths, park_at_goal=False, node_buffer=node_buffer, follow_gap=follow_gap)
    incursions = runway_conflicts(gate, result.paths, departs_from)
    settings = {
        "speed m/s": ap.taxi_speed,
        "node buffer s": node_buffer * DT,
        "follow gap s": follow_gap * DT,
        "runway windows": len(uses),
    }
    return ap, requests, result, {"taxiway": len(conflicts), "runway": len({(a, p) for a, p, _ in incursions})}, settings


def summarize(per_aircraft, side):
    n = len(per_aircraft)
    return {
        "aircraft": n,
        "total taxi length (km)": sum(m[side]["length_m"] for m in per_aircraft) / 1000,
        "total taxi time (min)": sum(m[side]["time_s"] for m in per_aircraft) / 60,
        "avg wait per aircraft (s)": sum(m[side]["wait_s"] for m in per_aircraft) / n if n else 0,
    }


def print_table(title, real, mapf):
    print(f"\n{title}")
    print(f"  {'metric':<28}{'real':>12}{'MAPF':>12}{'change':>10}")
    for key in real:
        r, m = real[key], mapf[key]
        change = f"{(m - r) / r * 100:+.1f}%" if key != "aircraft" and r else ""
        fmt = "{:>12.0f}" if key == "aircraft" else "{:>12.1f}"
        print(f"  {key:<28}" + fmt.format(r) + fmt.format(m) + f"{change:>10}")


def plot_routes(ap, flights, result, out):
    fig, axes = plt.subplots(1, 2, figsize=(18, 9), sharex=True, sharey=True)
    for ax, title in zip(axes, ("Real (ADS-B)", "MAPF plan")):
        for u, nbrs in ap.graph.items():
            for v in nbrs:
                if u < v:
                    pts = np.array(edge_points(ap.nodes, ap.geometry, u, v))
                    ax.plot(pts[:, 1], pts[:, 0], color="0.85", linewidth=0.6, zorder=0)
        ax.set_title(title)
        ax.set_aspect(1 / math.cos(math.radians(ap.origin[0])))
    colors = {"departure": "tab:blue", "arrival": "tab:orange"}
    for f in flights:
        c = colors[f["kind"]]
        axes[0].plot(f["rows"]["Lon"], f["rows"]["Lat"], color=c, alpha=0.5, linewidth=0.8)
        path = result.paths.get(f["id"])
        if not path:
            continue
        nodes = [n for i, (n, _) in enumerate(path) if i == 0 or n != path[i - 1][0]]
        for u, v in zip(nodes, nodes[1:]):
            pts = np.array(edge_points(ap.nodes, ap.geometry, u, v))
            axes[1].plot(pts[:, 1], pts[:, 0], color=c, alpha=0.5, linewidth=0.8)
    for kind, c in colors.items():
        axes[0].plot([], [], color=c, label=kind)
    axes[0].legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def main(start_hour=194, n_hours=2, realistic=True):
    print(f"Loading KEWR hours {start_hour}..{start_hour + n_hours - 1} from {REPO}")
    df = load_hours(start_hour, n_hours)

    flights, skipped = collect(df, get_airport())
    n_dep = sum(1 for f in flights if f["kind"] == "departure")
    print(f"{len(flights)} routable aircraft ({n_dep} departures, {len(flights) - n_dep} arrivals)")
    for reason, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f"  skipped {n:>4}: {reason}")

    ap, requests, result, conflicts, settings = plan(df, flights, realistic)
    print(f"\nMode: {'realistic' if realistic else 'simple'}  "
          + ", ".join(f"{k} {v:g}" for k, v in settings.items()))
    print(f"MAPF: {len(result.paths)} planned, {len(result.failures)} failed, "
          + ", ".join(f"{n} {k} conflicts" for k, n in conflicts.items()))
    for aid, reason in result.failures.items():
        print(f"  failed {aid}: {reason}")

    by_id = {r.id: r for r in requests}
    per_aircraft = [
        {"id": f["id"], "kind": f["kind"], "real": real_metrics(f["rows"]),
         "mapf": mapf_metrics(ap, by_id[f["id"]], result.paths[f["id"]])}
        for f in flights if f["id"] in result.paths
    ]

    print_table("All aircraft", summarize(per_aircraft, "real"), summarize(per_aircraft, "mapf"))
    for kind in ("departure", "arrival"):
        subset = [m for m in per_aircraft if m["kind"] == kind]
        print_table(f"{kind.capitalize()}s", summarize(subset, "real"), summarize(subset, "mapf"))
    late = [m["mapf"]["late_s"] for m in per_aircraft if m["mapf"]["late_s"] > 0]
    if realistic:
        print(f"\n  departures reaching the runway after their real slot: {len(late)}"
              + (f" (avg {sum(late) / len(late):.0f} s late)" if late else ""))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_out = OUT_DIR / "real_vs_mapf_test.csv"
    pd.DataFrame([
        {"id": m["id"], "kind": m["kind"],
         **{f"{side}_{k}": round(v, 1) for side in ("real", "mapf") for k, v in m[side].items()}}
        for m in per_aircraft
    ]).to_csv(csv_out, index=False)
    png_out = OUT_DIR / "real_vs_mapf_test.png"
    plot_routes(ap, flights, result, png_out)
    print(f"\nSaved {csv_out}\nSaved {png_out}")
    return per_aircraft, result


if __name__ == "__main__":
    simple = "--simple" in sys.argv
    args = [int(a) for a in sys.argv[1:] if a.isdigit()][:2]
    main(*args, realistic=not simple)
