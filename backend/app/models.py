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


class Vehicle(BaseModel):
    id: str
    position: str
    route: list[str] = Field(default_factory=list)
    speed: float = 0.0
    progress: float = 0.0


class Conflict(BaseModel):
    aircraft_ids: list[str]
    location: str
    severity: Literal["warning", "critical"]


class SimulationState(BaseModel):
    time: float = 0.0
    running: bool = False
    aircraft: list[Aircraft] = Field(default_factory=list)
    vehicles: list[Vehicle] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    score: int = 1000

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


class SpawnAircraftResponse(BaseModel):
    aircraft: Aircraft
    message: str