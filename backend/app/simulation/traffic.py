# backend/app/simulation/traffic.py

"""Real KEWR traffic from the Amelia recording (kewr_194_2h.csv) as MAPF input.

The CSV has one row per target per second for two hours. Type 0 is an
aircraft; 1 and 2 are ground vehicles or unknown targets. A flight counts as a
departure we can route if the recording holds its whole taxi: on the ground
when first seen, then a takeoff roll before the recording ends.
"""

import math

import numpy as np
import pandas as pd

from ..routing.airport import DT
from ..routing.osm_taxiways import DATA_DIR, haversine_m, load_saved_graph, snap_to_nearest_edge

TRAFFIC_CSV = DATA_DIR.parent / "kewr_194_2h.csv"

# Above this speed (kt) the plane has started its takeoff roll, so it is no longer taxiing.
TAKEOFF_ROLL_SPEED = 30
# At or below this speed the plane is stopped (e.g. lined up waiting for takeoff clearance).
STOPPED_SPEED = 1
# GPS points further than this from any taxiway segment are skipped; A* fills the gap.
SNAP_DISTANCE_M = 15
# Amelia reports 12.5 ft for aircraft on the ground at KEWR.
GROUND_ALTITUDE_FT = 20
# Less taxi than this isn't a route worth testing (e.g. first seen already lined up).
MIN_TAXI_SECONDS = 60
# Start and end points further than this from a taxiway (e.g. at a gate) can't be routed.
MAX_SNAP_M = 50
# How long a departure holds its line-up node before it's gone: line-up plus takeoff roll.
DEPARTURE_HOLD_S = 60
# KEWR magnetic variation (west), to turn the takeoff's true heading into a runway number.
MAGNETIC_VARIATION_DEG = 13


class NotEnoughHistory(Exception):
    """The recording doesn't hold this flight's whole taxi to takeoff."""


def read_traffic(csv_path=TRAFFIC_CSV):
    return pd.read_csv(csv_path)


def taxi_rows(flight):
    """Rows of one departing flight from first appearance until it lines up.

    By the time speed passes TAKEOFF_ROLL_SPEED the plane is already well down
    the runway, so the taxi ends at the last stop before that.
    """
    flight = flight.sort_values("Frame").reset_index(drop=True)
    rolling = flight.index[flight["Speed"] > TAKEOFF_ROLL_SPEED]
    end = rolling[0] if len(rolling) else len(flight)
    stopped = flight.index[(flight.index < end) & (flight["Speed"] <= STOPPED_SPEED)]
    if len(rolling) and len(stopped):
        end = stopped[-1] + 1
    return flight.loc[: end - 1]


def taxi_points(flight):
    """GPS points of one flight from first appearance until it lines up, as an (n, 2) [lat, lon] array."""
    return taxi_rows(flight)[["Lat", "Lon"]].to_numpy()


def arrival_taxi_rows(flight):
    """Rows of one arriving flight from the end of its landing roll to its last ground point.

    Raises NotEnoughHistory if the flight isn't an arrival with a taxi in.
    """
    flight = flight.sort_values("Frame")
    if flight.empty or (flight["Type"] != 0).all():
        raise NotEnoughHistory("is not an aircraft")
    if flight.iloc[0]["Altitude"] <= GROUND_ALTITUDE_FT:
        raise NotEnoughHistory("is on the ground when first seen, so it isn't an arrival")
    ground = flight[flight["Altitude"] <= GROUND_ALTITUDE_FT]
    rolling = ground.index[ground["Speed"] > TAKEOFF_ROLL_SPEED]
    if not len(rolling):
        raise NotEnoughHistory("never lands in the recording")
    taxi = ground.loc[rolling[-1]:].iloc[1:]
    if len(taxi) < MIN_TAXI_SECONDS:
        raise NotEnoughHistory(f"has only {len(taxi)} s of taxi after landing (need {MIN_TAXI_SECONDS} s)")
    return taxi


def segment_metrics(rows):
    """(meters per step, seconds per step, stopped per step) along a real taxi."""
    lat, lon = rows["Lat"].to_numpy(), rows["Lon"].to_numpy()
    dist = np.array([haversine_m(lat[i], lon[i], lat[i + 1], lon[i + 1]) for i in range(len(rows) - 1)])
    return dist, np.diff(rows["UnixTime"].to_numpy()), rows["Speed"].to_numpy()[:-1] <= STOPPED_SPEED


def measured_taxi_speed(flights):
    """Average real speed while moving, in m/s, over every flight's taxi."""
    meters = seconds = 0.0
    for f in flights:
        dist, dt, stopped = segment_metrics(f["rows"])
        meters += dist[~stopped].sum()
        seconds += dt[~stopped].sum()
    return meters / seconds


def runway_number(flight):
    """Runway number from the heading at the start of the takeoff roll, e.g. '04'."""
    roll = flight[flight["Speed"] > TAKEOFF_ROLL_SPEED].head(10)
    rad = roll["Heading"].map(math.radians)
    true_heading = math.degrees(math.atan2(rad.map(math.sin).mean(), rad.map(math.cos).mean()))
    magnetic = (true_heading + MAGNETIC_VARIATION_DEG) % 360
    number = round(magnetic / 10) % 36 or 36
    return f"{number:02d}"


def snap_to_graph_node(nodes, graph, geometry, lat, lon):
    """Nearest end of the nearest taxiway edge, and how far the point is from it."""
    hit = snap_to_nearest_edge(nodes, graph, lat, lon, MAX_SNAP_M, geometry)
    if hit is None:
        return None
    a, b, _, t = hit
    node = a if t < 0.5 else b
    return node, haversine_m(lat, lon, node.lat, node.lon)


def qualify_departure(flight, recording_start, nodes, graph, geometry):
    """Check one flight's rows hold a whole taxi to takeoff. Raises NotEnoughHistory.

    Returns the taxi GPS points, the snapped start and line-up nodes (with
    snap distances), the takeoff runway and an optional note.
    """
    flight = flight.sort_values("Frame")
    if flight.empty:
        raise NotEnoughHistory("is not in the recording")
    if (flight["Type"] != 0).all():
        raise NotEnoughHistory("is a ground vehicle or unknown target, not an aircraft")

    first = flight.iloc[0]
    if first["Altitude"] > GROUND_ALTITUDE_FT:
        raise NotEnoughHistory(
            f"is already in the air ({first['Altitude']:.0f} ft) when first seen, so it's an "
            "arrival or overflight; only departures are supported"
        )
    if first["Speed"] > TAKEOFF_ROLL_SPEED:
        raise NotEnoughHistory(f"is already on its takeoff roll ({first['Speed']:.0f} kt) when first seen")
    if not (flight["Speed"] > TAKEOFF_ROLL_SPEED).any():
        raise NotEnoughHistory(
            "never starts a takeoff roll before the recording ends, so its taxi has no known end "
            "(arrivals and planes still taxiing aren't supported)"
        )

    taxi = taxi_points(flight)
    if len(taxi) < MIN_TAXI_SECONDS:
        raise NotEnoughHistory(f"has only {len(taxi)} s of taxi before takeoff (need {MIN_TAXI_SECONDS} s)")

    start = snap_to_graph_node(nodes, graph, geometry, *taxi[0])
    goal = snap_to_graph_node(nodes, graph, geometry, *taxi[-1])
    if start is None:
        raise NotEnoughHistory(f"starts more than {MAX_SNAP_M} m from any taxiway (e.g. at a gate)")
    if goal is None:
        raise NotEnoughHistory(f"lines up more than {MAX_SNAP_M} m from any taxiway")
    if start[0].id == goal[0].id:
        raise NotEnoughHistory("starts and lines up at the same taxiway node; there's no route to find")

    note = None
    if first["UnixTime"] == recording_start:
        note = "already on the ground when the recording starts, so its taxi may have begun earlier"

    return {
        "taxi": taxi,
        "start": start[0],
        "start_dist": start[1],
        "goal": goal[0],
        "goal_dist": goal[1],
        "runway": runway_number(flight),
        "first_unix": int(first["UnixTime"]),
        "note": note,
    }


def load_departures(df=None, t0=None):
    """Every routable departure in the recording, as [(AircraftRequest, actual_taxi_seconds)].

    ready_t is the timestep (from t0, default the recording start) the
    aircraft first appears; the planner has no aircraft types, so all are 'large'.
    """
    from ..routing.mapf import AircraftRequest

    df = read_traffic() if df is None else df
    recording_start = int(df["UnixTime"].min())
    t0 = recording_start if t0 is None else t0
    nodes, graph, geometry = load_saved_graph("KEWR", simple=True)

    departures = []
    for flight_id, flight in df.groupby("ID"):
        try:
            info = qualify_departure(flight, recording_start, nodes, graph, geometry)
        except NotEnoughHistory:
            continue
        request = AircraftRequest(
            id=str(flight_id),
            start=info["start"].id,
            goal=info["goal"].id,
            ready_t=(info["first_unix"] - t0) // DT,
            goal_hold=DEPARTURE_HOLD_S // DT,
        )
        # One row per second, so the taxi's row count is its duration.
        departures.append((request, len(info["taxi"])))
    departures.sort(key=lambda d: (d[0].ready_t, d[0].id))
    return departures


def vehicles_snapshot(unix_time, df=None):
    """{target id: node id} for ground vehicles (Type 1/2) near a taxiway at unix_time."""
    df = read_traffic() if df is None else df
    nodes, graph, geometry = load_saved_graph("KEWR", simple=True)
    rows = df[(df["UnixTime"] == unix_time) & (df["Type"] != 0)]
    snapshot = {}
    for row in rows.itertuples():
        hit = snap_to_graph_node(nodes, graph, geometry, row.Lat, row.Lon)
        if hit:
            snapshot[str(row.ID)] = hit[0].id
    return snapshot
