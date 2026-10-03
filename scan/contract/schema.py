"""The published output contract (Pydantic models; the JSON Schema is generated from these).

Every number a client might use is a ``Measure`` with a 90 % interval. Surfaces have stable
ids (``room_2.wall_3``, ``room_2.floor``, ``room_2.ceiling``) that damage, flags and scope
items refer to. A quantity that could not be measured is ``null`` with a reason.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Measure(Strict):
    value: float
    lo: float = Field(description="lower end of the interval")
    hi: float | None = Field(description="upper end; null for a lower bound (e.g. a door head never seen)")
    sigma: float = Field(ge=0, description="one-sigma uncertainty used to build the interval")
    unit: str
    confidence: float = 0.9
    bound: Literal["two_sided", "lower"] = "two_sided"
    calibrated: bool = False


class QcIssue(Strict):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    fix: str | None = None


class CaptureInfo(Strict):
    id: str
    tier: Literal["lidar", "video", "photo"]
    source_format: str
    device: str | None
    device_supported: bool | None
    duration_s: float | None
    frames_raw: int
    keyframes_used: int
    quality_score: float = Field(ge=0, le=1)
    issues: list[QcIssue]
    warnings: list[str]


class ModelUsed(Strict):
    key: str
    repo: str
    licence: str
    purpose: str


class DriftInfo(Strict):
    enabled: bool
    loops_accepted: int
    loops_tested: int
    max_translation_m: float
    max_yaw_deg: float
    note: str | None


class Processing(Strict):
    pipeline_version: str
    run_utc: str
    runtime_s: float
    offline: bool = True
    interval_method: str
    calibrated: bool
    drift_correction: DriftInfo
    models: list[ModelUsed]


class Wall(Strict):
    id: str = Field(description="surface id, e.g. room_2.wall_3")
    index: int
    length: Measure
    height: Measure | None
    observed: bool = Field(description="false: no wall was seen here; the edge is inferred")
    coverage: float = Field(ge=0, le=1, description="share of the wall actually observed")
    plane_ids: list[int]


class Room(Strict):
    id: int
    name: str
    kind: Literal["room", "corridor"]
    polygon_xz: list[tuple[float, float]] = Field(description="corners in metres, world x/z, in order")
    floor_area: Measure
    perimeter: Measure
    ceiling_height: Measure | None
    ceiling_note: str | None
    floor_tilt_deg: float | None
    walls: list[Wall]
    floor_id: str
    ceiling_id: str
    opening_ids: list[int]


class Opening(Strict):
    id: int
    type: Literal["door", "window", "opening"]
    rooms: list[int]
    wall_ids: list[str]
    width: Measure
    height: Measure
    sill: Measure | None
    center_xz: tuple[float, float]
    views: int
    covered: bool = Field(description="seen only via labels (closed door, curtain)")
    low_evidence: bool


class Adjacency(Strict):
    room_a: int
    room_b: int
    via: Literal["door", "opening", "window", "doorway", "open boundary"]
    opening_id: int | None


class SharedWall(Strict):
    room_a: int
    wall_a: str
    room_b: int
    wall_b: str
    thickness: Measure


class PropertyPlan(Strict):
    rooms_count: int
    net_floor_area: Measure
    footprint_area: Measure
    connected: bool
    adjacency: list[Adjacency]
    shared_walls: list[SharedWall]
    overlaps: list[tuple[int, int, float]] = Field(description="(room a, room b, m2); empty when rooms do not overlap")
    unmeasured_doorways: int


class Damage(Strict):
    id: int
    surface_id: str
    room_id: int
    cls: Literal["water_stain", "crack", "mold"] = Field(alias="class", serialization_alias="class")
    area: Measure
    length: Measure
    extent_m: tuple[float, float]
    bottom_above_floor_m: float | None
    polygon_surface: list[tuple[float, float]] = Field(
        description="outline in surface coordinates: (along wall, height) for walls, (u, v) for floor / ceiling")
    confidence: float = Field(ge=0, le=1)
    views: int

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Flag(Strict):
    id: int
    rule_id: str
    damage_id: int
    surface_id: str
    severity: Literal["low", "medium", "high"]
    reason: str
    recommendation: str


class ScopeItem(Strict):
    id: int
    surface_id: str
    item: str
    quantity: Measure
    rule_id: str
    damage_id: int


class Result(Strict):
    schema_version: str = SCHEMA_VERSION
    capture: CaptureInfo
    processing: Processing
    property: PropertyPlan
    rooms: list[Room]
    openings: list[Opening]
    damage: list[Damage]
    flags: list[Flag]
    scope: list[ScopeItem]


def json_schema() -> dict:
    schema = Result.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Property Scan AI result"
    return schema
