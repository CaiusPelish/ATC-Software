# Backend To-Do

What the backend still needs for the demo, in priority order. Work on the `backend` branch; Raul handles merging into `main`.

## 1. Traffic visible right away
On a fresh start the map stays empty for about 45 s, because arrivals only enter the state at touchdown. Fix one of these ways:
- start `default` with 1–2 departures already waiting or taxiing, or
- include arrivals on final in the state (for example `status: "landing"` with `position` = the runway end).

## 2. Vehicle endpoints (crash challenge)
The judge drags a fire truck across a runway. `LiveSim.add_vehicle` exists, but nothing exposes it:

| Method | Path | Body | Effect |
|---|---|---|---|
| `POST` | `/api/vehicles` | `{"id": "TRUCK01", "position": "<node>"}` | Spawn a truck |
| `POST` | `/api/vehicles/{id}/route` | `{"destination": "<node>"}` | Drive there by the shortest path. Runway crossings are allowed |
| `POST` | `/api/vehicles/{id}/hold` | `{"hold": true \| false}` | Stop or continue |

## 3. Alerts with text and audio
Add `alerts` to `SimulationState` (or `GET /api/alerts`), one per active conflict:

| Field | Example |
|---|---|
| `id` | `"ALT-12"` |
| `severity` | from `calculate_alert_severity(conflict.severity, fatigue_score)` |
| `text` | `"Fire truck is crossing runway 4L/22R while UAL334 is landing."` |
| `atc_phrase` | `"TRUCK01, hold short runway 4L. UAL334, go around."` |
| `audio_url` | ElevenLabs clip. Pre-generate the common ones; the key stays on the server |
| `vehicle_ids` | `["TRUCK01", "AC-…"]` |
| `created_s` | sim time |

## 4. Fatigue score → alert severity
The frontend measures fatigue in the browser and will POST it every second:

| Method | Path | Body |
|---|---|---|
| `POST` | `/api/fatigue` | `{"score": 0-100, "perclos": 0-1, "alarm": bool}` |

Keep the latest value and pass `score / 100` as `fatigue_score` into `calculate_alert_severity`.

## 5. Time to impact on runway conflicts
For runway-incursion conflicts, add `seconds_to_conflict` to `Conflict`: how long until the landing or departing aircraft reaches the intruder. The crash-challenge result screen shows "Crash prevented X seconds before touchdown".

## 6. Speech to route (lowest priority)
`speech/parser.py` is a stub. Add `POST /api/speech` (audio upload) → transcribe → `parse_clearance` → route the aircraft. Support one pattern: callsign, "taxi to runway X via", taxiway list, optional "hold short".

---

## Don't change (the frontend depends on these)

| Field | Meaning |
|---|---|
| node IDs | Must be `KEWR_ground.json` IDs. If they change, tell frontend; the map is built from that file |
| `route` | Node IDs start → goal, including the start |
| `progress` | Fraction of the **whole** route, 0–1 |
| `position` | Last node passed |
| `status` | `waiting` \| `taxiing` \| `holding` \| `arrived` (add `landing` if you do #1) |
| `vehicles[]` | Same `id` / `position` / `route` / `progress` as aircraft |
| `conflicts[]` | `aircraft_ids` may include vehicle IDs; runway incursions use `critical` + the runway pair (e.g. `"4L/22R"`) |

## How to test
The backend now uses **uv** (`pyproject.toml` + `uv.lock`); `requirements.txt` is gone. Install uv once (no admin needed):
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```
Then, from `backend/`:
```powershell
uv sync --group analysis
uv run --with pytest pytest app/routing/tests app/simulation/tests/LiveTest.py tests -q
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```
- **Add a package:** `uv add <name>` for anything the API needs at runtime, or `uv add --group analysis <name>` for offline scripts and plots.
- **Commit** `pyproject.toml` and `uv.lock` together.
Then run the frontend (`cd frontend`, `npm install`, `npm run dev`) and open <http://127.0.0.1:5173/sim>. The top-left badge reads **LIVE** when the state is drawable.
