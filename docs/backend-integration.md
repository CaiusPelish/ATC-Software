# Backend → Frontend Hookup

What the backend needs to change so `/sim` shows live traffic instead of demo traffic.

## Where things stand

- **Frontend:** `/sim` polls `GET /api/state` every 500 ms and draws planes and trucks on the real EWR map. The map is built from `KEWR_ground.json`: all 431 nodes, including runways and crossings. Vehicles follow each edge's `geometry` curve.
- **Why it shows demo traffic:** the live API still uses the 7-node test graph (`GATE_A1`, `T1`, `RWY_4`), and nothing moves (`time` stays `0.0`, `running: false`).
- **Planner:** Luftalagi's planner works standalone (arrivals sim: 6/6 to the ramp, 0 conflicts, 0 incursions; routing tests pass). It just isn't wired into the API yet.

## What we need, in priority order

### 1. Use the real graph in the API
In `routing/graph.py`:

```python
airport_graph = json.load(open(".../KEWR_ground.json"))["graph"]
```

It's the same `{node: {neighbor: cost}}` shape, so A*, `/api/route` and the spawner pick it up automatically.

### 2. Real node IDs in state
In `simulation/state.py` and `simulation/scenarios.py`, replace `GATE_A1`, `T1` and `RWY_4` with `KEWR_ground` IDs, e.g. `"rwy_4L"`, or ramp ends like `"45452380"`.
The frontend switches to **Live** as soon as every `position` is a `KEWR_ground` ID.

### 3. Tick loop
On startup, run an asyncio task that calls `simulation_tick(state, 0.25)` every 250 ms and sets `running = True`.

### 4. Endpoints so the controller can act

| Method | Path | Body | Effect |
|---|---|---|---|
| `POST` | `/api/aircraft/{id}/route` | `{"destination": "<node id>"}` | Plan it (MAPF / space-time A*), set `route`, set `status = "taxiing"` |
| `POST` | `/api/aircraft/{id}/hold` | `{"hold": true \| false}` | `status = "holding"`, or back to `"taxiing"` |

### 5. Play the arrivals sim into state
For example, `POST /api/scenario/load {"scenario_id": "arrivals"}` runs `arrivals.simulate()`, and the tick loop advances each plane along its plan.

## Field contract the frontend relies on

Please keep these meanings:

| Field | Meaning |
|---|---|
| `route` | Full list of node IDs, start → goal, **including the start node** |
| `progress` | Fraction of the **whole route's** length, `0` to `1` (not per edge) |
| `position` | Last node passed |
| `status` | `"waiting"` \| `"taxiing"` \| `"holding"` \| `"arrived"` |
| `vehicles[]` | Trucks. Same `id` / `position` / `route` / `progress` fields |
| `conflicts[].aircraft_ids` | Can include vehicle IDs |
| `conflicts[].severity` / `location` | For runway incursions use `"critical"` and the runway ref (e.g. `"4L/22R"`). The crash challenge depends on this |

## Small stuff

- `requirements.txt` is saved as UTF-16, so `pip install -r` may fail. Re-save it as UTF-8, and add `matplotlib` and `pandas` (the arrivals GIF and `test_replay` need them).
- Run on `127.0.0.1:8000`. The frontend proxies `/api` through Vite, so no CORS changes are needed.
- If `KEWR_ground.json` node IDs change, tell the frontend team. The map has to be rebuilt from it with `frontend/scripts/build_chart.py`.

## How to test

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173/sim>. The badge in the top-left reads:

- `Demo traffic · backend offline`: backend not running on port 8000
- `Demo traffic · backend IDs not on map`: steps 1–2 not done yet
- `Live`: working
