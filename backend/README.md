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

## Drowsiness monitor

The `/drowsiness` frontend route requests webcam access only when the user
starts monitoring. JPEG frames are sent to `POST /api/drowsiness/frame`, where
OpenCV detects a face and eyes and the Hugging Face image-classification model
`dima806/closed_eyes_image_detection` estimates eye closure. Frames are
processed in memory and are not stored. Per-session PERCLOS and continuous
closure measurements expire after 15 minutes without activity.

The first frame with a detected face downloads the model from Hugging Face. Set
`DROWSINESS_MODEL_ID` to use a different compatible model, or
`VITE_BACKEND_URL` in the frontend environment to change the API URL.
Monitoring requires camera permission and a secure browser context (HTTPS or
localhost). This experimental feature is an aid, not a medical or
safety-critical system.
