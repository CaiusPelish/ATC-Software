from collections import deque
from dataclasses import dataclass


CLOSED_SECONDS_ALARM = 0.8
PERCLOS_WINDOW_SECONDS = 15.0
PERCLOS_THRESHOLD = 0.20
MIN_WINDOW_FILL_SECONDS = 4.0
CLOSED_PROBABILITY_THRESHOLD = 0.35


@dataclass(frozen=True)
class DrowsinessResult:
    status: str
    face_detected: bool
    eyes_closed: bool
    closed_probability: float
    closed_duration: float
    perclos: float
    alarm: bool


class DrowsinessMonitor:
    def __init__(self) -> None:
        self._history: deque[tuple[float, bool]] = deque()
        self._closed_since: float | None = None

    def update(
        self,
        timestamp: float,
        closed_probability: float | None,
    ) -> DrowsinessResult:
        if closed_probability is None:
            self._history.clear()
            self._closed_since = None
            return DrowsinessResult(
                status="no_face",
                face_detected=False,
                eyes_closed=False,
                closed_probability=0.0,
                closed_duration=0.0,
                perclos=0.0,
                alarm=False,
            )

        eyes_closed = closed_probability >= CLOSED_PROBABILITY_THRESHOLD
        self._history.append((timestamp, eyes_closed))

        if eyes_closed:
            if self._closed_since is None:
                self._closed_since = timestamp
        else:
            self._closed_since = None

        cutoff = timestamp - PERCLOS_WINDOW_SECONDS
        while len(self._history) > 1 and self._history[1][0] <= cutoff:
            self._history.popleft()

        window_span = min(PERCLOS_WINDOW_SECONDS, timestamp - self._history[0][0])
        closed_seconds = 0.0
        samples = list(self._history)
        for index, (sample_time, sample_closed) in enumerate(samples):
            next_time = (
                samples[index + 1][0]
                if index + 1 < len(samples)
                else timestamp
            )
            interval = max(0.0, min(timestamp, next_time) - max(cutoff, sample_time))
            if sample_closed:
                closed_seconds += interval

        perclos = closed_seconds / window_span if window_span > 0 else 0.0
        closed_duration = (
            timestamp - self._closed_since if self._closed_since is not None else 0.0
        )
        alarm = closed_duration >= CLOSED_SECONDS_ALARM or (
            window_span >= MIN_WINDOW_FILL_SECONDS
            and perclos >= PERCLOS_THRESHOLD
        )

        if alarm:
            status = "drowsy"
        elif eyes_closed:
            status = "eyes_closed"
        else:
            status = "alert"

        return DrowsinessResult(
            status=status,
            face_detected=True,
            eyes_closed=eyes_closed,
            closed_probability=closed_probability,
            closed_duration=closed_duration,
            perclos=perclos,
            alarm=alarm,
        )
