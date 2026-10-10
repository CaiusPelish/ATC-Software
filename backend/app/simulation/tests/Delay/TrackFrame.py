# backend/app/simulation/tests/Delay/TrackFrame.py

"""Every aircraft position at KEWR over a 4-hour window (Amelia42-Mini on Hugging Face).

Builds one pandas DataFrame with a row per aircraft position (Type 0 only;
vehicles and unknown targets are dropped) within ELEVATION_TOL_FT of field
elevation, and tags every row with the first and last taxiway graph node its
track encounters:
- field elevation is what the data reports on the ground, FIELD_ELEVATION_FT
  (12.5 ft), not the charted 18 ft MSL; ADS-B puts nearly every surface
  position at exactly 12.5 ft, so 18 +/- 5 ft would drop almost all of them;
- a track "encounters" a node when one of those positions comes within
  NODE_SNAP_M of it, the same rule RealVsMapfTest uses to pick a taxi's
  start node;
- FirstNode is the node at the earliest such point, LastNode at the latest;
- FirstNodeTime / LastNodeTime are the UnixTime of those points;
- tracks that never come within NODE_SNAP_M of a node (e.g. parked deep in
  a gate area) get NaN for all four.

Writes data/kewr_<start>_<n>h_tracks.pkl next to this file (reload with
pd.read_pickle) and prints a summary.

Run:  python3 TrackFrame.py [start_hour] [n_hours]      (default 164 4: Thu 2024-06-20 16:35–20:35 EDT, evening rush)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND))

from app.routing.airport import get_airport  # noqa: E402
from app.simulation.tests.RealVsMapfTest import NODE_SNAP_M, NodeSnapper, load_hours  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "data"
# Altitude the data reports for aircraft on the ground (see docstring).
FIELD_ELEVATION_FT = 12.5
ELEVATION_TOL_FT = 5


def node_encounters(track, snapper):
    """(first node, its UnixTime, last node, its UnixTime) for one track, or None."""
    ids, dist = snapper.nearest(track["Lat"].to_numpy(), track["Lon"].to_numpy())
    on_node = np.flatnonzero(dist <= NODE_SNAP_M)
    if not len(on_node):
        return None
    t = track["UnixTime"].to_numpy()
    first, last = on_node[0], on_node[-1]
    return ids[first], t[first], ids[last], t[last]


def build_tracks(start_hour=164, n_hours=4):
    """DataFrame of every aircraft position near field elevation, with FirstNode/LastNode per track."""
    df = load_hours(start_hour, n_hours)
    near_field = (df["Altitude"] - FIELD_ELEVATION_FT).abs() <= ELEVATION_TOL_FT
    df = df[(df["Type"] == 0) & near_field].sort_values(["ID", "UnixTime"]).reset_index(drop=True)
    df.insert(1, "Timestamp", pd.to_datetime(df["UnixTime"], unit="s", utc=True))

    snapper = NodeSnapper(get_airport())
    ends = {}
    for fid, track in df.groupby("ID", sort=False):
        hit = node_encounters(track, snapper)
        if hit is not None:
            ends[fid] = hit
    ends = pd.DataFrame.from_dict(
        ends, orient="index", columns=["FirstNode", "FirstNodeTime", "LastNode", "LastNodeTime"]
    ).astype({"FirstNodeTime": "Int64", "LastNodeTime": "Int64"})
    return df.join(ends, on="ID")


def summarize(df):
    tracks = df.drop_duplicates("ID")
    tagged = tracks["FirstNode"].notna()
    print(f"{df['Timestamp'].min():%Y-%m-%d %H:%M} → {df['Timestamp'].max():%Y-%m-%d %H:%M UTC}")
    print(f"{len(df):,} aircraft positions within {ELEVATION_TOL_FT} ft of field elevation "
          f"({FIELD_ELEVATION_FT} ft), {len(tracks)} tracks")
    print(f"{tagged.sum()} tracks touch a graph node, {(~tagged).sum()} never do "
          f"(never within {NODE_SNAP_M} m of a node)")
    print()
    print(tracks.loc[tagged, ["ID", "FirstNode", "LastNode"]].head(10).to_string(index=False))


if __name__ == "__main__":
    start_hour = int(sys.argv[1]) if len(sys.argv) > 1 else 164
    n_hours = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    df = build_tracks(start_hour, n_hours)
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"kewr_{start_hour}_{n_hours}h_tracks.pkl"
    df.to_pickle(out)
    summarize(df)
    print(f"\nSaved {out}")
