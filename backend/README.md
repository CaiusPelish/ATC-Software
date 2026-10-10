# Hold Short Backend

The backend is a FastAPI application managed with [uv](https://docs.astral.sh/uv/).

## Run locally

From this directory, install the locked dependencies and start the development
server:

```bash
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is available at <http://localhost:8000>, with interactive API
documentation at <http://localhost:8000/docs>.

The frontend development server runs separately from `frontend/` on port 5173
and connects to this API on port 8000.
