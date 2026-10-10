import pandas as pd
from huggingface_hub import hf_hub_download, HfApi
from datetime import datetime, timezone
from pathlib import Path
import matplotlib.pyplot as plt

REPO = "AmeliaCMU/Amelia42-Mini"


def list_kewr_hours():
    """Map hour index (from filename KEWR_0_<hour_idx>_<unix_ts>.csv) -> repo path."""
    files = HfApi().list_repo_files(REPO, repo_type="dataset")
    return {
        int(f.split("/")[-1].split("_")[2]): f
        for f in files
        if "/kewr/" in f and f.endswith(".csv")
    }


def print_ewr(hour_index=194, rows=20):
    """Download one hour of KEWR surface data and print it."""
    hours = list_kewr_hours()
    if hour_index not in hours:
        raise ValueError(f"No KEWR file for hour {hour_index}; available {min(hours)}–{max(hours)}")

    target = hours[hour_index]
    ts = int(target.split("_")[-1].replace(".csv", ""))
    print(f"File: {target.split('/')[-1]}")
    print(f"Hour: {datetime.fromtimestamp(ts, tz=timezone.utc):%Y-%m-%d %H:%M UTC}\n")

    path = hf_hub_download(REPO, target, repo_type="dataset")
    df = pd.read_csv(path)

    print(f"Shape: {df.shape[0]:,} rows × {df.shape[1]} cols")
    print(f"Unique agents: {df['ID'].nunique()}")
    print(f"Type breakdown: {dict(df['Type'].value_counts())}  (0=aircraft, 1=vehicle, 2=unknown)\n")
    print(df.head(rows).to_string())

    return df


def export_ewr(start_hour=194, n_hours=2, out=None):
    """Join n consecutive hours of KEWR data into one CSV.

    Each hourly file numbers its frames 0–3600 (1 s apart), so Frame is
    renumbered to run continuously and a UTC Timestamp column is added.
    Frame 3600 of one hour is the same second as frame 0 of the next, so
    duplicate (Timestamp, ID) rows are dropped.
    """
    hours = list_kewr_hours()
    wanted = list(range(start_hour, start_hour + n_hours))
    missing = [h for h in wanted if h not in hours]
    if missing:
        raise ValueError(f"No KEWR file for hours {missing}")

    starts = [int(hours[h].split("_")[-1].replace(".csv", "")) for h in wanted]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    if any(g != 3600 for g in gaps):
        raise ValueError(f"Hours {wanted} are not consecutive (gaps {gaps} s)")

    frames = []
    for h, ts in zip(wanted, starts):
        df = pd.read_csv(hf_hub_download(REPO, hours[h], repo_type="dataset"))
        df["UnixTime"] = ts + df["Frame"]
        frames.append(df)

    df = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["UnixTime", "ID"])
        .sort_values(["UnixTime", "ID"])
        .reset_index(drop=True)
    )
    df["Frame"] = df["UnixTime"] - starts[0]
    df.insert(1, "Timestamp", pd.to_datetime(df["UnixTime"], unit="s", utc=True))

    if out is None:
        out = Path(__file__).resolve().parent / "data" / f"kewr_{start_hour}_{n_hours}h.csv"
    df.to_csv(out, index=False)

    print(f"Saved {out}")
    print(f"{df['Timestamp'].min():%Y-%m-%d %H:%M} → {df['Timestamp'].max():%H:%M UTC}, "
          f"{len(df):,} rows, {df['ID'].nunique()} agents "
          f"({df.loc[df['Type'] == 0, 'ID'].nunique()} aircraft)")
    return df


def plot_ewr(df, out="ewr.png", max_alt_ft=100, title_hours=1):
    """Plot aircraft tracks in local km. Amelia's x is north and y is east, so
    y goes on the horizontal axis to get a north-up map. Points above max_alt_ft are dropped
    so approach/climb-out segments don't swamp the taxiways; pass None to keep them."""
    aircraft = df[df["Type"] == 0]
    if max_alt_ft is not None:
        aircraft = aircraft[aircraft["Altitude"] <= max_alt_ft]

    fig, ax = plt.subplots(figsize=(12, 12))
    for _, group in aircraft.groupby("ID"):
        g = group.sort_values("Frame")
        ax.plot(g["y"], g["x"], alpha=0.35, linewidth=0.7)
    ax.set_aspect("equal")
    ax.set_xlabel("east (km)")
    ax.set_ylabel("north (km)")
    ax.set_title(f"KEWR — {aircraft['ID'].nunique()} aircraft, {title_hours} hour{'s' if title_hours != 1 else ''}")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved {out}")


if __name__ == "__main__":
    df = export_ewr(194, 2)
    plot_ewr(df, title_hours=2)

