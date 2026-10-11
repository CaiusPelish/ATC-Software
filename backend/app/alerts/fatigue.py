# backend/app/alerts/fatigue.py

"""The controller's latest fatigue score, reported by the browser (MediaPipe drowsiness
monitor) through POST /api/fatigue. It lives outside the simulation so loading a
scenario doesn't reset it, and goes stale if the monitor stops reporting."""

import time

STALE_AFTER_S = 15

_latest = {"score": 0.0, "perclos": 0.0, "alarm": False, "at": None}


def report(score: float, perclos: float = 0.0, alarm: bool = False, now: float | None = None):
    _latest.update(score=float(score), perclos=float(perclos), alarm=bool(alarm),
                   at=time.monotonic() if now is None else now)
    return snapshot(now)


def snapshot(now: float | None = None):
    """The latest report, or a zero score once it's older than STALE_AFTER_S."""
    now = time.monotonic() if now is None else now
    fresh = _latest["at"] is not None and now - _latest["at"] <= STALE_AFTER_S
    return {
        "score": _latest["score"] if fresh else 0.0,
        "perclos": _latest["perclos"] if fresh else 0.0,
        "alarm": _latest["alarm"] if fresh else False,
        "fresh": fresh,
    }


def current_score() -> float:
    return snapshot()["score"]
