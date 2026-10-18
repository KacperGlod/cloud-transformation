from enum import StrEnum
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, field_validator


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


CloudName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Vector = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
Matrix = Annotated[list[FiniteFloat], Field(min_length=16, max_length=16)]


class Credentials(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
    password: Annotated[str, StringConstraints(min_length=10, max_length=256)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.lower()
        if value.count("@") != 1 or any(char.isspace() for char in value) or not all(value.split("@")):
            raise ValueError("Podaj prawidłowy adres e-mail.")
        return value


class CloudUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: CloudName | None = None
    visibility: Visibility | None = None


class UserRead(BaseModel):
    id: UUID
    email: str


class AccountRead(BaseModel):
    user: UserRead


class CloudRead(BaseModel):
    id: UUID
    name: str
    slug: str
    visibility: Visibility
    status: str
    url: str | None
    viewerUrl: str
    format: str
    sourceAvailable: bool
    sourceFile: str | None
    downloadName: str | None
    parentCloud: str | None
    operation: dict | None
    sizeBytes: int
    createdAt: datetime
    updatedAt: datetime
    canEdit: bool
    valid: bool
    validationError: str | None
    error: str | None


class CloudList(BaseModel):
    clouds: list[CloudRead]


class JobRead(BaseModel):
    id: UUID
    cloudId: UUID
    kind: str
    name: str
    slug: str
    filename: str | None
    status: str
    visibility: Visibility
    uploadProgress: int | None
    progress: int
    pointCloudUrl: str | None
    sourceCloud: str | None
    mode: str | None
    selection: dict | None
    transformation: dict | None
    transformationResult: dict | None
    removalSelections: list[dict] | None
    removalResult: dict | None
    error: str | None
    log: str
    createdAt: datetime
    updatedAt: datetime


class JobList(BaseModel):
    jobs: list[JobRead]


class Operation(BaseModel):
    name: CloudName
    sourceCloud: str
    visibility: Visibility = Visibility.PRIVATE


class ClipBox(BaseModel):
    name: str = "Clipping box"
    matrixWorld: Matrix
    position: Vector
    rotation: Vector
    scale: Vector
    pointCloudMatrixWorld: Matrix
    pointCloudOffset: Vector


class Segmentation(Operation):
    mode: Literal["inside", "outside"]
    sourceToBoxMatrix: Matrix
    clipBox: ClipBox


class Transformation(Operation):
    translation: Vector
    rotationDegrees: Vector
    scale: Vector
    pivotMode: Literal["center", "origin", "custom"] = "center"
    pivot: Vector = [0, 0, 0]

    @field_validator("scale")
    @classmethod
    def positive_scale(cls, value):
        if any(component <= 0 or component > 1_000_000_000 for component in value):
            raise ValueError("Skala musi być większa od zera i nie większa niż 1 000 000 000.")
        return value


class Sphere(BaseModel):
    position: Vector
    radius: Annotated[FiniteFloat, Field(gt=0, le=1_000_000_000)]


class PointRemoval(Operation):
    selections: Annotated[list[Sphere], Field(min_length=1, max_length=1000)]
