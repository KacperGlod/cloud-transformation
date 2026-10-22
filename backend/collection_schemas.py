from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .schemas import CloudName

CollectionDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
CollectionNotes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20000)]


class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: CloudName
    description: CollectionDescription = ""
    notes: CollectionNotes = ""
    cloudIds: Annotated[list[UUID], Field(max_length=1000)] = []


class CollectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: CloudName | None = None
    description: CollectionDescription | None = None
    notes: CollectionNotes | None = None

    @field_validator("name", "description", "notes")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Pole nie może mieć wartości null. Aby wyczyścić tekst, podaj pusty ciąg.")
        return value


class CollectionAddClouds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cloudIds: Annotated[list[UUID], Field(min_length=1, max_length=1000)]


class CollectionRead(BaseModel):
    id: UUID
    name: str
    description: str
    notes: str
    cloudIds: list[UUID]
    createdAt: datetime
    updatedAt: datetime


class CollectionList(BaseModel):
    collections: list[CollectionRead]


class CollectionSuggestion(BaseModel):
    cloudId: UUID
    relatedCloudId: UUID
    relatedCloudName: str
    distance: int


class CollectionSuggestions(BaseModel):
    suggestions: list[CollectionSuggestion]
