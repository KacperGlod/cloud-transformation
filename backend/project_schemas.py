"""Versioned, bounded viewer state. No executable HTML, URLs or dynamic types."""
from datetime import datetime
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, field_validator, model_validator

from .schemas import CloudName, Vector


MAX_PROJECT_BYTES = 2 * 1024 * 1024
Unit = Annotated[FiniteFloat, Field(ge=0, le=1)]
Color = Annotated[list[Unit], Field(min_length=3, max_length=3)]
ColorAlpha = Annotated[list[Unit], Field(min_length=4, max_length=4)]
Pair = Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]
Label = Annotated[str, StringConstraints(max_length=500)]
Positive = Annotated[FiniteFloat, Field(gt=0)]
Points = Annotated[list[Vector], Field(max_length=10000)]


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CameraState(StateModel):
    position: Vector
    yaw: FiniteFloat
    pitch: Annotated[FiniteFloat, Field(ge=-1.5707963267948966, le=1.5707963267948966)]
    radius: Positive
    fov: Annotated[FiniteFloat, Field(gt=0, lt=180)] = 60
    projection: Literal[0, 1] = 1


class DisplayState(StateModel):
    pointBudget: Annotated[int, Field(ge=10000, le=10000000)] = 1000000
    edlEnabled: bool = True
    edlRadius: Annotated[FiniteFloat, Field(ge=0, le=100)] = 1.4
    edlStrength: Annotated[FiniteFloat, Field(ge=0, le=100)] = 0.4
    background: Literal["gradient", "black", "white", "skybox"] = "gradient"
    minNodeSize: Annotated[FiniteFloat, Field(ge=0, le=10000)] = 30
    showBoundingBoxes: bool = False
    clipTask: Literal[0, 1, 2, 3] = 1
    clipMethod: Literal[0, 1] = 0
    returnNumberRange: Pair = [0, 7]
    numberOfReturnsRange: Pair = [0, 7]
    pointSourceIDRange: Pair = [0, 65535]
    gpsTimeRange: Pair | None = None

    @field_validator("returnNumberRange", "numberOfReturnsRange", "pointSourceIDRange", "gpsTimeRange")
    @classmethod
    def ordered_filter(cls, value):
        if value is not None and value[0] > value[1]:
            raise ValueError("Początek filtra nie może przekraczać końca.")
        return value


class MaterialState(StateModel):
    activeAttributeName: Annotated[str, StringConstraints(min_length=1, max_length=128)] = "rgba"
    size: Annotated[FiniteFloat, Field(ge=0, le=1000)] = 1
    minSize: Annotated[FiniteFloat, Field(ge=0, le=1000)] = 2
    maxSize: Annotated[FiniteFloat, Field(gt=0, le=10000)] = 50
    pointSizeType: Literal[0, 1, 2] = 2
    shape: Literal[0, 1, 2] = 0
    opacity: Unit = 1
    color: Color = [1, 1, 1]
    elevationRange: Pair = [0, 1]
    intensityRange: Pair = [0, 65535]
    gradient: Annotated[str, StringConstraints(max_length=64)] = "SPECTRAL"
    rgbGamma: Annotated[FiniteFloat, Field(ge=0, le=10)] = 1
    rgbBrightness: Annotated[FiniteFloat, Field(ge=-1, le=1)] = 0
    rgbContrast: Annotated[FiniteFloat, Field(ge=-1, le=1)] = 0
    intensityGamma: Annotated[FiniteFloat, Field(ge=0, le=10)] = 1
    intensityBrightness: Annotated[FiniteFloat, Field(ge=-1, le=1)] = 0
    intensityContrast: Annotated[FiniteFloat, Field(ge=-1, le=1)] = 0

    @field_validator("elevationRange", "intensityRange")
    @classmethod
    def ordered_range(cls, value):
        if value[0] > value[1]:
            raise ValueError("Początek zakresu nie może przekraczać końca.")
        return value


class TransformState(StateModel):
    position: Vector
    rotation: Vector = [0, 0, 0]
    rotationOrder: Literal["XYZ", "YXZ", "ZXY", "ZYX", "YZX", "XZY"] = "XYZ"
    scale: Annotated[list[Positive], Field(min_length=3, max_length=3)] = [1, 1, 1]


class MeasurementState(StateModel):
    id: UUID
    name: Label = "Pomiar"
    points: Points
    color: Color = [1, 0, 0]
    visible: bool = True
    showDistances: bool = True
    showCoordinates: bool = False
    showArea: bool = False
    closed: bool = False
    showAngles: bool = False
    showHeight: bool = False
    showCircle: bool = False
    showAzimuth: bool = False
    showEdges: bool = True


class VolumeState(TransformState):
    id: UUID
    name: Label = "Obszar"
    type: Literal["BoxVolume", "SphereVolume"] = "BoxVolume"
    visible: bool = True
    clip: bool = False


class ProfileState(StateModel):
    id: UUID
    name: Label = "Przekrój"
    points: Points
    width: Positive = 1
    visible: bool = True


class AnnotationState(StateModel):
    id: UUID
    parentId: UUID | None = None
    title: Label = "Adnotacja"
    description: Annotated[str, StringConstraints(max_length=8000)] = ""
    position: Vector
    offset: Vector = [0, 0, 0]
    cameraPosition: Vector | None = None
    cameraTarget: Vector | None = None
    radius: Positive | None = None
    visible: bool = True


class ClassificationState(StateModel):
    name: Annotated[str, StringConstraints(max_length=120)]
    visible: bool
    color: ColorAlpha


class SceneState(StateModel):
    schemaVersion: Literal[1] = 1
    camera: CameraState
    settings: DisplayState = Field(default_factory=DisplayState)
    material: MaterialState = Field(default_factory=MaterialState)
    transform: TransformState | None = None
    classification: dict[str, ClassificationState] = Field(default_factory=dict, max_length=257)
    measurements: list[MeasurementState] = Field(default_factory=list, max_length=1000)
    volumes: list[VolumeState] = Field(default_factory=list, max_length=1000)
    profiles: list[ProfileState] = Field(default_factory=list, max_length=1000)
    annotations: list[AnnotationState] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def bounded_scene(self):
        for key in self.classification:
            if key != "DEFAULT" and not (key.isascii() and key.isdecimal() and 0 <= int(key) <= 255):
                raise ValueError("Nieprawidłowy identyfikator klasy punktów.")
        objects = [*self.measurements, *self.volumes, *self.profiles, *self.annotations]
        if len({item.id for item in objects}) != len(objects):
            raise ValueError("Identyfikatory obiektów projektu muszą być unikalne.")
        if sum(len(item.points) for item in [*self.measurements, *self.profiles]) > 50000:
            raise ValueError("Projekt może zawierać najwyżej 50 000 wierzchołków narzędzi.")
        parents = {item.id: item.parentId for item in self.annotations}
        for identifier in parents:
            current, visited = identifier, set()
            while current is not None:
                if current not in parents or current in visited or len(visited) >= 16:
                    raise ValueError("Nieprawidłowa hierarchia adnotacji (maksymalnie 16 poziomów).")
                visited.add(current)
                current = parents[current]
        serialized = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > MAX_PROJECT_BYTES:
            raise ValueError("Zapis projektu przekracza 2 MiB.")
        return self


class ProjectCreate(StateModel):
    name: CloudName
    cloudId: UUID
    sceneState: SceneState


class ProjectUpdate(StateModel):
    name: CloudName
    sceneState: SceneState
    revision: Annotated[int, Field(ge=1)]


class ProjectSummary(BaseModel):
    id: UUID
    name: str
    cloudId: UUID
    cloudName: str
    cloudAvailable: bool
    viewerUrl: str
    revision: int
    createdAt: datetime
    updatedAt: datetime


class ProjectRead(ProjectSummary):
    sceneState: SceneState


class ProjectList(BaseModel):
    projects: list[ProjectSummary]
