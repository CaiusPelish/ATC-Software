import pandas as pd
from huggingface_hub import hf_hub_download, HfApi
from datetime import datetime, timezone
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


def plot_ewr(df, out="ewr.png", max_alt_ft=100):
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
    ax.set_title(f"KEWR — {aircraft['ID'].nunique()} aircraft, 1 hour")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved {out}")


if __name__ == "__main__":
    df = print_ewr()
    plot_ewr(df)

