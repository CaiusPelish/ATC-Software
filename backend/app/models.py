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


class CollisionWarning(BaseModel):
    """A conflict that hasn't happened yet: where things are headed brings them together."""
    aircraft_ids: list[str]
    location: str
    eta: float  # seconds until it happens


class SimulationState(BaseModel):
    time: float = 0.0
    running: bool = False
    aircraft: list[Aircraft] = Field(default_factory=list)
    vehicles: list[Vehicle] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    warnings: list[CollisionWarning] = Field(default_factory=list)
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