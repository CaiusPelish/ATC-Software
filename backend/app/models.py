from pydantic import BaseModel, Field
from typing import Literal

class Aircraft(BaseModel):
    id: str
    callsign: str
    position: str
    destination: str
    aircraft_type: str = "A320"
    route: list[str] = Field(default_factory=list)
    speed: float = 0.0
    progress: float = 0.0
    status: Literal[
        "waiting", "taxiing", "holding", "arrived"
    ] = "waiting"


class ScheduledSpawn(BaseModel):
    spawn_time: float
    callsign: str
    aircraft_type: str = "A320"
    position: str
    destination: str
    spawned: bool = False


class Vehicle(BaseModel):
    id: str
    position: str
    route: list[str] = Field(default_factory=list)
    speed: float = 0.0
    progress: float = 0.0
    status: Literal["waiting", "taxiing", "holding"] = "waiting"


class Conflict(BaseModel):
    aircraft_ids: list[str]
    location: str
    severity: Literal["warning", "critical"]
    # Runway incursions: seconds until the aircraft using the runway gets there
    # (0 once it is on the runway). None for separation conflicts, which are now.
    seconds_to_conflict: float | None = None


class CollisionWarning(BaseModel):
    """A conflict that hasn't happened yet: where things are headed brings them together."""
    aircraft_ids: list[str]
    location: str
    eta: float  # seconds until it happens


class Alert(BaseModel):
    """What the controller is told about a conflict or a predicted one."""
    id: str  # stable while the same pair is in conflict at the same place
    kind: Literal["runway", "separation", "predicted"]
    # The conflict's severity raised by controller fatigue (alerts.manager).
    severity: Literal["low", "medium", "high", "critical"]
    text: str  # plain English
    atc_phrase: str  # the instruction a controller would give
    vehicle_ids: list[str]
    location: str
    seconds: float | None = None  # until it happens; None when happening now
    fatigue: float = 0.0  # controller fatigue (0-100) the severity was based on
    created_s: float  # sim time the alert first appeared
    audio_url: str | None = None  # spoken by the frontend's ElevenLabs route for now


class FatigueReport(BaseModel):
    """Sent by the browser's drowsiness monitor (MediaPipe) about once a second."""
    score: float = Field(ge=0, le=100)
    perclos: float = Field(default=0.0, ge=0, le=1)
    alarm: bool = False


class SimulationState(BaseModel):
    time: float = 0.0
    running: bool = False
    aircraft: list[Aircraft] = Field(default_factory=list)
    vehicles: list[Vehicle] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    warnings: list[CollisionWarning] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    scheduled_spawns: list["ScheduledSpawn"] = Field(
        default_factory=list
    )
    score: int = 1000
    # pending_spawns: list[ScheduledSpawn] = Field(
    #     default_factory=list
    # )
    

class SpawnAircraftRequest(BaseModel):
    callsign: str
    aircraft_type: str = "A320"
    weight_class: str = "large"
    position: str
    destination: str


class RouteRequest(BaseModel):
    destination: str


class HoldRequest(BaseModel):
    hold: bool


class SpawnVehicleRequest(BaseModel):
    id: str
    position: str


class SpawnAircraftResponse(BaseModel):
    aircraft: Aircraft
    message: str