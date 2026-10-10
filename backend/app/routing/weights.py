# backend/app/routing/weights.py

import json
from pathlib import Path


DATA_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "taxiway_costs.json"
)


def load_weights():
    with open(DATA_PATH, "r", encoding="utf-8") as file:
        return json.load(file)