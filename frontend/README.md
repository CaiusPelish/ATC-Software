# Hold Short frontend

The frontend provides the airport ground-control simulator and opt-in
drowsiness monitor.

## Run locally

Install dependencies and start the development server:

```bash
npm install
npm run dev
```

The app runs at <http://localhost:5173>. The FastAPI backend runs separately;
see [the backend README](../backend/README.md). Set `VITE_BACKEND_URL` if the
backend is not available at `http://127.0.0.1:8000`.

## Main pages

- `/` — Home, with direct links to the simulator and drowsiness monitor.
- `/sim` — Airport ground simulation.
- `/drowsiness` — Webcam drowsiness monitoring.

## Drowsiness alert sounds

Start monitoring with sound enabled to allow the browser to play audio. When
the backend raises its drowsiness alarm, the browser plays a two-tone beep and
repeats it every five seconds while the alarm stays active. Use the **Sound
on/off** control to mute or restore the alert. Sounds are generated in the
browser and do not require an audio service or API key.

Build the production app with `npm run build`.
