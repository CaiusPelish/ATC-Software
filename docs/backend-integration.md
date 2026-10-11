# Backend To-Do

What's done and what's left for the demo. Work on the `backend` branch; Raul handles merging into `main`.

## Done ✅
- **Traffic at start:** `default` begins with two departures taxiing.
- **Vehicle endpoints:** `POST /api/vehicles`, `/api/vehicles/{id}/route`, `/api/vehicles/{id}/hold`.
- **Predicted collisions:** `state.warnings`, 30 s lookahead.
- **Alerts:** `state.alerts`, one per conflict or predicted collision. Fields: `id`, `kind`, `severity`, `text`, `atc_phrase`, `vehicle_ids`, `location`, `seconds`, `fatigue`, `created_s`. `audio_url` stays empty: the frontend speaks alerts through its own ElevenLabs route (`/alerts/voice`), which keeps the key on the frontend server.
- **Fatigue:** `POST /api/fatigue {score, perclos, alarm}` (the MediaPipe monitor sends it every second) and `GET /api/fatigue`. A score of 70 or more raises warnings to `high`; the frontend then says "Urgent." and plays louder. Reports go stale after 15 s.
- **Time to conflict:** `Conflict.seconds_to_conflict` on runway incursions, i.e. seconds until the runway's user reaches the runway (0 once it's on it).

Tests: `tests/test_alerts.py`.

## Still open: speech to route (lowest priority)
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
